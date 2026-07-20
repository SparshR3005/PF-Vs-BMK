#!/usr/bin/env python3
"""
Strict discovery/backfill probe for the exact Nifty Composite Debt Index.

The script tests, in order:
  1. NSE Indices Total Return Index endpoint (routed and legacy .aspx paths)
  2. NSE Indices Historical Index Data endpoint (same path variants)
  3. NSE charting token search + daily historical data

It writes data/tri/NIFTY_COMPOSITE_DEBT.json only when:
  * the returned identity is exactly the unqualified Nifty Composite Debt Index;
  * PRC variants A-III/B-III/C-III are rejected;
  * the series has at least 200 valid observations;
  * the end date is fresh; and
  * the existing fetcher's sanity checks pass.

Run:
    xvfb-run -a python probe_composite_debt.py

Exit codes:
    0 exact series retrieved and written
    2 no exact public series found
    3 a candidate was returned but failed validation
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

import fetch_tri as ft

EXPECTED_NAME = "Nifty Composite Debt Index"
EXPECTED_CODE = "NSE_D_214"
EXPECTED_NORMALIZED = "NIFTYCOMPOSITEDEBTINDEX"
EXPECTED_ALIASES = {EXPECTED_NORMALIZED, "NIFTYCOMPOSITEDEBT"}
OUT_PATH = Path("data/tri/NIFTY_COMPOSITE_DEBT.json")
PROBE_DAYS = 180
REQUEST_ATTEMPTS = 3

NSE_ENDPOINTS = (
    ("TRI routed", "https://www.niftyindices.com/BackPage/getTotalReturnIndexString"),
    ("TRI legacy", "https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString"),
    ("Historical routed", "https://www.niftyindices.com/BackPage/getHistoricaldatatabletoString"),
    ("Historical legacy", "https://www.niftyindices.com/Backpage.aspx/getHistoricaldatatabletoString"),
)

# The first two are the normal contract. The family/code combinations are diagnostic
# probes for deployments where the selector's parent family or benchmark code is
# required internally. They are never trusted without returned-row identity checks.
PAYLOAD_PAIRS = (
    (EXPECTED_NAME, EXPECTED_NAME),
    (EXPECTED_NAME.upper(), EXPECTED_NAME.upper()),
    ("Nifty Composite Debt", "Nifty Composite Debt"),
    ("NIFTY Fixed Income Aggregate Indices", EXPECTED_NAME),
    ("NIFTY Fixed Income Aggregate Indices", "Nifty Composite Debt"),
    ("NIFTY Fixed Income Aggregate Indices", EXPECTED_CODE),
    (EXPECTED_CODE, EXPECTED_NAME),
    (EXPECTED_CODE, EXPECTED_CODE),
)

CHARTING_HOME = "https://charting.nseindia.com/"
CHARTING_SEARCH = "https://charting.nseindia.com/v1/exchanges/symbolsDynamic"
CHARTING_HISTORY = "https://charting.nseindia.com/v1/charts/symbolHistoricalData"
CHARTING_QUERIES = (EXPECTED_NAME, "NIFTY COMPOSITE DEBT", EXPECTED_CODE)

NAME_FIELDS = (
    "Index Name", "INDEX_NAME", "IndexName", "indexName", "index_name",
    "name", "symbol", "description", "fullname",
)
CODE_FIELDS = ("Benchmark Code", "BENCHMARK_CODE", "benchmarkCode", "code")


def normalize(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def exact_identity(rows: list[dict], *, require_name: bool = True) -> tuple[bool, str]:
    """Accept only the unqualified index; explicitly reject every PRC variant."""
    names: set[str] = set()
    codes: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for field in NAME_FIELDS:
            if row.get(field) not in (None, ""):
                names.add(normalize(row[field]))
        for field in CODE_FIELDS:
            if row.get(field) not in (None, ""):
                codes.add(str(row[field]).strip().upper())

    bad_names = sorted(
        n for n in names
        if n.startswith("NIFTYCOMPOSITEDEBTINDEX") and n != EXPECTED_NORMALIZED
    )
    if bad_names:
        return False, f"PRC/qualified variant returned: {bad_names}"
    if names and not names.issubset(EXPECTED_ALIASES):
        return False, f"unexpected returned index identity: {sorted(names)}"
    if require_name and not names:
        return False, "response has no index-name field; refusing an unverifiable series"
    if codes and codes != {EXPECTED_CODE}:
        return False, f"unexpected benchmark code(s): {sorted(codes)}"
    return True, "exact identity"


def build_pair_payload(name: str, index_name: str, start: date, end: date) -> str:
    cinfo = (
        "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}"
        % (name, start.strftime("%d-%b-%Y"), end.strftime("%d-%b-%Y"), index_name)
    )
    return json.dumps({"cinfo": cinfo})


def in_page_post(page, url: str, payload: str) -> dict:
    return page.evaluate(ft.JS_FETCH, [url, payload])


def prime_nifty(page) -> None:
    ft.prime(page, reload=False, url=ft.PAGE)


def probe_nse(page, context, end: date):
    start = end - timedelta(days=PROBE_DAYS)
    prime_nifty(page)
    for endpoint_label, endpoint in NSE_ENDPOINTS:
        for name, index_name in PAYLOAD_PAIRS:
            payload = build_pair_payload(name, index_name, start, end)
            for attempt in range(1, REQUEST_ATTEMPTS + 1):
                try:
                    if attempt > 1:
                        ft.prime(page, reload=True, url=ft.PAGE)
                    res = in_page_post(page, endpoint, payload)
                    rows = ft.parse_rows(res)
                    count = len(rows) if isinstance(rows, list) else -1
                    snippet = (res.get("text", "") or "")[:140].replace("\n", " ")
                    print(
                        "[NSE PROBE] endpoint=%r name=%r indexName=%r "
                        "status=%s redirected=%s rows=%s raw=%r"
                        % (endpoint_label, name, index_name, res.get("status"),
                           res.get("redirected"), count, snippet)
                    )
                    if not isinstance(rows, list) or len(rows) < 5:
                        break
                    ok, reason = exact_identity(rows)
                    print(f"             identity={ok}: {reason}")
                    if ok:
                        return endpoint_label, endpoint, name, index_name
                    break
                except (PWTimeout, Exception) as exc:
                    print(f"[NSE PROBE] attempt {attempt} failed: {exc}")
                    time.sleep(attempt * 2)
    return None


def fetch_nse_full(page, endpoint: str, name: str, index_name: str,
                   start: date, end: date) -> list[dict]:
    rows: list[dict] = []
    cur = start
    while cur <= end:
        chunk_end = min(end, cur + timedelta(days=365))
        payload = build_pair_payload(name, index_name, cur, chunk_end)
        got = None
        for attempt in range(1, REQUEST_ATTEMPTS + 1):
            try:
                if attempt > 1:
                    ft.prime(page, reload=True, url=ft.PAGE)
                res = in_page_post(page, endpoint, payload)
                got = ft.parse_rows(res)
                if isinstance(got, list):
                    break
            except Exception as exc:
                print(f"[NSE BACKFILL] {cur}..{chunk_end} attempt {attempt}: {exc}")
            time.sleep(attempt * 2)
        if not isinstance(got, list):
            raise RuntimeError(f"NSE backfill failed for {cur}..{chunk_end}")
        if got:
            ok, reason = exact_identity(got)
            if not ok:
                raise RuntimeError(f"identity failure for {cur}..{chunk_end}: {reason}")
            rows.extend(got)
        print(f"[NSE BACKFILL] {cur}..{chunk_end}: {len(got)} rows")
        cur = chunk_end + timedelta(days=1)
        time.sleep(0.4)
    return rows


def charting_exact_candidate(data: list[dict]) -> dict | None:
    exact: list[dict] = []
    for row in data:
        identities = {normalize(row.get(field)) for field in NAME_FIELDS if row.get(field)}
        if identities.intersection(EXPECTED_ALIASES):
            exact.append(row)
    if len(exact) != 1:
        print(f"[CHART SEARCH] exact candidate count={len(exact)}; refusing ambiguity")
        return None
    row = exact[0]
    all_identities = {normalize(row.get(field)) for field in NAME_FIELDS if row.get(field)}
    bad = [n for n in all_identities if n.startswith(EXPECTED_NORMALIZED) and n != EXPECTED_NORMALIZED]
    if bad:
        print(f"[CHART SEARCH] rejected qualified variant: {bad}")
        return None
    return row


def probe_charting(page, end: date) -> dict | None:
    try:
        page.goto(CHARTING_HOME, wait_until="domcontentloaded", timeout=ft.NAV_TIMEOUT_MS)
        page.wait_for_timeout(1000)
    except Exception as exc:
        print(f"[CHART SEARCH] unable to prime charting domain: {exc}")
        return None

    candidates: dict[str, dict] = {}
    for query in CHARTING_QUERIES:
        payload = json.dumps({"symbol": query, "segment": "IDX"})
        try:
            res = in_page_post(page, CHARTING_SEARCH, payload)
            rows = ft.parse_rows(res)
            print(
                "[CHART SEARCH] query=%r status=%s redirected=%s rows=%s raw=%r"
                % (query, res.get("status"), res.get("redirected"),
                   len(rows) if isinstance(rows, list) else -1,
                   (res.get("text", "") or "")[:180].replace("\n", " "))
            )
            if not isinstance(rows, list):
                continue
            candidate = charting_exact_candidate(rows)
            if candidate:
                token = str(candidate.get("scripcode") or candidate.get("token") or "")
                if token:
                    candidates[token] = candidate
        except Exception as exc:
            print(f"[CHART SEARCH] query {query!r} failed: {exc}")
    if len(candidates) != 1:
        print(f"[CHART SEARCH] exact token count={len(candidates)}; no usable exact token")
        return None
    candidate = next(iter(candidates.values()))
    print(f"[CHART SEARCH] selected exact candidate: {candidate}")
    return candidate


def chart_time_to_iso(raw: object) -> str:
    value = float(raw)
    if value > 10_000_000_000:
        value /= 1000.0
    return datetime.fromtimestamp(value, timezone.utc).astimezone(
        ZoneInfo("Asia/Kolkata")
    ).strftime("%Y-%m-%d")


def fetch_charting_full(page, candidate: dict, start: date, end: date) -> list[dict]:
    token = str(candidate.get("scripcode") or candidate.get("token"))
    symbol = str(candidate.get("symbol") or EXPECTED_NAME)
    symbol_type = str(candidate.get("type") or "Index")
    out: list[dict] = []
    cur = start
    while cur <= end:
        chunk_end = min(end, cur + timedelta(days=365))
        from_dt = datetime.combine(cur, datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata"))
        to_dt = datetime.combine(chunk_end, datetime.max.time(), tzinfo=ZoneInfo("Asia/Kolkata"))
        payload = json.dumps({
            "token": token,
            "fromDate": int(from_dt.timestamp()),
            "toDate": int(to_dt.timestamp()),
            "symbol": symbol,
            "symbolType": symbol_type,
            "chartType": "D",
            "timeInterval": 1,
        })
        got = None
        for attempt in range(1, REQUEST_ATTEMPTS + 1):
            try:
                res = in_page_post(page, CHARTING_HISTORY, payload)
                got = ft.parse_rows(res)
                if isinstance(got, list):
                    break
            except Exception as exc:
                print(f"[CHART BACKFILL] {cur}..{chunk_end} attempt {attempt}: {exc}")
            time.sleep(attempt * 2)
        if not isinstance(got, list):
            raise RuntimeError(f"charting backfill failed for {cur}..{chunk_end}")
        for row in got:
            try:
                out.append({
                    "Index Name": EXPECTED_NAME,
                    "Date": chart_time_to_iso(row["time"]),
                    "TotalReturnsIndex": float(row["close"]),
                })
            except (KeyError, TypeError, ValueError, OSError):
                continue
        print(f"[CHART BACKFILL] {cur}..{chunk_end}: {len(got)} raw rows")
        cur = chunk_end + timedelta(days=1)
        time.sleep(0.25)
    return out


def validate_and_write(rows: list[dict], source: str) -> int:
    if not rows:
        print("No rows to validate.")
        return 3
    ok, reason = exact_identity(rows)
    if not ok:
        print(f"Identity validation failed: {reason}")
        return 3
    doc = ft.rows_to_doc("NIFTY_COMPOSITE_DEBT", EXPECTED_NAME, rows, source)
    problem = ft.validate_series(doc, min_rows=ft.MIN_ROWS)
    if problem:
        print(f"Series validation failed: {problem}")
        return 3
    if not ft.is_fresh(doc):
        print(f"Series is stale: end={doc.get('end')}")
        return 3
    old = ft.read_existing_doc(OUT_PATH)
    continuity = ft.continuity_problem(doc, old)
    if continuity:
        print(f"Continuity validation failed: {continuity}")
        return 3
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ft.write_json_atomic(OUT_PATH, doc, pretty=True)
    print(
        "SUCCESS: exact Nifty Composite Debt Index written to %s\n"
        "rows=%s start=%s end=%s source=%s"
        % (OUT_PATH, doc["count"], doc["start"], doc["end"], doc["source"])
    )
    if doc["start"] and doc["start"] > "2002-01-01":
        print("WARNING: exact series was found, but public history starts materially after the 03-Sep-2001 base date.")
    return 0


def self_test() -> int:
    exact = [{"Index Name": EXPECTED_NAME, "Benchmark Code": EXPECTED_CODE}]
    assert exact_identity(exact)[0]
    for suffix in (" A-III", " B-III", " C-III"):
        assert not exact_identity([{"Index Name": EXPECTED_NAME + suffix}])[0]
    assert not exact_identity([{"Index Name": "Nifty Composite G-Sec Index"}])[0]
    assert chart_time_to_iso(1_700_000_000).count("-") == 2
    print("self-test passed")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    end = ft.today_ist()
    start = date(2001, 9, 3)
    with sync_playwright() as playwright:
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
        try:
            selected = probe_nse(page, context, end)
            if selected:
                label, endpoint, name, index_name = selected
                print(f"[SELECTED] {label}; beginning full backfill")
                rows = fetch_nse_full(page, endpoint, name, index_name, start, end)
                return validate_and_write(rows, endpoint)

            candidate = probe_charting(page, end)
            if candidate:
                rows = fetch_charting_full(page, candidate, start, end)
                return validate_and_write(rows, CHARTING_HISTORY)

            print(
                "NO EXACT PUBLIC SERIES FOUND. The tested NSE TRI, historical, and "
                "charting-token routes did not return a verifiable unqualified "
                "Nifty Composite Debt Index. Do not substitute PRC variants or proxies."
            )
            return 2
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())