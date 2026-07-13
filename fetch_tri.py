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
import re
import sys
import time
from datetime import datetime, timezone, date
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PAGE = "https://www.niftyindices.com/reports/historical-data"
ENDPOINT = "https://www.niftyindices.com/BackPage/getTotalReturnIndexString"
OUT_DIR = Path("data/tri")
START_DATE = "01-Jan-1999"          # DD-MMM-YYYY, endpoint format
MIN_ROWS = 200                       # a real series has thousands; guards against []/wall
MAX_STALE_DAYS = 14
PER_INDEX_ATTEMPTS = 4
NAV_TIMEOUT_MS = 45000

# Canonical index names. MUST match niftyindices' internal spelling exactly, or the
# endpoint returns [] (empty). Verify against:
# https://www.niftyindices.com/BenchmarkCodes/Nifty_Indices_Benchmark_Codes.pdf
INDICES = [
    "NIFTY 50",
    "NIFTY 500",
    "NIFTY NEXT 50",
    "NIFTY MIDCAP 150",
    "NIFTY SMALLCAP 250",
    "NIFTY FMCG",
    "NIFTY FINANCIAL SERVICES",
]

# In-page fetch: runs in the real renderer, inherits cookies + fingerprint.
# Body arrives as text/html but is JSON; caller parses the text directly.
JS_FETCH = r"""
async ([url, payload]) => {
  try {
    const r = await fetch(url, {
      method: "POST", credentials: "include", redirect: "follow",
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
            rows = json.loads(d) if isinstance(d, str) else None
        else:
            return None
    except Exception:
        return None
    return rows if isinstance(rows, list) else None


def to_iso(d: str) -> str:
    return datetime.strptime(d.strip(), "%d %b %Y").strftime("%Y-%m-%d")


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


def fetch_index(page, context, name: str, end: str):
    payload = build_payload(name, START_DATE, end)
    for attempt in range(1, PER_INDEX_ATTEMPTS + 1):
        prime(page, reload=(attempt > 1))
        if not has_akamai(context):
            print("    [%s] attempt %d: no ak_bmsc yet" % (name, attempt))
            time.sleep(2 * attempt)
            continue
        res = page.evaluate(JS_FETCH, [ENDPOINT, payload])
        rows = parse_rows(res)
        if rows is None:
            snippet = (res.get("text", "") or "")[:80].replace("\n", " ")
            print("    [%s] attempt %d: wall/redirect (status=%s redir=%s) %r"
                  % (name, attempt, res.get("status"), res.get("redirected"), snippet))
            time.sleep(2 * attempt)
            continue
        if len(rows) == 0:
            print("    [%s] EMPTY result -> likely wrong canonical name; skipping" % name)
            return None
        if len(rows) < MIN_ROWS:
            print("    [%s] attempt %d: only %d rows (<%d), retrying"
                  % (name, attempt, len(rows), MIN_ROWS))
            time.sleep(2 * attempt)
            continue
        return rows
    print("    [%s] FAILED after %d attempts" % (name, PER_INDEX_ATTEMPTS))
    return None


def rows_to_doc(name: str, rows: list) -> dict:
    series = {}
    for r in rows:
        try:
            series[to_iso(r["Date"])] = float(r["TotalReturnsIndex"])
        except (KeyError, ValueError):
            continue
    ordered = dict(sorted(series.items()))
    keys = list(ordered.keys())
    return {
        "index": name,
        "source": "niftyindices.com getTotalReturnIndexString",
        "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(ordered),
        "start": keys[0] if keys else None,
        "end": keys[-1] if keys else None,
        "series": ordered,
    }


def is_fresh(doc: dict) -> bool:
    if not doc.get("end"):
        return False
    latest = datetime.strptime(doc["end"], "%Y-%m-%d").date()
    return (date.today() - latest).days <= MAX_STALE_DAYS


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = date.today().strftime("%d-%b-%Y")
    manifest = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "indices": []}
    failures = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            locale="en-IN", timezone_id="Asia/Kolkata",
            viewport={"width": 1366, "height": 900},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()
        prime(page, reload=False)

        for i, name in enumerate(INDICES):
            print("[%d/%d] %s" % (i + 1, len(INDICES), name))
            rows = fetch_index(page, context, name, end)
            if not rows:
                failures.append(name)
                continue
            doc = rows_to_doc(name, rows)
            fresh = is_fresh(doc)
            fpath = OUT_DIR / ("%s.json" % slug(name))
            fpath.write_text(json.dumps(doc, separators=(",", ":")))
            print("    -> %s  rows=%d  end=%s  fresh=%s"
                  % (fpath, doc["count"], doc["end"], fresh))
            manifest["indices"].append({
                "index": name, "file": "%s.json" % slug(name),
                "count": doc["count"], "end": doc["end"], "fresh": fresh,
            })
            if not fresh:
                failures.append("%s(stale:%s)" % (name, doc["end"]))
            time.sleep(1.5)

        context.close()
        browser.close()

    (OUT_DIR / "index.json").write_text(json.dumps(manifest, indent=2))
    print("\nWrote manifest with %d indices; failures: %s"
          % (len(manifest["indices"]), failures or "none"))

    if len(manifest["indices"]) == 0:
        print("ERROR: no indices fetched successfully.")
        sys.exit(1)


if __name__ == "__main__":
    main()
