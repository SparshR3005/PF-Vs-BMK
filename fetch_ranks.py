#!/usr/bin/env python3
"""
fetch_ranks.py -- build the category-ranking data behind the Insights tab.

WHAT IT PUBLISHES  (all under data/ranks/, mirroring data/tri/'s conventions)

  periods_<CAT>.json      Precomputed point-to-point returns, category averages,
                          ranks and quartiles for fixed horizons (6m..10y), for
                          BOTH plans. Tiny (a few KB). Computed here from full
                          DAILY NAV because point-to-point returns are measured
                          from just two observations, so a coarse grid moves ranks
                          by several places -- measured at up to 12.

  navs_<CAT>_<PLAN>.json  A WEEKLY NAV grid per fund, shipped to the browser so it
                          can rank candidates over the user's actual SIP window
                          using the same runSIP()/xirr() the portfolio uses.
                          Weekly is enough here (measured XIRR deviation 0.026 pp,
                          zero rank changes) because a SIP averages ~100
                          installments and endpoint errors cancel.

  index.json              Manifest: per-file freshness and status, so the client
                          can warn on stale data instead of silently ranking
                          against last month's numbers.

WHY WEEKLY AND NOT MONTHLY
  runSIP() places an installment at the first NAV within 7 days of the scheduled
  date. A month-end grid leaves most scheduled dates with no NAV in range: in
  testing, 0 of 120 installments were placed for SIP days 1, 5 and 12. A weekly
  grid placed all 120 in every case. Do not coarsen this grid.

WHY BOTH PLANS
  Ranking a Regular-plan holding against Direct peers measures a fee gap, not
  skill. Each plan is ranked against its own cohort.

WHY SECTORAL IS ABSENT
  MFAPI collapses every sector into one category string and never says which, so
  a flat "Sectoral/Thematic" ranking would compare an IT fund against a Pharma
  fund. Those funds are still benchmarked by the app via SECTOR_KEYWORDS; they
  are simply not ranked.

LOAD DISCIPLINE
  mfapi is a free community service. Universe discovery uses /mf/<code>/latest
  (~0.4 KB) rather than full history (~58 KB) -- measured 145x smaller -- and only
  the rankable subset gets a full-history fetch. Be a good citizen here: if this
  job gets throttled, the feature dies.

USAGE
  python fetch_ranks.py                 # full nightly run
  python fetch_ranks.py --dry-run       # compute everything, write nothing
  python fetch_ranks.py --canary MID_CAP  # one category end to end
  python fetch_ranks.py --max-funds 40  # cap work while testing
"""

import argparse
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# The scheme-universe filters live here, in the production job, and the probe
# imports them from this module. One definition, one place. tests/ asserts this
# module agrees with index.html, so the Python and the client can never drift.
from mf_universe import (  # noqa: F401
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
    parse_dmy,
    unwrap_list,
)

OUT_DIR = Path("data/ranks")

# Horizons published in the period table. 1-week and 1-month are deliberately
# absent: for a multi-year SIP investor they are noise, and short-horizon rank
# tables are precisely what drives performance chasing.
HORIZONS = [
    ("6m", 182),
    ("1y", 365),
    ("2y", 730),
    ("3y", 1095),
    ("5y", 1826),
    ("7y", 2557),
    ("10y", 3652),
]

# Never annualise a sub-year return -- it implies a precision the number does not
# have. Matches the "--" convention in the reference table.
MIN_ANNUALISE_DAYS = 365

# A category's `as_of` is the LATEST NAV date seen across every fund in it, so a
# fund that stopped reporting still gets measured against it. nav_on_or_before()
# then carries that fund's final NAV forward to both window boundaries and the
# result looks like a current figure -- a fund a full year stale published "0.0%"
# as its 1-year return in testing. Indian MF sees routine suspensions, mergers and
# closures, so this is an ordinary event, not an exotic one.
#
# 7 days matches the tolerance used everywhere else in this project (runSIP's NAV
# match window, MAX_STALE_DAYS in fetch_tri.py). A fund quiet for longer than a
# trading week is not reporting, and is excluded from that horizon rather than
# ranked on a stale price.
# A NAV cannot be dated in the future. MFAPI occasionally serves a malformed row,
# and `as_of` is a MAX across the whole category, so ONE such row redefines "now"
# for every other fund in it -- which, combined with the staleness rule below,
# marks every healthy fund dead and empties the category. Measured: a single row
# dated 2030 took a 6-fund cohort's 1-year universe to 0. Clamping `as_of` after
# the fact is not enough; the row has to be dropped at parse, before it can reach
# max(). Two days of slack absorbs IST-vs-UTC skew on the runner.
MAX_FUTURE_DAYS = 2
MAX_TERMINAL_STALE_DAYS = 7

# The window-OPEN observation has the same problem in reverse: if the nearest NAV
# at or before the window open is far older than the window, the "start" price
# belongs to a different period and the return spans the wrong interval. Kept
# looser than the terminal bound because early history is legitimately sparser
# (holidays, and thin early series), but still bounded.
MAX_BOUNDARY_STALE_DAYS = 30

# Hard bound on grid spacing. runSIP() matches an installment to the first NAV
# within 7 days of the scheduled date, so any wider hole can drop installments.
GRID_MAX_GAP_DAYS = 7
GRID_STEP_DAYS = 7
GRID_YEARS = 11           # 10y horizon plus buffer
MIN_GRID_POINTS = 8       # below this a fund cannot support any useful window

# Publishing gate. A category file is only replaced if the new one still covers a
# sane share of the funds the committed one had; a sudden collapse means mfapi
# returned junk, and last-good data beats confidently wrong data.
MIN_KEEP_FRACTION = 0.80
# A real category grows by a fund or two a year, never doubles overnight.
MAX_GROWTH_FACTOR = 1.60
# Below this many funds a quartile is noise dressed as a verdict; publish the
# rank and its denominator instead and let the thinness show.
MIN_QUARTILE_UNIVERSE = 8
# Losing a couple of funds is normal churn at any cohort size; only refuse a
# small-cohort drop when it is both proportionally AND absolutely large.
SMALL_COHORT_TOLERANCE = 3


# ------------------------------------------------------------------ pure maths
def build_weekly_grid(rows, as_of, years=GRID_YEARS, max_gap=GRID_MAX_GAP_DAYS):
    """Reduce a daily NAV history to a sparse grid with NO GAP EXCEEDING `max_gap`.

    Returns (t0, day_offsets, values, forced_gaps).

    The obvious implementation -- bucket into fixed 7-day windows, keep each
    bucket's last point -- is WRONG, and the bug is invisible on clean synthetic
    data. If one bucket's last trading day falls early (a holiday week) and the
    next bucket's falls late, the two selected points sit more than 7 days apart.
    Real published output had gaps of 8 and 9 days.

    That matters because runSIP() places an installment at the first NAV within 7
    days of the scheduled date. A 9-day hole can leave a scheduled date unmatched,
    the installment is skipped, and the eligibility filter (skipped == 0) then drops
    that fund from the ranking entirely -- silently, for a storage artifact rather
    than anything about the fund.

    So select greedily instead: from each chosen point, take the FURTHEST point
    still within max_gap. That keeps roughly weekly density while making the gap
    bound structural rather than hoped-for. Where the underlying data itself has a
    hole bigger than max_gap (a genuine market closure) no grid can fix it; those
    are counted and reported rather than hidden.
    """
    cutoff = as_of - timedelta(days=int(years * 365.25))
    horizon = date.today() + timedelta(days=MAX_FUTURE_DAYS)
    pts = []
    for r in rows:
        d = parse_dmy(r.get("date"))
        # This function parses rows itself rather than reusing the caller's points,
        # so it needs its own future guard: relying on `d > as_of` alone would trust
        # an as_of that a bad row may already have poisoned.
        if d is None or d < cutoff or d > as_of or d > horizon:
            continue
        try:
            nav = float(r.get("nav"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(nav) or nav <= 0:
            continue
        pts.append((d, nav))
    if not pts:
        return None
    pts.sort()
    # De-duplicate same-day rows, keeping the last seen for that date.
    dedup = {}
    for d, nav in pts:
        dedup[d] = nav
    pts = sorted(dedup.items())

    selected = [pts[0]]
    forced = 0
    i, n = 1, len(pts)
    while i < n:
        last = selected[-1][0]
        best = None
        j = i
        while j < n and (pts[j][0] - last).days <= max_gap:
            best = j
            j += 1
        if best is None:
            # Underlying history jumps more than max_gap; unavoidable, so record it.
            selected.append(pts[i])
            forced += 1
            i += 1
        else:
            selected.append(pts[best])
            i = best + 1

    if len(selected) < MIN_GRID_POINTS:
        return None
    t0 = selected[0][0]
    offsets = [(d - t0).days for d, _ in selected]
    values = [round(v, 4) for _, v in selected]
    return t0, offsets, values, forced


def nav_on_or_before(pts, target):
    """Latest (date, nav) at or before `target`; None if the series starts later."""
    lo, hi, best = 0, len(pts) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if pts[mid][0] <= target:
            best = pts[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def period_return(pts, as_of, days):
    """Point-to-point % return over `days`, or None when history doesn't cover it.

    Eligibility is strict: the series must actually start on or before the window
    open. A fund that launched mid-window would otherwise post a flattering short
    return and outrank funds that ran the whole period.

    The result is checked for finiteness. Extreme NAV pairs (1e-308 -> 1e308 was
    the reproduction) overflow to inf, and an inf that escapes here becomes a bare
    `Infinity` token in the published JSON, which the browser refuses to parse.
    """
    if not pts:
        return None
    start = as_of - timedelta(days=days)
    if pts[0][0] > start:
        return None
    a = nav_on_or_before(pts, start)
    b = nav_on_or_before(pts, as_of)
    if not a or not b or a[1] <= 0:
        return None
    # A fund whose last NAV predates the window close is STALE: nav_on_or_before
    # would carry that final NAV forward to both boundaries and report it as a
    # current figure. A suspended/closed/merging fund then ranks against live
    # peers -- the reproduction showed a fund a full year stale publishing "0.0%"
    # for its 1y return. Refuse the horizon instead.
    if (as_of - b[0]).days > MAX_TERMINAL_STALE_DAYS:
        return None
    # Likewise the opening observation: if the nearest NAV at or before the window
    # open is far older than the window itself, the "start" price is not this
    # period's price and the return spans the wrong interval.
    if (start - a[0]).days > MAX_BOUNDARY_STALE_DAYS:
        return None
    result = (b[1] / a[1] - 1.0) * 100.0
    return result if math.isfinite(result) else None


def annualised(abs_pct, days):
    """CAGR from a cumulative return. None under a year, by design."""
    if abs_pct is None or days < MIN_ANNUALISE_DAYS:
        return None
    growth = 1.0 + abs_pct / 100.0
    if growth <= 0:
        return None
    result = (growth ** (365.25 / days) - 1.0) * 100.0
    return result if math.isfinite(result) else None


def exclusion_reason(pts, as_of):
    """Why does this fund carry no horizon at all? Diagnostic only.

    The run log used to report every exclusion as "too young". That was accurate
    when youth was the only cause, but staleness checks were later added underneath
    and the message never caught up. The result: a run that correctly dropped 12
    dormant ELSS schemes reported them as too young, while the arithmetic said
    otherwise -- the nav grids were unchanged, so no new funds had entered and the
    same funds had simply lost every horizon. Funds do not get younger. Anyone
    investigating was sent looking for something that could not exist.

    Classified against the SHORTEST horizon: if a fund cannot manage even that, the
    reason it fails there is the reason it fails everywhere.
    """
    if not pts:
        return "no NAV data"
    shortest = min(d for _, d in HORIZONS)
    start = as_of - timedelta(days=shortest)
    if pts[0][0] > start:
        return "too young"
    b = nav_on_or_before(pts, as_of)
    if b is None:
        return "no NAV data"
    if (as_of - b[0]).days > MAX_TERMINAL_STALE_DAYS:
        return "stale"
    a = nav_on_or_before(pts, start)
    if a is None:
        return "no opening NAV"
    if (start - a[0]).days > MAX_BOUNDARY_STALE_DAYS:
        return "gap at window open"
    if a[1] <= 0 or not math.isfinite(a[1]) or not math.isfinite(b[1]):
        return "bad NAV value"
    return "other"


def rank_desc(pairs):
    """[(key, value)] -> {key: rank}, best value = rank 1. Ties share a rank.

    NaN-SAFE. The previous form used `vals.index(v)`, which compares with `==`,
    and NaN != NaN. A single NaN therefore either raised ValueError or shifted
    every rank below it: [1.0, NaN, 3.0] published {a:1, b:2, c:3} when c is
    genuinely rank 1. Those ranks went out with no error and no warning.
    period_return() can emit a non-finite value (e.g. inf/inf) if a bad NAV
    survives parsing, so this is reachable from live data, not theoretical.

    Non-finite entries are dropped rather than ranked: a fund whose return could
    not be computed has no defensible position in the table, and omitting it
    keeps the published denominator honest.

    Also O(n) instead of O(n^2) -- index() rescanned the whole list per fund.
    """
    clean = [(k, v) for k, v in pairs
             if isinstance(v, (int, float)) and math.isfinite(v)]
    if not clean:
        return {}
    first_pos = {}
    for i, v in enumerate(sorted((v for _, v in clean), reverse=True)):
        # setdefault keeps the FIRST index for a repeated value, so ties share the
        # better rank -- the same convention the old index() call produced.
        first_pos.setdefault(v, i + 1)
    return {k: first_pos[v] for k, v in clean}


def quartile(rank, n):
    """1 = top quartile ... 4 = bottom, or None when the cohort is too small.

    Two things were wrong with the obvious ceil(rank/n*4):

    1. For n < 4 it could never return 1 -- the best fund in a 3-fund category was
       published as "2nd quartile", and in a 1-fund category the only fund came out
       BOTTOM quartile. Real CONTRA data hit exactly this: Kotak Contra ranked #1 of
       3 and was labelled quartile 2. The floor form below always gives rank 1 -> 1.

    2. Even correct, a quartile over 3 funds is theatre. Quartiles need enough
       members to mean anything, so below MIN_QUARTILE_UNIVERSE we publish None and
       let the UI show the bare "1 of 3", which is honest about how thin it is.
    """
    if not n or rank is None:
        return None
    if n < MIN_QUARTILE_UNIVERSE:
        return None
    return min(4, (rank - 1) * 4 // n + 1)


# ------------------------------------------------------------------ per-category
def compute_period_table(funds, as_of):
    """funds: [{code, name, plan, pts}] -> the periods_<CAT>.json payload body.

    Ranks are computed WITHIN plan, and within the set eligible for that horizon.
    The eligible count is published alongside every rank so a thin comparison
    looks thin instead of authoritative.
    """
    plans = {}
    for plan in ("Direct", "Regular"):
        cohort = [f for f in funds if f["plan"] == plan]
        if not cohort:
            continue
        per_fund = {f["code"]: {"name": f["name"], "abs": {}, "ann": {}, "rank": {}, "q": {}}
                    for f in cohort}
        universe, avg, med = {}, {}, {}
        for label, days in HORIZONS:
            scored = []
            for f in cohort:
                r = period_return(f["pts"], as_of, days)
                # period_return already refuses non-finite results, but assert it
                # here too: everything downstream (avg, median, the published
                # value) assumes finiteness, and a bare Infinity in the output is
                # a file the browser cannot parse at all.
                if r is None or not math.isfinite(r):
                    continue
                scored.append((f["code"], r))
                per_fund[f["code"]]["abs"][label] = round(r, 2)
                a = annualised(r, days)
                if a is not None:
                    per_fund[f["code"]]["ann"][label] = round(a, 2)
            if not scored:
                universe[label] = 0
                continue
            ranks = rank_desc(scored)
            # ONE denominator for the whole horizon: the population actually
            # ranked. rank_desc drops any non-finite value that still slipped
            # through, so len(scored) can exceed it -- and a published "rank 6 of
            # 134" whose 134 came from a different population than the 6 is the
            # kind of number a client checks and cannot reconcile.
            ranked_n = len(ranks)
            universe[label] = ranked_n
            vals = [v for code, v in scored if code in ranks]
            avg[label] = round(sum(vals) / len(vals), 2)
            med[label] = round(statistics.median(vals), 2)
            for code, rk in ranks.items():
                per_fund[code]["rank"][label] = rk
                per_fund[code]["q"][label] = quartile(rk, ranked_n)
        plans[plan] = {"universe": universe, "avg": avg, "median": med,
                       "funds": {c: v for c, v in per_fund.items() if v["abs"]}}
    return plans


def build_nav_doc(key, plan, funds, as_of):
    """The weekly grid the browser uses for same-window top-5."""
    out = {}
    forced_total = 0
    for f in funds:
        grid = build_weekly_grid(f["rows"], as_of)
        if not grid:
            continue
        t0, offsets, values, forced = grid
        forced_total += forced
        out[f["code"]] = {"n": f["name"], "t0": t0.isoformat(), "d": offsets, "v": values}
    if not out:
        return None
    return {
        "key": key,
        "plan": plan,
        "as_of": as_of.isoformat(),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "step_days": GRID_STEP_DAYS,
        "count": len(out),
        "max_gap_days": GRID_MAX_GAP_DAYS,
        "forced_gaps": forced_total,   # holes the source data itself had
        "funds": out,
    }


# ------------------------------------------------------------------ io
def write_json_atomic(path, payload):
    """Two-phase write: a crash mid-write must not leave a half-file that parses
    as valid JSON with missing funds.

    allow_nan=False is LOAD-BEARING, not hygiene. Python's json.dump defaults to
    allow_nan=True, which emits BARE `Infinity` / `NaN` tokens. Those are not
    valid JSON: Python round-trips them happily, but the browser's JSON.parse()
    (and response.json()) REJECT the file outright -- so one extreme NAV pair
    could take an entire ranking category offline in the client while every
    server-side check passed. fetch_tri.py's writer already sets this; the two
    writers disagreeing is exactly how the bad value reached disk.

    Failing here is the point: a ValueError aborts this file's publish and leaves
    the last-good copy in place, which is strictly better than shipping a file the
    browser cannot read.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"), sort_keys=True,
                      allow_nan=False)
    except (ValueError, OSError):
        # Never leave a partial .tmp behind for the next run to trip over.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def existing_count(path):
    try:
        with path.open(encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if "count" in doc:
        return doc["count"]
    total = 0
    for plan in (doc.get("plans") or {}).values():
        total += len(plan.get("funds") or {})
    return total or None


def safe_to_publish(path, new_count):
    """Refuse to replace a healthy file with a collapsed one.

    Also refuses an implausible SURGE. The gate was originally one-sided, which left
    a trap: an upstream duplicate-list bug inflates the file, passes as "growth",
    and then every correct night afterwards looks like a 50% collapse and is
    refused -- pinning the data to the corrupted version. A category cannot
    plausibly double overnight, so treat that as corruption too.
    """
    old = existing_count(path)
    if old is None or old == 0:
        return True, "no prior file"
    # A PERCENTAGE floor alone assumes a large cohort. In a 6-fund category one
    # fund merging or ageing out is a 17% drop and two is 33% -- ordinary events
    # that a 80% floor refuses. Worse, once refused the stale count never updates,
    # so the category stays refused forever. CONTRA hit exactly this: 6 vs 8 = 75%,
    # refused, and it would have stayed refused every night thereafter. So allow a
    # small ABSOLUTE change regardless of percentage.
    if new_count < old * MIN_KEEP_FRACTION and (old - new_count) > SMALL_COHORT_TOLERANCE:
        return False, f"REFUSED: {new_count} funds vs {old} committed (<{MIN_KEEP_FRACTION:.0%})"
    if new_count > old * MAX_GROWTH_FACTOR:
        return False, (f"REFUSED: {new_count} funds vs {old} committed "
                       f"(>{MAX_GROWTH_FACTOR:.0%}) -- looks like duplicated upstream data")
    return True, f"{new_count} vs {old} committed"


# ------------------------------------------------------------------ fetching
def discover_universe(timeout, concurrency, log):
    """Name-filter /mf, then resolve each survivor's category via /latest.

    /latest carries meta.scheme_category at ~0.4 KB against ~58 KB for full
    history, so discovery costs a fraction of a full-detail pass.
    """
    raw, _, _, status = get_json(f"{API}/mf", timeout)
    if raw is None:
        log(f"FATAL: /mf returned {status}")
        return None
    all_schemes, complete = unwrap_list(raw)
    if all_schemes is None:
        log("FATAL: unexpected /mf shape")
        return None
    if not complete:
        log("WARNING: /mf looks paginated; universe may be partial")

    has_isin = any(isinstance(s, dict) and "isinGrowth" in s for s in all_schemes)

    # DEDUPE BY schemeCode. mfapi has been observed serving the full /mf list with
    # every entry duplicated: one probe run returned 75,378 schemes and a later run
    # returned 37,689 -- an exact 2.0000 ratio across every single category. Without
    # this, a duplicated night puts each fund into its category ranking twice, so
    # "rank 6 of 134" is published where the truth is "rank 3 of 67". Worse, the
    # publish gate only guards against shrinkage, so the inflated files pass, and the
    # next NORMAL night looks like a 50% collapse and gets refused -- latching the
    # corruption in permanently. Deduping at ingest is the root-cause fix.
    seen, deduped, dupes = set(), [], 0
    for s in all_schemes:
        if not isinstance(s, dict):
            continue
        code = s.get("schemeCode")
        if code is None:
            continue
        if code in seen:
            dupes += 1
            continue
        seen.add(code)
        deduped.append(s)
    if dupes:
        log(f"WARNING: /mf contained {dupes} duplicate schemeCode entries -- dropped "
            f"({len(all_schemes)} -> {len(deduped)})")
    all_schemes = deduped

    candidates = []
    for s in all_schemes:
        if not isinstance(s, dict) or not s.get("schemeName") or not s.get("schemeCode"):
            continue
        n = str(s["schemeName"]).lower()
        if "growth" not in n:
            continue
        if name_looks_income_option(n):
            continue
        if has_isin and not str(s.get("isinGrowth") or "").strip():
            continue
        if name_looks_non_equity(n):
            continue
        candidates.append({"code": s["schemeCode"], "name": s["schemeName"],
                           "plan": classify_plan(n)})
    log(f"name filters: {len(all_schemes)} -> {len(candidates)} candidates")

    def resolve(c):
        payload, _, _, _ = get_json(f"{API}/mf/{c['code']}/latest", timeout)
        if payload is None:
            return None
        cat = ((payload.get("meta") or {}).get("scheme_category"))
        key = category_key(cat)
        if key is None or key in UNRANKABLE_KEYS:
            return None
        c["cat"] = key
        return c

    resolved, done = [], 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = [pool.submit(resolve, c) for c in candidates]
        for fut in as_completed(futs):
            r = fut.result()
            done += 1
            if r:
                resolved.append(r)
            if done % 500 == 0:
                log(f"  categorised {done}/{len(candidates)} -> {len(resolved)} rankable")
    log(f"universe: {len(resolved)} rankable funds across "
        f"{len({r['cat'] for r in resolved})} categories")
    return resolved


def fetch_histories(funds, timeout, concurrency, log):
    def one(f):
        payload, _, _, _ = get_json(f"{API}/mf/{f['code']}", timeout)
        if payload is None:
            return None
        rows = payload.get("data") or []
        if not rows:
            return None
        f["rows"] = rows
        return f

    out, done = [], 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = [pool.submit(one, f) for f in funds]
        for fut in as_completed(futs):
            r = fut.result()
            done += 1
            if r:
                out.append(r)
            if done % 200 == 0:
                log(f"  history {done}/{len(funds)}")
    return out


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Build category ranking data for the Insights tab.")
    ap.add_argument("--dry-run", action="store_true", help="compute everything, write nothing")
    ap.add_argument("--canary", metavar="CAT", help="process a single category end to end")
    ap.add_argument("--max-funds", type=int, default=0, help="cap funds per category (testing)")
    ap.add_argument("--force", action="store_true",
                    help="bypass the publish gate; for deliberate schema/semantics changes")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    started = time.monotonic()

    def log(msg):
        print(f"[{time.monotonic()-started:7.1f}s] {msg}", flush=True)

    log("discovering universe")
    universe = discover_universe(args.timeout, args.concurrency, log)
    if not universe:
        return 1
    if args.canary:
        universe = [u for u in universe if u["cat"] == args.canary]
        log(f"canary {args.canary}: {len(universe)} funds")
        if not universe:
            log("FATAL: canary category matched no funds")
            return 1

    by_cat = {}
    for u in universe:
        by_cat.setdefault(u["cat"], []).append(u)

    manifest = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "categories": {}}
    written = refused = failed = 0

    for cat in sorted(by_cat):
        # ONE category must not be able to take down the others. write_json_atomic
        # raises by design (allow_nan=False, so a non-finite value can never reach
        # disk), but nothing caught it: categories run in alphabetical order, so an
        # exception on CONTRA aborted main() and the remaining nine never wrote at
        # all -- no files, no manifest entries. A last line of defence should not
        # also be a single point of failure.
        #
        # The failed category is marked "stale", NOT dropped: a missing manifest
        # entry tells the client the category does not exist, while "stale" tells it
        # the committed files are real but old. Those mean different things.
        try:
            funds = by_cat[cat]
            if args.max_funds:
                funds = funds[: args.max_funds]
            log(f"{cat}: fetching {len(funds)} histories")
            funds = fetch_histories(funds, args.timeout, args.concurrency, log)
            if not funds:
                log(f"{cat}: no histories returned -- skipping")
                manifest["categories"][cat] = {"status": "missing"}
                continue

            # Parse once; both the period table and the grid reuse it.
            as_of = None
            future_horizon = date.today() + timedelta(days=MAX_FUTURE_DAYS)
            dropped_future = 0
            for f in funds:
                # DEDUPE BY DATE, keeping the LAST row seen for a date. Three paths
                # consume this data and they must agree: build_weekly_grid() already
                # keeps the last, and index.html's getDetail() now does the same.
                # A bare list + pts.sort() did NOT: sorting (date, nav) tuples makes a
                # duplicated date resolve to the HIGHEST nav, so the period table and
                # the weekly grid could disagree about the same fund on the same day.
                by_day = {}
                for r in f["rows"]:
                    d = parse_dmy(r.get("date"))
                    if d is None:
                        continue
                    # Drop future-dated rows BEFORE they can reach the max() below.
                    if d > future_horizon:
                        dropped_future += 1
                        continue
                    try:
                        nav = float(r.get("nav"))
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(nav) and nav > 0:
                        by_day[d] = nav
                pts = sorted(by_day.items())
                f["pts"] = pts
                if pts:
                    as_of = pts[-1][0] if as_of is None else max(as_of, pts[-1][0])
            if dropped_future:
                log(f"{cat}: dropped {dropped_future} future-dated NAV row(s) "
                    f"(after {future_horizon.isoformat()})")
            if as_of is None:
                log(f"{cat}: no parseable NAV -- skipping")
                manifest["categories"][cat] = {"status": "missing"}
                continue

            usable = [f for f in funds if f["pts"]]
            plan_tables = compute_period_table(usable, as_of)
            # Count what actually lands in the file. compute_period_table drops funds with
            # no eligible horizon at all (under ~6 months old), so len(usable) overstates
            # it -- CONTRA published 8 while holding 3+3, LARGE_MID 69 while holding
            # 33+34. A manifest that disagrees with its own payload is worse than no
            # manifest, because the client trusts it.
            published = sum(len(pt.get("funds") or {}) for pt in plan_tables.values())
            periods = {
                "key": cat, "as_of": as_of.isoformat(),
                "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "horizons": [h for h, _ in HORIZONS],
                "count": published,
                "parsed": len(usable),      # kept for diagnostics; never a display figure
                "plans": plan_tables,
            }
            p_path = OUT_DIR / f"periods_{cat}.json"
            excluded_tally = {}
            if published < len(usable):
                ranked_codes = set()
                for pt in plan_tables.values():
                    ranked_codes.update(pt.get("funds") or {})
                for f in usable:
                    if f["code"] in ranked_codes:
                        continue
                    why = exclusion_reason(f["pts"], as_of)
                    excluded_tally[why] = excluded_tally.get(why, 0) + 1
                detail = ", ".join(f"{n} {why}" for why, n
                                   in sorted(excluded_tally.items(), key=lambda kv: -kv[1]))
                log(f"{cat}: {len(usable)-published} fund(s) carry no horizon -- "
                    f"excluded ({detail})")
            allowed, why = safe_to_publish(p_path, published)
            if args.force and not allowed:
                allowed, why = True, why + "  [OVERRIDDEN by --force]"
            log(f"{cat}: periods {why}")
            if allowed and not args.dry_run:
                write_json_atomic(p_path, periods)
                written += 1
            elif not allowed:
                refused += 1

            # Two different populations, so two different names. "ranked" is the period
            # table (needs >=6 months of history); "grid" is the weekly NAV file (needs
            # ~2 months). They legitimately differ, and a single "funds" field made the
            # manifest look self-contradictory.
            cat_status = {"status": "ok" if allowed else "stale", "as_of": as_of.isoformat(),
                          "ranked": published, "plans": {}}
            # Why funds were left out, by cause. A bare count invites the wrong guess,
            # which is exactly what happened when 12 stale ELSS schemes were reported
            # as young ones.
            if excluded_tally:
                cat_status["excluded"] = excluded_tally
            for plan in ("Direct", "Regular"):
                cohort = [f for f in usable if f["plan"] == plan]
                if not cohort:
                    continue
                doc = build_nav_doc(cat, plan, cohort, as_of)
                if not doc:
                    continue
                n_path = OUT_DIR / f"navs_{cat}_{plan}.json"
                ok_pub, why2 = safe_to_publish(n_path, doc["count"])
                if args.force and not ok_pub:
                    ok_pub, why2 = True, why2 + "  [OVERRIDDEN by --force]"
                log(f"{cat}/{plan}: navs {why2}")
                if ok_pub and not args.dry_run:
                    write_json_atomic(n_path, doc)
                    written += 1
                elif not ok_pub:
                    refused += 1
                cat_status["plans"][plan] = {"grid": doc["count"],
                                             "status": "ok" if ok_pub else "stale"}
            manifest["categories"][cat] = cat_status


        except Exception as exc:   # noqa: BLE001 - any failure must stay local
            failed += 1
            log(f"{cat}: FAILED ({type(exc).__name__}: {exc}) -- "
                f"keeping last-good files, continuing with other categories")
            prev = manifest["categories"].get(cat) or {}
            prev.update({"status": "stale", "error": type(exc).__name__})
            manifest["categories"][cat] = prev
            continue
    if not args.dry_run:
        write_json_atomic(OUT_DIR / "index.json", manifest)
    log(f"done: {written} file(s) written, {refused} refused, {failed} category error(s)"
        + (" (dry run -- nothing written)" if args.dry_run else ""))
    # A category that errored is a real failure even when others succeeded, so the
    # Action must go red rather than reporting a partial run as success.
    if failed:
        return 1
    return 1 if refused and not written else 0


if __name__ == "__main__":
    sys.exit(main())
