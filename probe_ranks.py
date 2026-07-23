#!/usr/bin/env python3
"""
probe_ranks.py -- measure the mfapi universe BEFORE building the ranking fetcher.

WHY THIS EXISTS
---------------
The Insights feature needs a nightly job that pulls NAV history for every equity
fund so it can rank a holding against its category peers. How that job must be
built depends entirely on one number nobody currently knows: how many schemes
survive the filters, and how long mfapi takes to serve them.

  ~600 funds  -> a simple nightly full refresh is fine.
  ~4000 funds -> the fetcher MUST be incremental, resumable, and probably split
                 across runs, or it will exceed the Actions timeout and commit
                 nothing, every night, silently.

Guessing wrong means debugging a failing Action from the GitHub web editor with
no local environment. So: measure first, build second.

This script is READ-ONLY. It writes nothing into data/, commits nothing, and
mutates no repo state. It only reads mfapi and prints what it found.

WHAT IT MEASURES
----------------
  Stage 1 (one request)  -- the filter funnel: how many schemes exist, and how
                            many survive each filter, ported verbatim from
                            index.html's loadSchemeList().
  Stage 2 (sampled)      -- per-scheme detail cost: latency, payload size, NAV
                            history depth, and the SEBI category distribution,
                            extrapolated to the full universe.

The filters are a faithful port of the client. They are cross-checked against
index.html by tests/test_probe_ranks.py, which fails if the two ever drift.

USAGE
  python probe_ranks.py                 # default: 60-scheme sample
  python probe_ranks.py --sample 150    # bigger sample, tighter extrapolation
  python probe_ranks.py --full          # fetch EVERY scheme (exact counts; slow)
  python probe_ranks.py --concurrency 8 # tune politeness/speed

Stdlib only -- deliberately no new entry in requirements.txt.
"""

import argparse
import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.mfapi.in"
UA = "PF-Vs-BMK-probe/1.0 (+https://github.com/SparshR3005/PF-Vs-BMK)"

# ---------------------------------------------------------------- ported filters
# Every constant below is copied from index.html. Do not "improve" them here in
# isolation -- the client and this script must agree, or the universe measured
# will not be the universe the UI can actually rank against.
# tests/test_probe_ranks.py re-parses index.html and fails on any drift.

NON_EQUITY_NAME_TOKENS = [
    "liquid", "overnight", "gilt", "money market", "ultra short", "low duration",
    "short duration", "medium duration", "long duration", "banking and psu",
    "corporate bond", "credit risk", "debt", "duration", "floater", "dynamic bond", "bond",
    "hybrid", "balanced", "arbitrage", "equity savings", "multi asset", "asset allocation",
    "gold", "silver", "commodit", "fund of fund", "fof", "overseas", "international", "global",
    "index fund", "exchange traded", "etf", "retirement", "children", "pension",
]

# Growth-option gate. IDCW/payout NAV is reduced at each distribution and mfapi
# does not add the dividend back, so those variants show structurally understated
# returns -- including them would fill the bottom of every ranking with a
# measurement artifact rather than genuine underperformance.
INCOME_TOKENS = ["idcw", "dividend", "payout", "reinvest", "bonus"]

CATEGORY_CANON = {
    "equity scheme - large cap fund":         "LARGE_CAP",
    "equity scheme - large & mid cap fund":   "LARGE_MID",
    "equity scheme - large and mid cap fund": "LARGE_MID",
    "equity scheme - mid cap fund":           "MID_CAP",
    "equity scheme - small cap fund":         "SMALL_CAP",
    "equity scheme - multi cap fund":         "MULTI_CAP",
    "equity scheme - flexi cap fund":         "FLEXI_CAP",
    "equity scheme - focused fund":           "FOCUSED",
    "equity scheme - value fund":             "VALUE",
    "equity scheme - contra fund":            "CONTRA",
    "equity scheme - dividend yield fund":    "DIV_YIELD",
    "equity scheme - sectoral/ thematic":     "SECTORAL",
    "equity scheme - sectoral/thematic":      "SECTORAL",
    "equity scheme - elss":                   "ELSS",
    "elss":                                   "ELSS",
}

# Decided in design review: Sectoral/Thematic is NOT rankable. mfapi collapses
# every sector into one category string and never says which, so a flat ranking
# would compare an IT fund against a Pharma fund. Counted here so we know the
# size of what we are deliberately forgoing.
UNRANKABLE_KEYS = {"SECTORAL"}


def norm_name(s):
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def norm_category(c):
    return re.sub(r"\s+", " ", str(c or "").lower()).strip()


def category_key(category):
    """Canonical SEBI key, or None when absent/junk/non-equity. Fails closed."""
    return CATEGORY_CANON.get(norm_category(category))


def name_looks_non_equity(n):
    return any(t in n for t in NON_EQUITY_NAME_TOKENS)


def classify_plan(n):
    """Direct when the name says so; otherwise Regular (mirrors the client's
    else-branch, which treats a name mentioning neither as Regular)."""
    if "direct" in n:
        return "Direct"
    return "Regular"


# ---------------------------------------------------------------- http
def get_json(url, timeout, attempts=3):
    """GET with retry/backoff. Returns (payload, elapsed_s, nbytes, status)."""
    last = None
    for i in range(attempts):
        started = time.monotonic()
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urlopen(req, timeout=timeout) as res:
                raw = res.read()
                elapsed = time.monotonic() - started
                # mfapi has been observed serving JSON under a text/html content
                # type, so never gate parsing on the content-type header.
                return json.loads(raw.decode("utf-8", "replace")), elapsed, len(raw), res.status
        except HTTPError as e:
            last = e
            # 429/5xx are worth backing off on; 404 is final.
            if e.code == 404:
                return None, time.monotonic() - started, 0, 404
            if e.code == 429:
                time.sleep(2.0 * (i + 1))
                continue
        except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
            last = e
        time.sleep(0.6 * (i + 1))
    return None, 0.0, 0, getattr(last, "code", 0)


def unwrap_list(raw):
    """Tolerate a shape change exactly as the client does: today /mf returns a
    bare array, but guard against a future paginated wrapper so the whole list
    doesn't silently vanish."""
    if isinstance(raw, list):
        return raw, True
    if isinstance(raw, dict):
        for k in ("data", "schemes"):
            if isinstance(raw.get(k), list):
                paginated = (
                    raw.get("nextPage") is not None
                    or raw.get("next") is not None
                    or raw.get("hasMore") is True
                    or (raw.get("page") is not None
                        and raw.get("totalPages") is not None
                        and raw["page"] < raw["totalPages"])
                )
                return raw[k], not paginated
    return None, False


# ---------------------------------------------------------------- stage 1
def stage1(timeout):
    print("STAGE 1 -- scheme list and name filters")
    print("-" * 72)
    raw, elapsed, nbytes, status = get_json(f"{API}/mf", timeout)
    if raw is None:
        print(f"  FAILED to fetch {API}/mf (status {status})")
        return None
    all_schemes, complete = unwrap_list(raw)
    if all_schemes is None:
        print("  FAILED: unexpected scheme-list response shape")
        return None

    print(f"  fetched /mf in {elapsed:.2f}s, {nbytes/1024/1024:.2f} MB, "
          f"{len(all_schemes)} entries, complete={complete}")
    if not complete:
        print("  WARNING: list looks paginated -- the fetcher will need pagination.")

    has_isin_field = any(isinstance(s, dict) and "isinGrowth" in s for s in all_schemes)
    print(f"  isinGrowth field present in response: {has_isin_field}")

    funnel = {
        "total": len(all_schemes),
        "malformed": 0,
        "not_growth": 0,
        "income_option": 0,
        "blank_isin": 0,
        "non_equity_name": 0,
    }
    survivors = []
    for s in all_schemes:
        if not isinstance(s, dict) or not s.get("schemeName") or not s.get("schemeCode"):
            funnel["malformed"] += 1
            continue
        n = str(s["schemeName"]).lower()
        if "growth" not in n:
            funnel["not_growth"] += 1
            continue
        if any(t in n for t in INCOME_TOKENS):
            funnel["income_option"] += 1
            continue
        if has_isin_field and not str(s.get("isinGrowth") or "").strip():
            funnel["blank_isin"] += 1
            continue
        if name_looks_non_equity(n):
            funnel["non_equity_name"] += 1
            continue
        survivors.append({
            "schemeCode": s["schemeCode"],
            "schemeName": s["schemeName"],
            "plan": classify_plan(n),
        })

    print()
    print("  filter funnel")
    print(f"    {funnel['total']:>6}  schemes returned by /mf")
    print(f"    {-funnel['malformed']:>6}  malformed (missing name/code)")
    print(f"    {-funnel['not_growth']:>6}  not a Growth option")
    print(f"    {-funnel['income_option']:>6}  IDCW / dividend / payout / reinvest / bonus")
    print(f"    {-funnel['blank_isin']:>6}  blank isinGrowth (legacy/closed record)")
    print(f"    {-funnel['non_equity_name']:>6}  name looks non-equity (debt/hybrid/index/FoF)")
    print(f"    {'='*6}")
    print(f"    {len(survivors):>6}  candidates needing a category lookup")

    direct = sum(1 for s in survivors if s["plan"] == "Direct")
    print(f"           of which Direct {direct}, Regular {len(survivors)-direct}")
    return {"survivors": survivors, "funnel": funnel, "complete": complete,
            "has_isin_field": has_isin_field}


# ---------------------------------------------------------------- stage 2
def fetch_detail(code, timeout):
    payload, elapsed, nbytes, status = get_json(f"{API}/mf/{code}", timeout)
    if payload is None:
        return {"code": code, "ok": False, "status": status, "elapsed": elapsed}
    meta = payload.get("meta") or {}
    data = payload.get("data") or []
    dates = []
    for row in data[:1] + data[-1:]:
        d = str(row.get("date") or "")
        if d:
            dates.append(d)
    return {
        "code": code,
        "ok": True,
        "status": status,
        "elapsed": elapsed,
        "bytes": nbytes,
        "category": meta.get("scheme_category"),
        "cat_key": category_key(meta.get("scheme_category")),
        "nav_points": len(data),
        "newest": dates[0] if dates else None,
        "oldest": dates[-1] if len(dates) > 1 else None,
    }


def stage2(survivors, sample_n, concurrency, timeout, full):
    targets = survivors if full else survivors[::max(1, len(survivors) // max(1, sample_n))][:sample_n]
    label = "ALL" if full else f"sample of {len(targets)}"
    print()
    print(f"STAGE 2 -- per-scheme detail ({label}, concurrency {concurrency})")
    print("-" * 72)

    started = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = {pool.submit(fetch_detail, t["schemeCode"], timeout): t for t in targets}
        done = 0
        for fut in as_completed(futs):
            results.append(fut.result())
            done += 1
            if done % 25 == 0 or done == len(targets):
                print(f"    {done}/{len(targets)} fetched ({time.monotonic()-started:.1f}s elapsed)")
    wall = time.monotonic() - started

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    if not ok:
        print("  FAILED: no successful detail fetches. Check connectivity/rate limits.")
        return None

    lat = sorted(r["elapsed"] for r in ok)
    navs = sorted(r["nav_points"] for r in ok)
    print()
    print(f"  ok {len(ok)}, failed {len(bad)}")
    if bad:
        codes = {}
        for r in bad:
            codes[r["status"]] = codes.get(r["status"], 0) + 1
        print(f"  failure status codes: {codes}"
              + ("   <-- 429 means mfapi IS rate limiting; lower --concurrency"
                 if 429 in codes else ""))
    print(f"  latency  median {statistics.median(lat):.2f}s   p90 {lat[int(len(lat)*0.9)-1]:.2f}s   max {lat[-1]:.2f}s")
    print(f"  payload  median {statistics.median(r['bytes'] for r in ok)/1024:.0f} KB")
    print(f"  NAV pts  median {statistics.median(navs):.0f}   min {navs[0]}   max {navs[-1]}")
    print(f"  wall time for {len(targets)} schemes at concurrency {concurrency}: {wall:.1f}s")

    # category distribution
    dist = {}
    unsupported = 0
    for r in ok:
        k = r["cat_key"]
        if k is None:
            unsupported += 1
        else:
            dist[k] = dist.get(k, 0) + 1
    print()
    print("  SEBI category distribution in this batch")
    for k in sorted(dist, key=lambda x: -dist[x]):
        flag = "   (EXCLUDED from ranking)" if k in UNRANKABLE_KEYS else ""
        print(f"    {k:<12} {dist[k]:>5}{flag}")
    print(f"    {'(rejected)':<12} {unsupported:>5}   category junk/non-equity -- fails closed")

    scale = len(survivors) / len(ok) if not full else 1.0
    rankable_sample = sum(v for k, v in dist.items() if k not in UNRANKABLE_KEYS)
    est_rankable = rankable_sample * scale

    print()
    print("  EXTRAPOLATION" if not full else "  EXACT COUNTS")
    print(f"    estimated rankable equity funds (excl. Sectoral): {est_rankable:,.0f}")
    for conc in (4, 8, 12):
        per = statistics.median(lat)
        est_s = est_rankable * per / conc
        print(f"    est. nightly fetch at concurrency {conc:>2}: {est_s/60:>6.1f} min")
    return {"dist": dist, "unsupported": unsupported, "median_latency": statistics.median(lat),
            "median_nav_points": statistics.median(navs), "est_rankable": est_rankable,
            "sampled": len(ok), "failed": len(bad), "wall": wall}


def main():
    ap = argparse.ArgumentParser(description="Measure the mfapi universe for the ranking fetcher.")
    ap.add_argument("--sample", type=int, default=60, help="schemes to sample in stage 2 (default 60)")
    ap.add_argument("--concurrency", type=int, default=6, help="parallel detail fetches (default 6)")
    ap.add_argument("--timeout", type=float, default=30.0, help="per-request timeout seconds")
    ap.add_argument("--full", action="store_true", help="fetch EVERY candidate for exact counts (slow)")
    args = ap.parse_args()

    print("=" * 72)
    print("mfapi universe probe -- READ ONLY, commits nothing")
    print(f"started {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print("=" * 72)

    s1 = stage1(args.timeout)
    if not s1:
        return 1
    if not s1["survivors"]:
        print("\nNo candidates survived the name filters -- nothing to measure.")
        return 1

    s2 = stage2(s1["survivors"], args.sample, args.concurrency, args.timeout, args.full)
    if not s2:
        return 1

    report = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "list_complete": s1["complete"],
        "has_isin_field": s1["has_isin_field"],
        "funnel": s1["funnel"],
        "candidates": len(s1["survivors"]),
        "direct": sum(1 for s in s1["survivors"] if s["plan"] == "Direct"),
        "regular": sum(1 for s in s1["survivors"] if s["plan"] == "Regular"),
        "sampled": s2["sampled"],
        "sample_failed": s2["failed"],
        "median_latency_s": round(s2["median_latency"], 3),
        "median_nav_points": s2["median_nav_points"],
        "category_distribution": s2["dist"],
        "rejected_category": s2["unsupported"],
        "est_rankable_funds": round(s2["est_rankable"]),
        "full_scan": bool(args.full),
    }
    print()
    print("=" * 72)
    print("COPY THE BLOCK BELOW back into the design thread")
    print("=" * 72)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
