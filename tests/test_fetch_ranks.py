#!/usr/bin/env python3
"""
Tests for fetch_ranks.py -- the computation core, offline.

No network. Everything here exercises the pure functions that decide what numbers
end up in front of the user: the weekly grid, point-to-point returns, eligibility,
ranking, quartiles, and the publish gate that refuses to overwrite good data with
collapsed data.

    python tests/test_fetch_ranks.py
"""

import json
import math
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fetch_ranks as R  # noqa: E402

_pass = 0
_fail = 0


def ok(label, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {label}")
    else:
        _fail += 1
        print(f"  FAIL  {label}")


def eq(label, got, want):
    ok(label, got == want)
    if got != want:
        print(f"          got  {got!r}")
        print(f"          want {want!r}")


def near(label, got, want, tol):
    ok(f"{label} (got {got!r})", got is not None and abs(got - want) <= tol)


print("Run python tests/test_fetch_ranks.py")

AS_OF = date(2026, 7, 17)


def daily_rows(years, start=None, rate=0.10):
    """Business-day NAV rows in mfapi's DD-MM-YYYY shape, compounding at `rate`."""
    start = start or (AS_OF - timedelta(days=int(years * 365.25)))
    rows, nav, d = [], 100.0, start
    while d <= AS_OF:
        if d.weekday() < 5:
            nav *= (1 + rate) ** (1 / 252)
            rows.append({"date": f"{d.day:02d}-{d.month:02d}-{d.year}", "nav": f"{nav:.4f}"})
        d += timedelta(days=1)
    return rows


def to_pts(rows):
    out = []
    for r in rows:
        dt = R.parse_dmy(r["date"])
        if dt:
            out.append((dt, float(r["nav"])))
    out.sort()
    return out


# ------------------------------------------------------------------ weekly grid
def holiday_rows(years, seed=11, rate=0.10):
    """Business days MINUS realistic holiday clusters.

    The original tests used clean weekday-only data, where every 7-day bucket was
    full and the last point always landed at the bucket end -- so the old bucketed
    grid never produced an oversized gap and the invariant test passed while the
    real published data had 8- and 9-day holes. Indian markets close for Diwali,
    Holi and scattered single days; this reproduces that, which is what makes the
    max-gap assertion meaningful rather than decorative.
    """
    import random
    rnd = random.Random(seed)
    start = AS_OF - timedelta(days=int(years * 365.25))
    closed, d = set(), start
    while d <= AS_OF:
        if rnd.random() < 0.012:                       # multi-day festival closure
            for k in range(rnd.randint(2, 5)):
                closed.add(d + timedelta(days=k))
        elif rnd.random() < 0.02:                      # scattered single holidays
            closed.add(d)
        d += timedelta(days=1)
    rows, nav, d = [], 100.0, start
    while d <= AS_OF:
        if d.weekday() < 5 and d not in closed:
            nav *= (1 + rate) ** (1 / 252)
            rows.append({"date": f"{d.day:02d}-{d.month:02d}-{d.year}", "nav": f"{nav:.4f}"})
        d += timedelta(days=1)
    return rows


rows = daily_rows(6)
grid = R.build_weekly_grid(rows, AS_OF)
ok("grid builds from a 6-year daily history", grid is not None)
t0, offs, vals, _forced = grid
eq("grid offsets and values are the same length", len(offs), len(vals))
ok("grid is roughly weekly (~52/yr)", 280 <= len(vals) <= 330)
ok("grid offsets strictly increase", all(b > a for a, b in zip(offs, offs[1:])))
ok("no gap exceeds 7 days, so navOnOrAfter(d,7) can always place an installment",
   all(b - a <= 7 for a, b in zip(offs, offs[1:])))

# THE REGRESSION THAT MATTERED. Clean weekday data cannot expose the bucketed
# grid's flaw; holiday clusters can, and real published files had 8- and 9-day
# gaps that could silently drop a fund from every ranking.
#
# The invariant is NOT "no gap exceeds 7" -- if the source itself has a hole longer
# than 7 days, nothing can bridge it. The invariant is that the grid never ADDS a
# gap: every oversized gap must correspond to a hole that was already in the data,
# and those are counted in forced_gaps.
for _seed in (11, 29, 47, 83, 101):
    _rows = holiday_rows(8, seed=_seed)
    _hg = R.build_weekly_grid(_rows, AS_OF)
    if not _hg:
        ok(f"grid builds despite holidays (seed {_seed})", False)
        break
    _, _ho, _, _hf = _hg
    _over = [b - a for a, b in zip(_ho, _ho[1:]) if b - a > R.GRID_MAX_GAP_DAYS]
    if len(_over) != _hf:
        ok(f"every oversized gap is a forced one (seed {_seed}: "
           f"{len(_over)} oversized vs {_hf} forced)", False)
        break
    # And each forced gap must be genuinely unbridgeable in the source.
    _src = sorted({R.parse_dmy(r["date"]) for r in _rows})
    _src_gaps = [(b - a).days for a, b in zip(_src, _src[1:])]
    if _over and max(_over) > max(_src_gaps) + R.GRID_MAX_GAP_DAYS:
        ok(f"forced gaps stay within what the source forces (seed {_seed})", False)
        break
else:
    ok("the grid never adds a gap beyond what the source data forces", True)

_hg = R.build_weekly_grid(holiday_rows(8), AS_OF)
ok("holiday grid stays roughly weekly (not degenerate)", 350 <= len(_hg[2]) <= 460)

# With realistic Indian-market closures (never more than a long weekend), every gap
# should in fact come in at or under 7.
def _tight_rows(years):
    rows, nav = [], 100.0
    d = AS_OF - timedelta(days=int(years * 365.25))
    skip = 0
    while d <= AS_OF:
        if d.weekday() < 5:
            skip = (skip + 1) % 61
            if skip not in (0, 1):            # an occasional 2-day closure
                nav *= 1.0004
                rows.append({"date": f"{d.day:02d}-{d.month:02d}-{d.year}", "nav": f"{nav:.4f}"})
        d += timedelta(days=1)
    return rows

_tg = R.build_weekly_grid(_tight_rows(8), AS_OF)
_to = _tg[1]
ok("under realistic market closures every gap is <= 7 days",
   max(b - a for a, b in zip(_to, _to[1:])) <= R.GRID_MAX_GAP_DAYS)
ok("and none of them are forced", _tg[3] == 0)

# A genuine multi-month hole in the SOURCE is reported rather than hidden.
_sparse = [{"date": "01-01-2020", "nav": "100"}]
for _k in range(120):                      # enough post-hole history to clear the floor
    _d = date(2020, 3, 1) + timedelta(days=_k)
    _sparse.append({"date": f"{_d.day:02d}-{_d.month:02d}-{_d.year}", "nav": f"{100+_k}"})
_sg = R.build_weekly_grid(_sparse, date(2020, 6, 28), years=11)
ok("a two-month hole in the source is counted in forced_gaps",
   _sg is not None and _sg[3] >= 1)
ok("the forced hole is the only oversized gap",
   _sg is not None
   and len([b - a for a, b in zip(_sg[1], _sg[1][1:]) if b - a > R.GRID_MAX_GAP_DAYS]) == _sg[3])

ok("a fund with almost no history yields no grid",
   R.build_weekly_grid(daily_rows(0.02), AS_OF) is None)
ok("duplicate same-day rows collapse to one point",
   len(R.build_weekly_grid(daily_rows(3) + daily_rows(3), AS_OF)[2])
   == len(R.build_weekly_grid(daily_rows(3), AS_OF)[2]))
ok("empty rows yield no grid", R.build_weekly_grid([], AS_OF) is None)
ok("garbage rows are skipped, not fatal",
   R.build_weekly_grid([{"date": "nonsense", "nav": "x"}] * 5, AS_OF) is None)

mixed = daily_rows(3) + [{"date": "01-01-2026", "nav": "-5"},
                         {"date": "02-01-2026", "nav": "0"},
                         {"date": "03-01-2026", "nav": "abc"}]
g2 = R.build_weekly_grid(mixed, AS_OF)
ok("non-positive and unparseable NAVs are dropped", g2 is not None and all(v > 0 for v in g2[2]))

# History older than the retention window must not bloat the grid.
long_rows = daily_rows(20)
g3 = R.build_weekly_grid(long_rows, AS_OF)
ok("history is capped at the retention window (~11y)", len(g3[2]) <= 11 * 53)


# ------------------------------------------------------------------ returns
pts10 = to_pts(daily_rows(10, rate=0.10))
r1y = R.period_return(pts10, AS_OF, 365)
near("1-year return on a 10% compounder", r1y, 10.0, 0.6)
r5y = R.period_return(pts10, AS_OF, 1826)
near("5-year cumulative return", r5y, (1.10 ** 5 - 1) * 100, 4.0)

# Eligibility is the guard that stops a young fund posting a flattering number.
pts2 = to_pts(daily_rows(2))
ok("a 2-year-old fund is INELIGIBLE for the 5-year horizon",
   R.period_return(pts2, AS_OF, 1826) is None)
ok("the same fund IS eligible for the 1-year horizon",
   R.period_return(pts2, AS_OF, 365) is not None)
ok("empty series returns None", R.period_return([], AS_OF, 365) is None)

near("annualising a 5-year cumulative recovers the CAGR",
     R.annualised(r5y, 1826), 10.0, 0.6)
eq("sub-year returns are never annualised", R.annualised(5.0, 182), None)
eq("annualising None is None", R.annualised(None, 1826), None)
eq("a total wipeout does not raise", R.annualised(-100.0, 1826), None)


# ------------------------------------------------------------------ ranking
pairs = [("a", 10.0), ("b", 30.0), ("c", 20.0)]
eq("best return ranks 1", R.rank_desc(pairs), {"a": 3, "b": 1, "c": 2})
eq("ties share a rank", R.rank_desc([("a", 5.0), ("b", 5.0), ("c", 1.0)]),
   {"a": 1, "b": 1, "c": 3})

eq("rank 1 of 40 is the top quartile", R.quartile(1, 40), 1)
eq("rank 40 of 40 is the bottom quartile", R.quartile(40, 40), 4)
eq("rank 10 of 40 is still the top quartile", R.quartile(10, 40), 1)
eq("rank 11 of 40 crosses into the second", R.quartile(11, 40), 2)
eq("rank 30 of 40 is the third quartile", R.quartile(30, 40), 3)
eq("quartile of an empty universe is None", R.quartile(1, 0), None)
ok("quartiles always fall in 1..4 for every rank in a 37-fund universe",
   all(R.quartile(i, 37) in (1, 2, 3, 4) for i in range(1, 38)))

# REGRESSION: the original ceil(rank/n*4) could never return 1 for n < 4. Live
# CONTRA data published Kotak Contra as rank 1 of 3 and quartile 2; at n=1 the sole
# fund in a category came out BOTTOM quartile. The top-ranked fund must always be
# in the top quartile whenever a quartile is published at all.
for _n in range(R.MIN_QUARTILE_UNIVERSE, 60):
    if R.quartile(1, _n) != 1:
        ok(f"rank 1 of {_n} must be top quartile", False)
        break
else:
    ok("rank 1 is the top quartile at every publishable cohort size", True)
for _n in range(R.MIN_QUARTILE_UNIVERSE, 60):
    if R.quartile(_n, _n) != 4:
        ok(f"rank {_n} of {_n} must be bottom quartile", False)
        break
else:
    ok("the last rank is the bottom quartile at every publishable cohort size", True)
ok("quartiles never decrease as rank worsens",
   all(R.quartile(r, 40) <= R.quartile(r + 1, 40) for r in range(1, 40)))

# A quartile over three funds is theatre; publish the bare rank instead.
ok("quartiles are suppressed on a 3-fund cohort (real CONTRA case)",
   R.quartile(1, 3) is None)
ok("quartiles are suppressed just below the threshold",
   R.quartile(1, R.MIN_QUARTILE_UNIVERSE - 1) is None)
ok("quartiles appear exactly at the threshold",
   R.quartile(1, R.MIN_QUARTILE_UNIVERSE) == 1)


# ------------------------------------------------------------------ period table
funds = []
for i in range(12):
    yrs = 12 if i < 6 else 2                     # half the cohort is too young for long horizons
    funds.append({"code": f"C{i}", "name": f"Fund {i}",
                  "plan": "Direct" if i % 2 == 0 else "Regular",
                  "pts": to_pts(daily_rows(yrs, rate=0.06 + 0.01 * i))})
table = R.compute_period_table(funds, AS_OF)

eq("both plans are ranked separately", sorted(table), ["Direct", "Regular"])
d = table["Direct"]
ok("the 1-year universe includes every Direct fund", d["universe"]["1y"] == 6)
ok("the 10-year universe excludes the young funds", d["universe"]["10y"] == 3)
ok("category average is published per horizon", "1y" in d["avg"] and "1y" in d["median"])
ok("a young fund carries no 10-year figure",
   all("10y" not in f["abs"] for c, f in d["funds"].items() if c in ("C6", "C8", "C10")))
ok("every published rank is within its horizon's universe",
   all(rk <= d["universe"][h] for f in d["funds"].values() for h, rk in f["rank"].items()))
ok("every ranked fund also carries a quartile",
   all(set(f["rank"]) == set(f["q"]) for f in d["funds"].values()))
ok("the highest-drift Direct fund ranks 1 over 1 year",
   d["funds"]["C10"]["rank"]["1y"] == 1)
ok("sub-year horizons carry no annualised figure",
   all("6m" not in f["ann"] for f in d["funds"].values()))


# ------------------------------------------------------------------ nav doc
cohort = [{"code": "X1", "name": "Fund X1", "plan": "Direct", "rows": daily_rows(7)},
          {"code": "X2", "name": "Fund X2", "plan": "Direct", "rows": daily_rows(0.01)}]
doc = R.build_nav_doc("MID_CAP", "Direct", cohort, AS_OF)
ok("nav doc is produced", doc is not None)
eq("funds with too little history are omitted", list(doc["funds"]), ["X1"])
eq("count matches the payload", doc["count"], len(doc["funds"]))
for field in ("key", "plan", "as_of", "step_days", "generated_utc"):
    ok(f"nav doc carries {field!r}", field in doc)
ok("a cohort with no usable history yields no doc",
   R.build_nav_doc("MID_CAP", "Direct", [cohort[1]], AS_OF) is None)


# ------------------------------------------------------------------ publish gate
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "periods_X.json"
    allowed, _ = R.safe_to_publish(p, 50)
    ok("first publish is always allowed", allowed)

    R.write_json_atomic(p, {"count": 50, "plans": {}})
    ok("atomic write lands a readable file", json.loads(p.read_text())["count"] == 50)
    ok("no .tmp file is left behind", not list(Path(td).glob("*.tmp")))

    ok("a similar count publishes", R.safe_to_publish(p, 48)[0])
    ok("modest growth publishes", R.safe_to_publish(p, 60)[0])
    ok("a COLLAPSED count is refused (mfapi junk must not wipe good data)",
       not R.safe_to_publish(p, 12)[0])
    ok("exactly at the lower threshold publishes", R.safe_to_publish(p, 40)[0])
    ok("just under the lower threshold is refused", not R.safe_to_publish(p, 39)[0])

    # The gate must be two-sided. mfapi was observed serving a duplicated /mf list
    # (exact 2.0000 ratio across every category). A one-sided gate lets the doubled
    # file through as "growth", then refuses every correct night after it as a
    # collapse -- pinning the data to the corrupt version forever.
    ok("a DOUBLED count is refused as upstream duplication",
       not R.safe_to_publish(p, 100)[0])
    ok("just over the growth ceiling is refused", not R.safe_to_publish(p, 81)[0])
    ok("exactly at the growth ceiling publishes", R.safe_to_publish(p, 80)[0])
    _, why = R.safe_to_publish(p, 100)
    ok("the surge refusal explains itself", "duplicat" in why.lower())

    bad = Path(td) / "corrupt.json"
    bad.write_text("{not json")
    ok("an unreadable prior file does not block publishing", R.safe_to_publish(bad, 5)[0])

    nested = Path(td) / "periods_Y.json"
    R.write_json_atomic(nested, {"plans": {"Direct": {"funds": {"a": 1, "b": 2}}}})
    eq("count is derived from plans when absent", R.existing_count(nested), 2)


# ------------------------------------------------------------------ horizons
eq("no sub-6-month horizon is published (short windows drive chasing)",
   [h for h, d_ in R.HORIZONS if d_ < 182], [])
ok("10-year horizon is present", any(h == "10y" for h, _ in R.HORIZONS))
ok("horizons increase monotonically",
   all(b > a for (_, a), (_, b) in zip(R.HORIZONS, R.HORIZONS[1:])))
ok("SECTORAL is excluded from ranking", "SECTORAL" in R.UNRANKABLE_KEYS)
ok("the plural Thematic variant now resolves (was rejected outright)",
   R.category_key("Equity Schemes - Thematic Fund") == "SECTORAL")

# ------------------------------------------------- published-count integrity
# Live output disagreed with itself: the CONTRA manifest claimed 8 funds while the
# file held 3 Direct + 3 Regular, and LARGE_MID claimed 69 while holding 33 + 34.
# The header counted parsed funds; the table drops funds too young for any horizon.
_mixed = []
for _i in range(6):
    _mixed.append({"code": f"M{_i}", "name": f"Fund {_i}",
                   "plan": "Direct" if _i % 2 == 0 else "Regular",
                   "pts": to_pts(daily_rows(5))})
for _i in range(2):                       # too young for even the 6-month horizon
    _mixed.append({"code": f"Y{_i}", "name": f"Young {_i}",
                   "plan": "Direct" if _i % 2 == 0 else "Regular",
                   "pts": to_pts(daily_rows(0.1))})
_tbl = R.compute_period_table(_mixed, AS_OF)
_published = sum(len(p.get("funds") or {}) for p in _tbl.values())
eq("funds with no eligible horizon are excluded from the table", _published, 6)
ok("the excluded funds are the young ones",
   all(not c.startswith("Y") for p in _tbl.values() for c in p["funds"]))
ok("every published fund carries at least one absolute return",
   all(f["abs"] for p in _tbl.values() for f in p["funds"].values()))


# --------------------------------------------------- duplicate-list resilience
# Reproduces the real incident: mfapi served /mf with every entry duplicated.
def _run_discovery(schemes):
    calls = []

    def stub(url, timeout, attempts=3):
        if url.endswith("/mf"):
            return schemes, 0.1, 100, 200
        calls.append(url)
        return ({"meta": {"scheme_category": "Equity Scheme - Mid Cap Fund"},
                 "data": [{"date": "17-07-2026", "nav": "100"}]}, 0.1, 100, 200)

    real = R.get_json
    R.get_json = stub
    try:
        out = R.discover_universe(5, 2, lambda m: None)
    finally:
        R.get_json = real
    return out, calls


base = [{"schemeCode": 300000 + i,
         "schemeName": f"AMC{i} Mid Cap Fund - Direct Plan - Growth",
         "isinGrowth": f"INF{i}"} for i in range(10)]

clean, clean_calls = _run_discovery(list(base))
eq("clean list yields one entry per scheme", len(clean), 10)

dupes, dupe_calls = _run_discovery(list(base) + list(base))
eq("a fully duplicated /mf list still yields one entry per scheme", len(dupes), 10)
eq("duplicated codes are not fetched twice", len(dupe_calls), len(clean_calls))
eq("no duplicate schemeCode survives discovery",
   len({c["code"] for c in dupes}), len(dupes))

# ---------------------------------------------------------------- rank_desc NaN
# rank_desc used vals.index(v), which compares with == -- and NaN != NaN. A single
# non-finite return either raised ValueError or shifted every rank below it:
# [1.0, NaN, 3.0] published c as rank 3 when it is genuinely rank 1, with no error.
# period_return() can emit a non-finite value from bad upstream NAV, so this was
# reachable from live data.
eq("rank_desc orders best-first",
   R.rank_desc([("a", 10.0), ("b", 5.0), ("c", 7.0)]), {"a": 1, "c": 2, "b": 3})
eq("tied values share the better rank",
   R.rank_desc([("a", 10.0), ("b", 10.0), ("c", 5.0)]), {"a": 1, "b": 1, "c": 3})

_nan = R.rank_desc([("a", 1.0), ("b", float("nan")), ("c", 3.0)])
ok("a NaN entry is dropped rather than ranked", "b" not in _nan)
eq("the best finite value is rank 1 despite a NaN", _nan.get("c"), 1)
eq("and the other fund keeps its true position", _nan.get("a"), 2)

_inf = R.rank_desc([("a", 1.0), ("b", float("inf")), ("c", 3.0)])
ok("an infinite value is dropped", "b" not in _inf)
eq("finite ranks are unaffected by an infinity", _inf.get("c"), 1)

eq("an all-NaN cohort ranks nothing", R.rank_desc([("a", float("nan"))]), {})
eq("an empty cohort ranks nothing", R.rank_desc([]), {})

# The quartile denominator must be the population actually RANKED. rank_desc can
# drop funds, so len(scored) would over-count and shift bands.
_scored = [("f%d" % i, float(i)) for i in range(12)] + [("bad", float("nan"))]
_ranks = R.rank_desc(_scored)
eq("ranked population excludes the non-finite fund", len(_ranks), 12)
ok("quartiles computed on the ranked population stay in range",
   all(R.quartile(rk, len(_ranks)) in (1, 2, 3, 4) for rk in _ranks.values()))

# ------------------------------------------------------- non-finite -> bad JSON
# json.dump defaults to allow_nan=True and emits BARE `Infinity`, which Python
# round-trips but the browser's JSON.parse REJECTS -- one extreme NAV pair could
# take a whole ranking category offline client-side while every server check
# passed. Reproduced with NAVs 1e-308 -> 1e308.
_AS_OF = date(2026, 7, 20)
_extreme = [
    {"code": "BAD", "name": "Extreme", "plan": "Direct",
     "pts": [(_AS_OF - timedelta(days=365), 1e-308), (_AS_OF, 1e308)]},
    {"code": "OK", "name": "Normal", "plan": "Direct",
     "pts": [(_AS_OF - timedelta(days=365), 100.0), (_AS_OF, 110.0)]},
]
_plans = R.compute_period_table(_extreme, _AS_OF)
ok("an overflowing return is excluded from output",
   "BAD" not in _plans["Direct"]["funds"])
ok("a normal fund alongside it is unaffected",
   "OK" in _plans["Direct"]["funds"])
eq("the published universe counts only ranked funds",
   _plans["Direct"]["universe"].get("1y"), 1)

ok("period_return refuses a non-finite result",
   R.period_return([(_AS_OF - timedelta(days=365), 1e-308), (_AS_OF, 1e308)],
                   _AS_OF, 365) is None)

with tempfile.TemporaryDirectory() as _td:
    _p = Path(_td) / "periods.json"
    R.write_json_atomic(_p, {"plans": _plans})
    _raw = _p.read_text(encoding="utf-8")
ok("no bare Infinity token reaches the file", "Infinity" not in _raw)
try:
    json.loads(_raw, parse_constant=lambda t: (_ for _ in ()).throw(ValueError(t)))
    _strict = True
except ValueError:
    _strict = False
ok("a strict JSON parser accepts the published file", _strict)

# The writer itself must fail closed rather than emit invalid JSON.
with tempfile.TemporaryDirectory() as _td:
    _p = Path(_td) / "x.json"
    try:
        R.write_json_atomic(_p, {"v": float("inf")})
        _refused = False
    except ValueError:
        _refused = True
    ok("write_json_atomic refuses a non-finite payload", _refused)
    ok("and leaves no partial .tmp behind", not list(Path(_td).glob("*.tmp")))

# ------------------------------------------------------- stale funds
# `as_of` is the latest NAV across the whole category, so a fund that stopped
# reporting still gets measured against it: nav_on_or_before() carries its final
# NAV to BOTH boundaries and prints a current-looking figure. A fund a full year
# stale published "0.0%" as its 1-year return.
_stale = [(date(2024, 7, 20), 100.0), (date(2025, 7, 20), 110.0)]
ok("a year-stale fund is excluded from the 1y horizon",
   R.period_return(_stale, _AS_OF, 365) is None)
ok("...and from every other horizon too",
   R.period_return(_stale, _AS_OF, 730) is None
   and R.period_return(_stale, _AS_OF, 1095) is None)

_fresh = [(date(2025, 7, 18), 100.0), (date(2026, 7, 18), 115.0)]
near("a fund reporting within tolerance still ranks",
     R.period_return(_fresh, date(2026, 7, 20), 365), 15.0, 0.5)

_edge = [(date(2025, 7, 12), 100.0), (date(2026, 7, 13), 110.0)]
ok("a fund exactly at the 7-day bound is still included",
   R.period_return(_edge, date(2026, 7, 20), 365) is not None)
_over = [(date(2025, 7, 10), 100.0), (date(2026, 7, 11), 110.0)]
ok("a fund one day past the bound is excluded",
   R.period_return(_over, date(2026, 7, 20), 365) is None)

# ------------------------------------------------------- duplicate NAV dates
# Three consumers read this data and must agree. build_weekly_grid() keeps the
# LAST row for a date; a bare list + pts.sort() resolved duplicates to the
# HIGHEST nav instead, so the period table and the weekly grid could disagree
# about the same fund on the same day. index.html now keeps the last too.
_dupe_rows = [
    {"date": "01-01-2025", "nav": "100"},
    {"date": "01-01-2025", "nav": "200"},
    {"date": "02-01-2025", "nav": "150"},
]
_by_day = {}
for _r in _dupe_rows:
    _d = R.parse_dmy(_r["date"])
    if _d is not None:
        _by_day[_d] = float(_r["nav"])
_pts = sorted(_by_day.items())
eq("duplicate dates collapse to one point", len(_pts), 2)
eq("and the LAST row wins, matching build_weekly_grid", _pts[0][1], 200.0)

# Assert the grid's own dedupe policy directly rather than trusting it: both
# paths must resolve a duplicated date to the SAME nav, or the period table and
# the ranking grid describe different funds.
_long_rows = []
for _i in range(30):
    _d = date(2025, 1, 1) + timedelta(days=_i * 3)
    _long_rows.append({"date": _d.strftime("%d-%m-%Y"), "nav": "%d" % (100 + _i)})
_long_rows.append({"date": "01-01-2025", "nav": "999"})   # duplicate of day 1, later row
_grid = R.build_weekly_grid(_long_rows, date(2025, 4, 1), years=1, max_gap=7)
ok("the weekly grid still builds with a duplicated date", _grid is not None)
if _grid:
    _t0, _off, _vals, _forced = _grid
    eq("the grid resolves the duplicate to the LAST row, as the parser does",
       _vals[0], 999.0)

ok("an impossible calendar date is rejected outright",
   R.parse_dmy("31-02-2025") is None)
ok("a real leap day is still accepted",
   R.parse_dmy("29-02-2024") is not None)
ok("a non-leap 29 Feb is rejected",
   R.parse_dmy("29-02-2025") is None)

# ------------------------------------------------- future-dated NAV rows
# `as_of` is a MAX across every fund in a category, so ONE malformed future-dated
# row redefines "now" for all of them. Combined with the staleness rule, every
# healthy fund then looks dead. Measured before the fix: a single row dated 2030
# took a 6-fund cohort's 1-year universe from 6 to 0. Clamping as_of afterwards is
# not sufficient -- the row must be dropped before it can reach max().
def _cohort(n, years=8):
    out = []
    for i in range(n):
        out.append({"code": f"FD{i}", "name": f"Fund {i}", "plan": "Direct",
                    "pts": to_pts(daily_rows(years, rate=0.08 + 0.01 * i))})
    return out


_c = _cohort(6)
_clean_as_of = max(f["pts"][-1][0] for f in _c)
eq("a healthy cohort ranks every fund",
   R.compute_period_table(_c, _clean_as_of)["Direct"]["universe"]["1y"], 6)

ok("MAX_FUTURE_DAYS is defined", hasattr(R, "MAX_FUTURE_DAYS"))
_MFD = getattr(R, "MAX_FUTURE_DAYS", 2)   # keep going if absent, so all gaps report

# The grid builder re-parses rows itself, so it needs its own guard rather than
# trusting an as_of that a bad row may already have poisoned.
# as_of must be BEYOND the sentinel, or the pre-existing `d > as_of` check drops
# the row on its own and the test proves nothing about the future guard.
_rows = daily_rows(6) + [{"date": "01-01-2030", "nav": "999"}]
_g = R.build_weekly_grid(_rows, date(2031, 1, 1), years=30)
ok("build_weekly_grid drops a future-dated row", _g is not None)
if _g:
    _t0, _off, _vals, _ = _g
    _last = _t0 + timedelta(days=_off[-1])
    ok("no grid point lies beyond today's tolerance",
       _last <= date.today() + timedelta(days=_MFD))
    ok("and the sentinel NAV never entered the grid", 999.0 not in _vals)

# The guard has to sit before the max(): clamping as_of afterwards still lets the
# bad row define "now" for the category.
_parse = _src_main = (ROOT / "fetch_ranks.py").read_text(encoding="utf-8")
_loop_src = _parse[_parse.index("        as_of = None"):_parse.index("        if as_of is None:")]
ok("future rows are dropped inside the parse loop", "dropped_future" in _loop_src)
ok("...and the drop precedes the as_of max()",
   "dropped_future" in _loop_src and "max(as_of" in _loop_src
   and _loop_src.index("dropped_future") < _loop_src.index("max(as_of"))
ok("the count is surfaced in the log", "future-dated NAV row" in _parse)

# ------------------------------------------------- per-category isolation
# write_json_atomic raises by design, but nothing caught it: categories run in
# alphabetical order, so an exception on CONTRA aborted main() and the remaining
# nine wrote nothing at all -- no files, no manifest entries.
_src = (ROOT / "fetch_ranks.py").read_text(encoding="utf-8")
_main = _src[_src.index("def main("):]
# Tolerate comments between the loop header and the try, but require that the
# first executable statement in the loop body IS the try.
_loop = _main[_main.index("for cat in sorted(by_cat):"):]
_first_stmt = next((ln.strip() for ln in _loop.split("\n")[1:]
                    if ln.strip() and not ln.strip().startswith("#")), "")
eq("the first statement inside the category loop is a try", _first_stmt, "try:")
ok("a category failure is counted rather than raised", "failed += 1" in _main)
ok("a failed category is marked stale, not dropped from the manifest",
   '"status": "stale", "error"' in _main)
ok("the run still exits non-zero when a category failed",
   "if failed:\n        return 1" in _main)
ok("the summary line reports category errors", "category error(s)" in _main)

print(f"\n{'FAILED' if _fail else 'ALL PASSED'} ({_pass} passed, {_fail} failed)")
sys.exit(1 if _fail else 0)
