#!/usr/bin/env python3
"""
Fetch Nifty TRI (Total Return Index) series from niftyindices.com and write
normalized JSON into data/tri/. The API call is issued from inside a real
(headful) Chromium page so it carries valid Akamai cookies + browser fingerprint.

Key detail: the live endpoint is the ROUTED path /BackPage/getTotalReturnIndexString
(no .aspx). The old /Backpage.aspx/... path now 302-redirects to a wall.
Responses come back as content-type text/html but the body is JSON -> parse the body
regardless of content-type.
"""
import json
import math
import re
import sys
import time
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PAGE = "https://www.niftyindices.com/reports/historical-data"
ENDPOINT = "https://www.niftyindices.com/BackPage/getTotalReturnIndexString"
# Composite/hybrid/debt indices are standalone TR series that are NOT served by the
# TRI endpoint above (it 200s with an EMPTY body for them — confirmed on the first
# run). They come through the historical index-values endpoint instead, which takes
# the identical cinfo payload and returns {"d": "[...]"} rows carrying CLOSE /
# HistoricalDate (parse_rows + the field-tolerant rows_to_doc already handle both).
# Use the ROUTED path (/BackPage/..., no .aspx): the old Backpage.aspx path walls.
ENDPOINT_HIST = "https://www.niftyindices.com/BackPage/getHistoricaldatatabletoString"
OUT_DIR = Path("data/tri")
START_DATE = "01-Jan-1999"          # DD-MMM-YYYY, endpoint format
MIN_ROWS = 200                       # a real series has thousands; guards against []/wall
MAX_STALE_DAYS = 7
PER_INDEX_ATTEMPTS = 4
NAV_TIMEOUT_MS = 45000

# --- composite (historical-endpoint) fetch tuning ---
# The historical index-values endpoint truncates large date ranges (a 27-year ask
# came back with 6 rows on the first run), so composites must be pulled in windows.
# jugaad-data proves calendar-month windows always work; larger windows are faster
# when the endpoint honours them. We therefore try a ~1-year window first and let it
# SELF-SPLIT (halving toward the monthly floor) whenever a window looks truncated —
# so the code adapts to the endpoint's real cap instead of hard-coding a guess, and
# the per-window row counts are logged so that cap is visible after a single run.
START_DATE_HIST      = date(2006, 1, 1)   # covers all NSE hybrid/ES inceptions with headroom
HIST_TOP_WINDOW_DAYS = 366                # first-try window per step; splits if truncated
HIST_MIN_WINDOW_DAYS = 32                 # never split below ~1 month (jugaad-data's floor)
HIST_GAP_TOLERANCE_DAYS = 10              # earliest row must reach within this of the window start
HIST_MAX_CALLS       = 400                # per-index backstop so a tiny cap can't blow the budget

# Soft completeness gate: the run fails (and commits nothing) only if one of these
# broad-market indices is missing/invalid — they are the fallbacks every equity
# holding relies on, so a bad core fetch must never overwrite good committed data.
# Sector indices (IT, Pharma, FMCG, ...) may fail without aborting the run: they
# keep their last-good file and are flagged stale in the manifest, which the UI
# surfaces via its per-benchmark staleness banner.
REQUIRED_KEYS = {
    "NIFTY500", "NIFTY100", "NIFTY_MIDCAP150", "NIFTY_SMALLCAP250",
    "NIFTY_LARGEMIDCAP250", "NIFTY_MULTICAP",
}

# Canonical index names. MUST match niftyindices' internal spelling exactly, or the
# endpoint returns [] (empty). Verify against:
# https://www.niftyindices.com/BenchmarkCodes/Nifty_Indices_Benchmark_Codes.pdf
#
# Each entry maps a FRONT-END KEY (the key index.html's resolver produces) to:
#   name : the exact canonical NSE name to send to the endpoint
#   file : the output filename index.html will fetch (data/tri/<file>)
#
# The `file` is normally slug(name), but is stated explicitly so the front-end
# and fetcher never drift. If any index logs "EMPTY result", its `name` spelling
# is wrong -- fix it against the benchmark-codes PDF above and re-run.
# `name` MUST be the exact canonical index name niftyindices' endpoint expects —
# it is sent verbatim as both `name` and `indexName`. A wrong name returns an
# EMPTY result (not an error), which fetch_index() reports as
# "likely wrong canonical name; skipping". Verify a new entry's first run rather
# than assuming the name is right.
INDEX_MAP = {
    # --- broad market ---
    "NIFTY50":              {"name": "NIFTY 50",                   "file": "NIFTY50.json"},
    "NIFTY100":             {"name": "NIFTY 100",                  "file": "NIFTY100.json"},
    "NIFTY200":             {"name": "NIFTY 200",                  "file": "NIFTY200.json"},
    "NIFTY500":             {"name": "NIFTY 500",                  "file": "NIFTY500.json"},
    "NIFTY_NEXT_50":        {"name": "NIFTY NEXT 50",              "file": "NIFTY_NEXT_50.json"},
    "NIFTY_TOTAL_MARKET":   {"name": "NIFTY TOTAL MARKET",         "file": "NIFTY_TOTAL_MARKET.json"},
    "NIFTY_MIDCAP150":      {"name": "NIFTY MIDCAP 150",           "file": "NIFTY_MIDCAP150.json"},
    "NIFTY_MIDCAP100":      {"name": "NIFTY MIDCAP 100",           "file": "NIFTY_MIDCAP100.json"},
    "NIFTY_SMALLCAP250":    {"name": "NIFTY SMALLCAP 250",         "file": "NIFTY_SMALLCAP250.json"},
    "NIFTY_SMALLCAP100":    {"name": "NIFTY SMALLCAP 100",         "file": "NIFTY_SMALLCAP100.json"},
    "NIFTY_MICROCAP250":    {"name": "NIFTY MICROCAP 250",         "file": "NIFTY_MICROCAP250.json"},
    "NIFTY_LARGEMIDCAP250": {"name": "NIFTY LARGEMIDCAP 250",      "file": "NIFTY_LARGEMIDCAP250.json"},
    "NIFTY_MIDSMALLCAP400": {"name": "NIFTY MIDSMALLCAP 400",      "file": "NIFTY_MIDSMALLCAP400.json"},
    "NIFTY_MULTICAP":       {"name": "NIFTY500 MULTICAP 50:25:25", "file": "NIFTY_MULTICAP.json"},
    # --- sectoral / thematic ---
    "NIFTY_FINSERV_OR_BANK":{"name": "NIFTY FINANCIAL SERVICES",   "file": "NIFTY_FINSERV_OR_BANK.json"},
    "NIFTY_BANK":           {"name": "NIFTY BANK",                 "file": "NIFTY_BANK.json"},
    "NIFTY_PRIVATE_BANK":   {"name": "NIFTY PRIVATE BANK",         "file": "NIFTY_PRIVATE_BANK.json"},
    "NIFTY_PSU_BANK":       {"name": "NIFTY PSU BANK",             "file": "NIFTY_PSU_BANK.json"},
    "NIFTY_IT":             {"name": "NIFTY IT",                   "file": "NIFTY_IT.json"},
    "NIFTY_PHARMA":         {"name": "NIFTY PHARMA",               "file": "NIFTY_PHARMA.json"},
    "NIFTY_HEALTHCARE":     {"name": "NIFTY HEALTHCARE",           "file": "NIFTY_HEALTHCARE.json"},
    "NIFTY_FMCG":           {"name": "NIFTY FMCG",                 "file": "NIFTY_FMCG.json"},
    "NIFTY_CONSUMPTION":    {"name": "NIFTY INDIA CONSUMPTION",    "file": "NIFTY_CONSUMPTION.json"},
    "NIFTY_CONSUMER_DUR":   {"name": "NIFTY CONSUMER DURABLES",    "file": "NIFTY_CONSUMER_DUR.json"},
    "NIFTY_INFRA":          {"name": "NIFTY INFRASTRUCTURE",       "file": "NIFTY_INFRA.json"},
    "NIFTY_AUTO":           {"name": "NIFTY AUTO",                 "file": "NIFTY_AUTO.json"},
    "NIFTY_ENERGY":         {"name": "NIFTY ENERGY",               "file": "NIFTY_ENERGY.json"},
    "NIFTY_OIL_GAS":        {"name": "NIFTY OIL & GAS",            "file": "NIFTY_OIL_GAS.json"},
    "NIFTY_METAL":          {"name": "NIFTY METAL",                "file": "NIFTY_METAL.json"},
    "NIFTY_REALTY":         {"name": "NIFTY REALTY",               "file": "NIFTY_REALTY.json"},
    "NIFTY_MEDIA":          {"name": "NIFTY MEDIA",                "file": "NIFTY_MEDIA.json"},
    "NIFTY_PSE":            {"name": "NIFTY PSE",                  "file": "NIFTY_PSE.json"},
    "NIFTY_CPSE":           {"name": "NIFTY CPSE",                 "file": "NIFTY_CPSE.json"},
    "NIFTY_COMMODITIES":    {"name": "NIFTY COMMODITIES",          "file": "NIFTY_COMMODITIES.json"},
    "NIFTY_MNC":            {"name": "NIFTY MNC",                  "file": "NIFTY_MNC.json"},
    "NIFTY_INDIA_DEFENCE":  {"name": "NIFTY INDIA DEFENCE",        "file": "NIFTY_INDIA_DEFENCE.json"},
    "NIFTY_INDIA_MFG":      {"name": "NIFTY INDIA MANUFACTURING",  "file": "NIFTY_INDIA_MFG.json"},
    "NIFTY_INDIA_DIGITAL":  {"name": "NIFTY INDIA DIGITAL",        "file": "NIFTY_INDIA_DIGITAL.json"},
    "NIFTY_TRANSPORT":      {"name": "NIFTY TRANSPORTATION & LOGISTICS", "file": "NIFTY_TRANSPORT.json"},
    # --- hybrid composite (Phase A) ---
    # ALREADY total-return composite indices (Nifty 50 TR blended with a fixed-income
    # index) used to benchmark aggressive/balanced/conservative hybrid, balanced-
    # advantage/dynamic-allocation and equity-savings funds. Diagnostics established:
    #   * the request wrapper is `cinfo` (a flat body 302-redirects to a wall);
    #   * niftyindices matches the index name in UPPERCASE — every working equity name
    #     in this map is uppercase, and the Title-Case spellings returned a clean [].
    # So names below are uppercase (colon ratios, no "Index" suffix), matching the
    # equity convention. fetch_composite() probes BOTH endpoints (the TRI endpoint
    # first — if it serves these, the whole series comes in one call like equity; else
    # the historical-values endpoint, chunked) and logs the raw response either way.
    # DELIBERATELY OPTIONAL (absent from REQUIRED_KEYS): a composite miss keeps its
    # last-good file and is flagged stale, and NEVER aborts the equity run.
    "NIFTY_HYBRID_65_35":   {"name": "NIFTY 50 HYBRID COMPOSITE DEBT 65:35",
                             "composite": True,
                             "names": ["NIFTY 50 HYBRID COMPOSITE DEBT 65:35",
                                       "NIFTY 50 HYBRID COMPOSITE DEBT 65:35 INDEX"],
                             "file": "NIFTY_HYBRID_65_35.json"},
    "NIFTY_HYBRID_50_50":   {"name": "NIFTY 50 HYBRID COMPOSITE DEBT 50:50",
                             "composite": True,
                             "names": ["NIFTY 50 HYBRID COMPOSITE DEBT 50:50",
                                       "NIFTY 50 HYBRID COMPOSITE DEBT 50:50 INDEX"],
                             "file": "NIFTY_HYBRID_50_50.json"},
    "NIFTY_HYBRID_15_85":   {"name": "NIFTY 50 HYBRID COMPOSITE DEBT 15:85",
                             "composite": True,
                             "names": ["NIFTY 50 HYBRID COMPOSITE DEBT 15:85",
                                       "NIFTY 50 HYBRID COMPOSITE DEBT 15:85 INDEX"],
                             "file": "NIFTY_HYBRID_15_85.json"},
    "NIFTY_EQ_SAVINGS":     {"name": "NIFTY EQUITY SAVINGS",
                             "composite": True,
                             "names": ["NIFTY EQUITY SAVINGS",
                                       "NIFTY EQUITY SAVINGS INDEX"],
                             "file": "NIFTY_EQ_SAVINGS.json"},
}

# In-page fetch: runs in the real renderer, inherits cookies + fingerprint.
# Body arrives as text/html but is JSON; caller parses the text directly.
JS_FETCH = r"""
async ([url, payload]) => {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 20000);   // 20s hard deadline per request
  try {
    const r = await fetch(url, {
      method: "POST", credentials: "include", redirect: "follow",
      signal: ctrl.signal,
      headers: {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
      },
      body: payload,
    });
    const text = await r.text();
    return { status: r.status, redirected: r.redirected, text: text };
  } catch (e) {
    return { status: -1, redirected: false, text: "FETCH_ERROR: " + String(e) };
  } finally {
    clearTimeout(t);
  }
}
"""


def slug(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def build_payload(name: str, start: str, end: str) -> str:
    cinfo = "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}" % (
        name, start, end, name,
    )
    return json.dumps({"cinfo": cinfo})


def parse_rows(res: dict):
    """Return list rows, [] for empty result, or None if wall/redirect/garbage."""
    if res.get("status") != 200 or res.get("redirected"):
        return None
    text = res.get("text", "") or ""
    t = text.lstrip("\ufeff \r\n\t")
    # Live endpoint returns a bare JSON array. Some deployments wrap in {"d":"..."}.
    try:
        if t.startswith("["):
            rows = json.loads(t)
        elif t.startswith("{"):
            outer = json.loads(t)
            d = outer.get("d")
            if isinstance(d, str):
                rows = json.loads(d)
            elif isinstance(d, list):
                rows = d
            else:
                rows = None
        else:
            return None
    except Exception:
        return None
    return rows if isinstance(rows, list) else None


def to_iso(value: str) -> str:
    text = str(value).strip()
    for date_format in ("%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, date_format).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {text!r}")


def prime(page, reload: bool):
    if reload:
        page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    else:
        page.goto(PAGE, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass
    for x, y in ((120, 160), (400, 300), (650, 480)):
        page.mouse.move(x, y)
        page.wait_for_timeout(120)
    page.wait_for_timeout(1500)


def has_akamai(context) -> bool:
    names = {c["name"] for c in context.cookies()}
    return "ak_bmsc" in names


def _fetch_one_name(page, context, name: str, end: str, endpoint: str = ENDPOINT):
    """Fetch a single canonical name. Returns (rows, "empty"|"fail").
    rows is a non-empty list on success; on failure rows is None and the second
    element is "empty" (endpoint answered [] -> wrong name, try the next candidate)
    or "fail" (walls/timeouts exhausted -> a later candidate is unlikely to help,
    but is still attempted so a transient wall doesn't strand an index)."""
    payload = build_payload(name, START_DATE, end)
    for attempt in range(1, PER_INDEX_ATTEMPTS + 1):
        # The whole attempt — navigation, cookie check and in-page fetch — runs inside
        # one guard. A PWTimeout from goto/reload/evaluate must degrade this index to
        # "failed" (last-good preserved, or fail-closed if required), never propagate
        # out of main() and abort every index still queued behind it. (#5)
        try:
            prime(page, reload=(attempt > 1))
            if not has_akamai(context):
                print("    [%s] attempt %d: no ak_bmsc yet" % (name, attempt))
                time.sleep(2 * attempt)
                continue
            res = page.evaluate(JS_FETCH, [endpoint, payload])
            rows = parse_rows(res)
            if rows is None:
                snippet = (res.get("text", "") or "")[:80].replace("\n", " ")
                print("    [%s] attempt %d: wall/redirect (status=%s redir=%s) %r"
                      % (name, attempt, res.get("status"), res.get("redirected"), snippet))
                time.sleep(2 * attempt)
                continue
            if len(rows) == 0:
                print("    [%s] EMPTY result -> likely wrong canonical name" % name)
                return None, "empty"
            if len(rows) < MIN_ROWS:
                print("    [%s] attempt %d: only %d rows (<%d), retrying"
                      % (name, attempt, len(rows), MIN_ROWS))
                time.sleep(2 * attempt)
                continue
            return rows, "ok"
        except PWTimeout as exc:
            print("    [%s] attempt %d: browser timeout: %s" % (name, attempt, exc))
            time.sleep(2 * attempt)
        except Exception as exc:
            print("    [%s] attempt %d: browser error: %s" % (name, attempt, exc))
            time.sleep(2 * attempt)
    print("    [%s] FAILED after %d attempts" % (name, PER_INDEX_ATTEMPTS))
    return None, "fail"


def fetch_index(page, context, names, end: str, endpoint: str = ENDPOINT):
    """Try each candidate spelling until one returns a usable series. Equity indices
    pass a single name (list-of-one) on the default TRI endpoint and behave exactly as
    before. Composite indices pass several spellings AND an alternate endpoint, so an
    unverified name/endpoint self-corrects on the first run instead of silently
    skipping the index."""
    if isinstance(names, str):
        names = [names]
    for name in names:
        rows, why = _fetch_one_name(page, context, name, end, endpoint)
        if rows:
            if name != names[0]:
                print("    [%s] matched via candidate spelling %r" % (names[0], name))
            return rows
        # "empty" -> the name was wrong; a different candidate may work.
        # "fail"  -> walls/timeouts; try the next candidate too, in case the
        #            primary spelling coincided with a bad window.
    return None


# ---------------------------------------------------------------------------
# Composite / hybrid indices: chunked fetch against the historical-values endpoint.
# That endpoint truncates large ranges, so we pull the series in windows and merge.
# The equity path above is untouched; only INDEX_MAP entries carrying an alternate
# `endpoint` come through here.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Composite / hybrid indices: chunked fetch against the historical-values endpoint.
# That endpoint truncates large ranges, so we pull the series in windows and merge.
# The equity path above is untouched; only INDEX_MAP entries carrying an alternate
# `endpoint` come through here.
#
# The endpoint's exact request contract is version-dependent in the wild (some
# deployments want the TRI endpoint's `cinfo`-wrapped string, others a flat body;
# the date token format also varies). Rather than hard-code one, a one-time probe
# tries a small matrix on a recent window, LOGS the raw response for each so a
# failure is fully diagnosable from the Actions log, and adopts whichever encoding
# actually returns rows. That encoding is then reused for the whole series.
# ---------------------------------------------------------------------------
def _ddmon(d: date) -> str:
    return d.strftime("%d-%b-%Y")


# Encoding is settled by diagnostics: the `cinfo`-wrapped body (same as the TRI
# endpoint) with DD-Mon-YYYY dates. A flat body 302-redirects to a wall.
COMPOSITE_ENDPOINTS = [ENDPOINT, ENDPOINT_HIST]   # try TRI (one-call) before HIST (chunked)


def _hist_window(page, context, endpoint, name, start_d, end_d, budget):
    """Fetch one [start_d, end_d] window with prime+retry, cinfo-encoded. Returns a rows
    list (possibly empty) or None on hard failure. Decrements `budget` per real request.
    Primes only when the Akamai cookie is missing or after a wall, so a long chunk loop
    reuses one session. Logs the wall/redirect case so it is never silently conflated
    with a genuine empty window."""
    for attempt in range(1, PER_INDEX_ATTEMPTS + 1):
        if budget[0] <= 0:
            return []
        try:
            if attempt > 1 or not has_akamai(context):
                prime(page, reload=(attempt > 1))
            if not has_akamai(context):
                time.sleep(2 * attempt)
                continue
            budget[0] -= 1
            payload = build_payload(name, _ddmon(start_d), _ddmon(end_d))
            res = page.evaluate(JS_FETCH, [endpoint, payload])
            rows = parse_rows(res)
            if rows is None:                    # wall/redirect -> log, re-prime and retry
                snippet = (res.get("text", "") or "")[:80].replace("\n", " ")
                print("    [%s] window %s..%s attempt %d: wall/redirect "
                      "(status=%s redir=%s) %r"
                      % (name, start_d, end_d, attempt, res.get("status"),
                         res.get("redirected"), snippet))
                time.sleep(2 * attempt)
                continue
            return rows                          # [] is a valid answer (empty window)
        except PWTimeout as exc:
            print("    [%s] window %s..%s timeout: %s" % (name, start_d, end_d, exc))
            time.sleep(2 * attempt)
        except Exception as exc:
            print("    [%s] window %s..%s error: %s" % (name, start_d, end_d, exc))
            time.sleep(2 * attempt)
    return None


def _probe_composite(page, context, names, end_d):
    """One-time resolver + diagnostic. Over a recent ~120-day window, try every
    (endpoint, name) pair with the cinfo encoding, logging the RAW response each time.
    Returns (endpoint, name) for the first pair that yields real rows, or None. Trying
    the TRI endpoint first means a composite that lives there is pulled in a single call
    (like equity); otherwise the historical-values endpoint is used with chunking."""
    probe_start = end_d - timedelta(days=120)
    for endpoint in COMPOSITE_ENDPOINTS:
        tag = endpoint.rsplit("/", 1)[-1]
        for name in names:
            try:
                if not has_akamai(context):
                    prime(page, reload=False)
                payload = build_payload(name, _ddmon(probe_start), _ddmon(end_d))
                res = page.evaluate(JS_FETCH, [endpoint, payload])
            except Exception as exc:
                print("    [PROBE] %s name=%r -> evaluate error: %s" % (tag, name, exc))
                continue
            text = (res.get("text", "") or "")
            rows = parse_rows(res)
            n = len(rows) if isinstance(rows, list) else -1
            print("    [PROBE] %s name=%r status=%s redir=%s textlen=%d parsed=%s raw=%r"
                  % (tag, name, res.get("status"), res.get("redirected"),
                     len(text), n, text[:180].replace("\n", " ")))
            if isinstance(rows, list) and len(rows) >= 5:
                print("    [PROBE] SELECTED endpoint=%s name=%r" % (tag, name))
                return endpoint, name
            time.sleep(1)
    return None


def _collect_hist(page, context, endpoint, name, start_d, end_d, budget, out):
    """Recursively pull [start_d, end_d], halving the window whenever the endpoint
    TRUNCATES it, down to the ~1-month floor. Truncation is detected by DATE, not row
    count: a capped response returns the most-recent rows, so its earliest date won't
    reach back to the window start. Robust to any cap size. An empty window is accepted
    as-is (pre-inception / real gap won't split into existence). Per-window diagnostics
    are logged so the endpoint's true cap is visible from one run."""
    if start_d > end_d or budget[0] <= 0:
        return
    rows = _hist_window(page, context, endpoint, name, start_d, end_d, budget)
    if rows is None:
        rows = []
    earliest = None
    for r in rows:
        try:
            dt = datetime.strptime(_row_date(r), "%Y-%m-%d").date()
            if earliest is None or dt < earliest:
                earliest = dt
        except Exception:
            continue
    span = (end_d - start_d).days + 1
    reached_back = earliest is not None and (earliest - start_d).days <= HIST_GAP_TOLERANCE_DAYS
    print("    [%s] %s..%s span=%dd rows=%d earliest=%s reached_back=%s"
          % (name, start_d, end_d, span, len(rows), earliest, reached_back))
    if not rows or span <= HIST_MIN_WINDOW_DAYS or reached_back:
        out.extend(rows)
        return
    mid = start_d + timedelta(days=span // 2)
    _collect_hist(page, context, endpoint, name, start_d, mid, budget, out)
    _collect_hist(page, context, endpoint, name, mid + timedelta(days=1), end_d, budget, out)


def fetch_composite(page, context, names, start_d, end_d):
    """Probe both endpoints for a working (endpoint, name), then fetch accordingly:
    the TRI endpoint returns the whole series in one call; the historical-values
    endpoint truncates, so it is pulled in self-splitting windows. Returns
    (rows, endpoint_used) or (None, None) if nothing the probe tried was recognised."""
    resolved = _probe_composite(page, context, names, end_d)
    if not resolved:
        return None, None
    endpoint, name = resolved
    if endpoint == ENDPOINT:
        # TRI endpoint: one call for the full range, exactly like the equity path.
        rows = fetch_index(page, context, [name], _ddmon(end_d), ENDPOINT)
        return rows, endpoint
    # Historical-values endpoint: chunked, truncation-aware.
    budget = [HIST_MAX_CALLS]
    out = []
    cur = start_d
    while cur <= end_d and budget[0] > 0:
        win_end = min(end_d, cur + timedelta(days=HIST_TOP_WINDOW_DAYS - 1))
        _collect_hist(page, context, endpoint, name, cur, win_end, budget, out)
        cur = win_end + timedelta(days=1)
    if budget[0] <= 0:
        print("    [%s] hit per-index call budget (%d); publishing what was collected"
              % (names[0], HIST_MAX_CALLS))
    return (out or None), endpoint


# The equity TRI endpoint returns rows keyed Date / TotalReturnsIndex. The hybrid
# composite indices carry a TotalReturnsIndex too, but if any variant is instead
# served with a plain closing-value field we still want to parse it rather than
# silently produce an empty series. Equity is unaffected: TotalReturnsIndex / Date
# are tried FIRST, so existing behaviour is byte-identical.
VALUE_FIELDS = ("TotalReturnsIndex", "totalReturnsIndex",
                "INDEX_VALUE", "CLOSE", "Close", "closingValue", "Closing Index Value")
DATE_FIELDS = ("Date", "HistoricalDate", "Index Date", "TIMESTAMP")


def _row_value(row):
    for field in VALUE_FIELDS:
        if field in row and row[field] not in (None, ""):
            return float(str(row[field]).replace(",", "").strip())
    raise KeyError("no known value field")


def _row_date(row):
    for field in DATE_FIELDS:
        if field in row and row[field]:
            return to_iso(row[field])
    raise KeyError("no known date field")


def rows_to_doc(key: str, name: str, rows: list, endpoint: str = ENDPOINT) -> dict:
    series = {}
    for row in rows:
        try:
            iso_date = _row_date(row)
            tri_value = _row_value(row)
            if math.isfinite(tri_value) and tri_value > 0:
                series[iso_date] = tri_value
        except (KeyError, TypeError, ValueError):
            continue

    ordered = dict(sorted(series.items()))
    dates = list(ordered)
    return {
        "key": key,
        "index": name,
        "source": "niftyindices.com " + endpoint.rsplit("/", 1)[-1],
        "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(ordered),
        "start": dates[0] if dates else None,
        "end": dates[-1] if dates else None,
        "series": ordered,
    }


# A TRI series is a slow-moving index level. A single-day move beyond this is
# almost certainly a data error (wrong index spliced in, decimal shift, or a
# provider glitch) rather than a real market move -- even 2020's worst crash day
# was ~13%. Publishing such a series would silently corrupt every XIRR computed
# against it, so we refuse it and keep the last-good file instead.
MAX_DAILY_MOVE = 0.35


def validate_series(doc: dict):
    """Return an error string if the series looks corrupt, else ''."""
    series = doc.get("series") or {}
    if len(series) < MIN_ROWS:
        return f"only {len(series)} valid rows (<{MIN_ROWS})"

    items = list(series.items())          # already date-sorted by rows_to_doc
    prev_date, prev_val = None, None
    for iso, val in items:
        if val <= 0:
            return f"non-positive value {val} on {iso}"
        if prev_val is not None:
            cur = datetime.strptime(iso, "%Y-%m-%d").date()
            gap = (cur - prev_date).days
            # Only police consecutive trading days; long gaps (holidays, and the
            # sparse early history of some indices) can legitimately move more.
            if gap <= 4:
                move = abs(val - prev_val) / prev_val
                if move > MAX_DAILY_MOVE:
                    return (f"implausible {move:.0%} move on {iso} "
                            f"({prev_val:.2f} -> {val:.2f}); refusing to publish")
            prev_date, prev_val = cur, val
        else:
            prev_date = datetime.strptime(iso, "%Y-%m-%d").date()
            prev_val = val
    return ""


def today_ist():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def is_fresh(doc: dict) -> bool:
    if not doc.get("end"):
        return False
    latest = datetime.strptime(doc["end"], "%Y-%m-%d").date()
    age_days = (today_ist() - latest).days
    return 0 <= age_days <= MAX_STALE_DAYS


def write_json_atomic(path: Path, payload: dict, *, pretty: bool = False) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(
        payload,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        allow_nan=False,
    )
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def read_existing_end(path: Path):
    """Return (count, start, end) of an already-committed TRI file, or (0, None, None)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc.get("count", 0), doc.get("start"), doc.get("end")
    except Exception:
        return 0, None, None


def read_existing_doc(path: Path):
    """Return the full committed TRI doc (incl. series), or None if absent/unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# A validated series can still be a DISASTER to publish: a smooth, fresh, but
# truncated 200-row window passes validate_series()/is_fresh() yet would overwrite
# a multi-year committed file and silently rewrite every historical XIRR. Before
# publishing we therefore diff each new series against the last-good file and
# refuse anything that loses early history, materially shrinks, or disagrees with
# committed historical values (the fingerprint of a wrong index being spliced in).
CONT_MIN_KEEP_FRACTION = 0.98   # new count must be >= 98% of the committed count
CONT_VALUE_TOLERANCE   = 0.01   # committed historical points must match within 1%


def continuity_problem(new_doc: dict, old_doc) -> str:
    """Return a reason string if the new series breaks continuity, else ''."""
    if not old_doc:
        return ""                                   # first run: nothing to protect
    old_series = old_doc.get("series") or {}
    new_series = new_doc.get("series") or {}
    old_count, new_count = len(old_series), len(new_series)
    old_start, new_start = old_doc.get("start"), new_doc.get("start")
    old_end, new_end = old_doc.get("end"), new_doc.get("end")

    # ISO dates sort chronologically as plain strings, so a later start = lost history.
    if old_start and new_start and new_start > old_start:
        return (f"new start {new_start} is later than committed start {old_start} "
                f"— earlier history would be dropped")
    # ...and an earlier END = lost RECENT history. A fresh-but-truncated tail passes
    # is_fresh() (MAX_STALE_DAYS bounds it to ~7 days) and can retain >98% of rows,
    # so neither the freshness gate nor the row-count gate catches this on its own.
    if old_end and new_end and new_end < old_end:
        return (f"new end {new_end} is earlier than committed end {old_end} "
                f"— recent history would be dropped")
    if old_count and new_count < old_count * CONT_MIN_KEEP_FRACTION:
        return (f"row count shrank {old_count} -> {new_count} "
                f"(<{CONT_MIN_KEEP_FRACTION:.0%} retained)")

    # Overlapping historical points are fixed once published; large drift means a
    # different index (or a decimal shift) was returned under the same name.
    common = [d for d in old_series if d in new_series]
    if len(common) >= 20:
        step = max(1, len(common) // 40)            # sample up to ~40 points
        for d in common[::step]:
            ov, nv = old_series[d], new_series[d]
            if ov and abs(nv - ov) / ov > CONT_VALUE_TOLERANCE:
                return (f"committed value on {d} changed {ov:.2f} -> {nv:.2f} "
                        f"(>{CONT_VALUE_TOLERANCE:.0%}) — likely a different series")
        if old_end and old_end in new_series and old_series.get(old_end):
            ov, nv = old_series[old_end], new_series[old_end]
            if abs(nv - ov) / ov > CONT_VALUE_TOLERANCE:
                return (f"value on prior end {old_end} changed {ov:.2f} -> {nv:.2f} "
                        f"(>{CONT_VALUE_TOLERANCE:.0%}) — likely a different series")
    return ""


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = today_ist().strftime("%d-%b-%Y")
    failures = []          # human-readable reason per index that didn't pass
    staged = {}            # key -> (filename, doc) validated THIS run

    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = playwright.chromium.launch(
                headless=False,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                viewport={"width": 1366, "height": 900},
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.new_page()
            # Best-effort warm-up only: fetch_index() re-primes on every attempt, so a
            # timeout here must not abort the run before a single index is tried. (#5)
            try:
                prime(page, reload=False)
            except PWTimeout as exc:
                print("    initial prime timed out (%s) — continuing; "
                      "each index re-primes on its own attempts" % exc)
            except Exception as exc:
                print("    initial prime failed (%s) — continuing" % exc)

            items = list(INDEX_MAP.items())
            for index, (key, meta) in enumerate(items, start=1):
                name = meta["name"]
                filename = meta["file"]
                names = meta.get("names") or [name]        # composites carry alt spellings
                print(f"[{index}/{len(items)}] {name} ({key})")
                if meta.get("composite"):
                    # Probe both endpoints; fetch full (TRI) or chunked (historical).
                    rows, used_endpoint = fetch_composite(page, context, names,
                                                          START_DATE_HIST, today_ist())
                else:
                    rows = fetch_index(page, context, names, end, ENDPOINT)
                    used_endpoint = ENDPOINT
                if not rows:
                    failures.append(f"{name}: fetch failed")
                    continue

                doc = rows_to_doc(key, name, rows, used_endpoint)
                problem = validate_series(doc)
                if problem:
                    failures.append(f"{name}: {problem}")
                    continue

                if not is_fresh(doc):
                    failures.append(f"{name}: stale end date {doc['end']}")
                    continue

                # Continuity gate: never let a validated-but-truncated/mismatched
                # series overwrite good committed history (see continuity_problem).
                cont = continuity_problem(doc, read_existing_doc(OUT_DIR / filename))
                if cont:
                    failures.append(f"{name}: continuity check failed — {cont}")
                    continue

                print(
                    "    -> rows=%d  start=%s  end=%s  fresh=True"
                    % (doc["count"], doc["start"], doc["end"])
                )
                staged[key] = (filename, doc)
                time.sleep(1.5)
        finally:
            if context is not None:
                context.close()
            if browser is not None:
                browser.close()

    # ---- SOFT COMPLETENESS GATE ----
    # Fail closed (commit nothing, preserve the last-good dataset) only when a
    # required broad-market index is missing/invalid, or nothing was fetched.
    required_missing = sorted(k for k in REQUIRED_KEYS if k not in staged)
    if required_missing or not staged:
        print("\nERROR: required benchmark(s) missing/invalid — nothing was updated.")
        for k in required_missing:
            print(f"  - required index unavailable: {k}")
        for failure in failures:
            print(f"  - {failure}")
        sys.exit(1)

    # Publish the fresh docs atomically; leave failed optional indices as last-good.
    for key, (filename, doc) in staged.items():
        write_json_atomic(OUT_DIR / filename, doc)

    # Manifest lists every configured index: staged -> fresh:true;
    # optional-that-failed -> fresh:false with its last-good end date (or null),
    # so the UI banner flags exactly which benchmarks are out of date.
    manifest = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "indices": [],
    }
    for key, meta in INDEX_MAP.items():
        filename = meta["file"]
        name = meta["name"]
        if key in staged:
            doc = staged[key][1]
            manifest["indices"].append({
                "key": key, "index": name, "file": filename,
                "count": doc["count"], "start": doc["start"], "end": doc["end"],
                "fresh": True,
            })
        else:
            count, start, prev_end = read_existing_end(OUT_DIR / filename)
            manifest["indices"].append({
                "key": key, "index": name, "file": filename,
                "count": count, "start": start, "end": prev_end,
                "fresh": False,
            })
    write_json_atomic(OUT_DIR / "index.json", manifest, pretty=True)

    published = len(staged)
    skipped = len(INDEX_MAP) - published
    print(f"\nWrote {published} fresh TRI file(s); "
          f"{skipped} optional index(es) kept last-good and flagged stale.")
    if failures:
        print("Non-fatal failures this run:")
        for failure in failures:
            print(f"  - {failure}")


if __name__ == "__main__":
    main()
