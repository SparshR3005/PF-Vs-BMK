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
rows = daily_rows(6)
grid = R.build_weekly_grid(rows, AS_OF)
ok("grid builds from a 6-year daily history", grid is not None)
t0, offs, vals = grid
eq("grid offsets and values are the same length", len(offs), len(vals))
ok("grid is roughly weekly (~52/yr)", 280 <= len(vals) <= 330)
ok("grid offsets strictly increase", all(b > a for a, b in zip(offs, offs[1:])))
ok("no gap exceeds 7 days, so navOnOrAfter(d,7) can always place an installment",
   all(b - a <= 7 for a, b in zip(offs, offs[1:])))
ok("grid values are positive", all(v > 0 for v in vals))
ok("t0 is a real trading day, not a synthesised boundary",
   t0 in {p[0] for p in to_pts(rows)})

ok("a fund with almost no history yields no grid",
   R.build_weekly_grid(daily_rows(0.02), AS_OF) is None)
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

print(f"\n{'FAILED' if _fail else 'ALL PASSED'} ({_pass} passed, {_fail} failed)")
sys.exit(1 if _fail else 0)
