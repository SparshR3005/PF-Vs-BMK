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
import random
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ---------------------------------------------------------------- shared filters
# These now live in mf_universe.py so there is exactly ONE Python copy, imported by
# both this probe and the production fetcher. The dependency points at the shared
# module, never at the probe: this script is disposable, mf_universe.py is not.
from mf_universe import (  # noqa: E402
    API,
    CATEGORY_CANON,
    INCOME_TOKENS,
    name_looks_income_option,
    NON_EQUITY_NAME_TOKENS,
    UNRANKABLE_KEYS,
    category_key,
    classify_plan,
    get_json,
    name_looks_non_equity,
    norm_category,
    norm_name,
    parse_dmy,
    unwrap_list,
)

# ---------------------------------------------------------------- http


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
        if name_looks_income_option(n):
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

def fetch_detail(entry, timeout):
    code = entry["schemeCode"]
    payload, elapsed, nbytes, status = get_json(f"{API}/mf/{code}", timeout)
    if payload is None:
        return {"code": code, "ok": False, "status": status, "elapsed": elapsed}
    meta = payload.get("meta") or {}
    data = payload.get("data") or []
    # Don't assume newest-first or oldest-first ordering; take the true extremes.
    stamps = [d for d in (parse_dmy(r.get("date")) for r in data) if d]
    oldest = min(stamps) if stamps else None
    newest = max(stamps) if stamps else None
    span_years = ((newest - oldest).days / 365.25) if (oldest and newest) else 0.0
    return {
        "code": code,
        "ok": True,
        "status": status,
        "elapsed": elapsed,
        "bytes": nbytes,
        "plan": entry["plan"],
        "category_raw": meta.get("scheme_category"),
        "cat_key": category_key(meta.get("scheme_category")),
        "nav_points": len(data),
        "oldest": oldest.isoformat() if oldest else None,
        "newest": newest.isoformat() if newest else None,
        "span_years": span_years,
    }


def probe_latest_endpoint(entries, timeout):
    """Does /mf/<code>/latest return the category too?

    This is the single highest-leverage unknown left. A nightly job that refetches
    full NAV history for ~1200 funds pulls ~70 MB and ~20 min off a FREE community
    API, every night, forever. If /latest carries meta.scheme_category in a small
    payload, the nightly job can instead fetch one tiny record per fund and append
    it to the committed series, refetching full history only occasionally --
    roughly a 50x reduction in load. Worth ten requests to find out."""
    print()
    print("STAGE 3 -- is /mf/<code>/latest a cheap nightly alternative?")
    print("-" * 72)
    ok_n = with_cat = 0
    sizes, lats = [], []
    for e in entries[:10]:
        payload, elapsed, nbytes, status = get_json(f"{API}/mf/{e['schemeCode']}/latest", timeout)
        if payload is None:
            continue
        ok_n += 1
        sizes.append(nbytes)
        lats.append(elapsed)
        meta = payload.get("meta") or {}
        if category_key(meta.get("scheme_category")) is not None or meta.get("scheme_category"):
            with_cat += 1
    if not ok_n:
        print("  /latest did not respond -- nightly job must refetch full history.")
        return {"ok": 0}
    print(f"  responded {ok_n}/10   carries scheme_category: {with_cat}/{ok_n}")
    print(f"  median payload {statistics.median(sizes)/1024:.1f} KB "
          f"(vs ~58 KB for full history)   median latency {statistics.median(lats):.2f}s")
    verdict = ("USABLE -- nightly can fetch /latest and append; full refetch weekly"
               if with_cat == ok_n else
               "NOT usable for category; full detail still needed for discovery")
    print(f"  verdict: {verdict}")
    return {"ok": ok_n, "with_category": with_cat,
            "median_bytes": statistics.median(sizes),
            "median_latency": statistics.median(lats)}


def stage2(survivors, sample_n, concurrency, timeout, full):
    # Seeded RANDOM sample, not a fixed stride. Striding assumes the list has no
    # periodic structure, and mfapi's does: schemes arrive grouped by AMC, and AMCs
    # register funds in contiguous blocks. A stride that lands in step with those
    # blocks silently samples one slice of the market and reports it as the whole.
    # (Caught by tests/test_probe_ranks.py, where a strided sample returned a 100%
    # rejection rate that random sampling does not reproduce.) Seeded so two runs
    # over an unchanged list are comparable.
    if full:
        targets = survivors
    else:
        rng = random.Random(20260723)
        targets = rng.sample(survivors, min(sample_n, len(survivors)))
    label = "ALL" if full else f"sample of {len(targets)}"
    print()
    print(f"STAGE 2 -- per-scheme detail ({label}, concurrency {concurrency})")
    print("-" * 72)

    started = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = {pool.submit(fetch_detail, t, timeout): t for t in targets}
        done = 0
        step = 100 if len(targets) > 400 else 25
        for fut in as_completed(futs):
            results.append(fut.result())
            done += 1
            if done % step == 0 or done == len(targets):
                el = time.monotonic() - started
                rate = done / el if el else 0
                eta = (len(targets) - done) / rate if rate else 0
                print(f"    {done}/{len(targets)}  {el:6.0f}s elapsed  "
                      f"{rate:.2f}/s  ETA {eta/60:5.1f} min")
    wall = time.monotonic() - started

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    if not ok:
        print("  FAILED: no successful detail fetches. Check connectivity/rate limits.")
        return None

    lat = sorted(r["elapsed"] for r in ok)
    navs = sorted(r["nav_points"] for r in ok)
    observed_rate = len(targets) / wall if wall else 0

    print()
    print(f"  ok {len(ok)}, failed {len(bad)}")
    if bad:
        codes = {}
        for r in bad:
            codes[r["status"]] = codes.get(r["status"], 0) + 1
        print(f"  failure status codes: {codes}"
              + ("   <-- 429: mfapi IS rate limiting; lower --concurrency"
                 if 429 in codes else ""))
    print(f"  latency  median {statistics.median(lat):.2f}s   "
          f"p90 {lat[int(len(lat)*0.9)-1]:.2f}s   max {lat[-1]:.2f}s")
    print(f"  payload  median {statistics.median(r['bytes'] for r in ok)/1024:.0f} KB")
    print(f"  NAV pts  median {statistics.median(navs):.0f}   min {navs[0]}   max {navs[-1]}")

    # THROUGHPUT: measured, not modelled. An earlier version of this script divided
    # median latency by concurrency and under-predicted the real wall time by ~4x,
    # because the latency distribution is heavily right-skewed (median 1.5s, p90 14s)
    # and the slow tail dominates. Only observed throughput is trustworthy here.
    print()
    print(f"  OBSERVED THROUGHPUT: {observed_rate:.2f} schemes/sec at concurrency {concurrency}")
    print(f"    -> all {len(survivors)} candidates: {len(survivors)/observed_rate/60:.1f} min at this concurrency")

    # ---- category distribution, split by plan
    dist, plan_split, rejected_raw = {}, {}, {}
    for r in ok:
        k = r["cat_key"]
        if k is None:
            raw = (r["category_raw"] or "(empty)").strip()
            rejected_raw[raw] = rejected_raw.get(raw, 0) + 1
            continue
        dist[k] = dist.get(k, 0) + 1
        plan_split.setdefault(k, {"Direct": 0, "Regular": 0})[r["plan"]] += 1

    scale = len(survivors) / len(ok)
    print()
    print("  SEBI CATEGORY COUNTS" + ("" if full else "  (sampled -- see CI caveat below)"))
    print(f"    {'category':<12}{'seen':>6}{'Direct':>8}{'Regular':>9}{'est. full':>11}")
    for k in sorted(dist, key=lambda x: -dist[x]):
        ps = plan_split[k]
        flag = "  EXCLUDED" if k in UNRANKABLE_KEYS else ""
        est = dist[k] if full else dist[k] * scale
        print(f"    {k:<12}{dist[k]:>6}{ps['Direct']:>8}{ps['Regular']:>9}{est:>11.0f}{flag}")

    if rejected_raw:
        print()
        print("  REJECTED category strings (passed name filters, failed the allowlist)")
        print("    If a legitimate equity category appears here, the allowlist is missing it.")
        for raw, c in sorted(rejected_raw.items(), key=lambda kv: -kv[1])[:15]:
            print(f"    {c:>5}  {raw[:60]}")

    # ---- history depth: decides which horizons are worth displaying
    print()
    print("  HISTORY DEPTH (drives which period rows can be populated)")
    for yrs, lbl in ((1, "1Y"), (3, "3Y"), (5, "5Y"), (7, "7Y"), (10, "10Y")):
        n = sum(1 for r in ok if r["span_years"] >= yrs)
        print(f"    >= {lbl:<4} {n:>5} of {len(ok)}  ({100*n/len(ok):5.1f}%)"
              f"   est. {n*scale:>6.0f} funds" if not full else
              f"    >= {lbl:<4} {n:>5} of {len(ok)}  ({100*n/len(ok):5.1f}%)")

    rankable_sample = sum(v for k, v in dist.items() if k not in UNRANKABLE_KEYS)
    est_rankable = rankable_sample if full else rankable_sample * scale
    print()
    print(f"  RANKABLE equity funds (excl. Sectoral): {est_rankable:,.0f}")

    return {"dist": dist, "plan_split": plan_split, "rejected_raw": rejected_raw,
            "median_latency": statistics.median(lat), "median_nav_points": statistics.median(navs),
            "est_rankable": est_rankable, "sampled": len(ok), "failed": len(bad),
            "wall": wall, "observed_rate": observed_rate,
            "depth": {f"ge_{y}y": sum(1 for r in ok if r["span_years"] >= y) for y in (1, 3, 5, 7, 10)}}


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

    s3 = probe_latest_endpoint(s1["survivors"], args.timeout)

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
        "rejected_category": sum(s2["rejected_raw"].values()),
        "est_rankable_funds": round(s2["est_rankable"]),
        "full_scan": bool(args.full),
        "observed_rate_per_s": round(s2["observed_rate"], 3),
        "concurrency_used": args.concurrency,
        "plan_split": s2["plan_split"],
        "rejected_category_strings": dict(sorted(s2["rejected_raw"].items(), key=lambda kv: -kv[1])[:15]),
        "history_depth": s2["depth"],
        "latest_endpoint": s3,
    }
    print()
    print("=" * 72)
    print("COPY THE BLOCK BELOW back into the design thread")
    print("=" * 72)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())