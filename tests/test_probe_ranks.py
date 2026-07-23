#!/usr/bin/env python3
"""
Tests for probe_ranks.py.

The important ones are the DRIFT GUARDS. probe_ranks.py re-implements filters that
already exist in index.html, because the nightly job is Python and the client is
JavaScript. Two copies of the same rule is a bug waiting to happen: someone adds a
SEBI category or a non-equity token to index.html, the Python keeps the old list,
and the universe the fetcher publishes silently stops matching the universe the UI
can rank against. Nobody notices, because nothing errors -- funds just quietly go
missing from rankings.

So these tests parse index.html and assert the two agree, exactly. If you edit one
list, this suite fails until you edit the other.

No network access is required or used.

    python tests/test_probe_ranks.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import probe_ranks as P  # noqa: E402

HTML = (ROOT / "index.html").read_text(encoding="utf-8")

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
    ok(f"{label}", got == want)
    if got != want:
        print(f"          got  {got!r}")
        print(f"          want {want!r}")


# ------------------------------------------------------------------ drift guards
def js_array(const_name):
    """Pull a JS array-of-strings constant out of index.html."""
    m = re.search(const_name + r"\s*=\s*\[(.*?)\]\s*;", HTML, re.S)
    if not m:
        raise AssertionError(f"{const_name} not found in index.html")
    return re.findall(r'"([^"]*)"', m.group(1))


def js_object(const_name):
    """Pull a JS object-literal of string->string out of index.html."""
    m = re.search(const_name + r"\s*=\s*\{(.*?)\n\}\s*;", HTML, re.S)
    if not m:
        raise AssertionError(f"{const_name} not found in index.html")
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return dict(re.findall(r'"([^"]*)"\s*:\s*"([^"]*)"', body))


print("Run python tests/test_probe_ranks.py")

eq("NON_EQUITY_NAME_TOKENS matches index.html exactly",
   P.NON_EQUITY_NAME_TOKENS, js_array("NON_EQUITY_NAME_TOKENS"))

eq("CATEGORY_CANON matches index.html exactly",
   P.CATEGORY_CANON, js_object("CATEGORY_CANON"))

# The income-option tokens live inline in loadSchemeList() rather than in a named
# constant, so assert on the source text that produces them.
for tok in P.INCOME_TOKENS:
    ok(f"income token {tok!r} is also excluded by index.html",
       f'n.includes("{tok}")' in HTML)

ok("index.html still gates on the Growth option",
   'const isGrowth=n.includes("growth")' in HTML)

ok("index.html still null-checks isinGrowth for legacy records",
   "hasGrowthIsinField" in HTML and "isinGrowth" in HTML)


# ------------------------------------------------------------------ category gate
eq("large cap resolves", P.category_key("Equity Scheme - Large Cap Fund"), "LARGE_CAP")
eq("large & mid resolves", P.category_key("Equity Scheme - Large & Mid Cap Fund"), "LARGE_MID")
eq("elss bare form resolves", P.category_key("ELSS"), "ELSS")
eq("sectoral resolves (then excluded downstream)",
   P.category_key("Equity Scheme - Sectoral/ Thematic"), "SECTORAL")
eq("whitespace/case is normalised",
   P.category_key("  EQUITY   SCHEME  -  MID CAP FUND "), "MID_CAP")

for junk in ["1", "1099 Days", "Growth", "Income", "IDF", "Payout",
             "Formerly Known as IIFL Mutual Fund", "", None]:
    ok(f"junk category {junk!r} fails closed", P.category_key(junk) is None)

for non_equity in ["Hybrid Scheme - Arbitrage Fund", "Debt Scheme - Medium Duration Fund",
                   "Other Scheme - Index Funds", "Other Scheme - FoF Overseas",
                   "Hybrid Scheme - Equity Savings"]:
    ok(f"non-equity {non_equity!r} rejected", P.category_key(non_equity) is None)

ok("SECTORAL is marked unrankable", "SECTORAL" in P.UNRANKABLE_KEYS)


# ------------------------------------------------------------------ name filters
ok("liquid fund flagged non-equity", P.name_looks_non_equity("xyz liquid fund - direct plan - growth"))
ok("arbitrage flagged non-equity", P.name_looks_non_equity("xyz arbitrage fund - direct - growth"))
ok("index fund flagged non-equity", P.name_looks_non_equity("xyz nifty 50 index fund - direct - growth"))
ok("etf flagged non-equity", P.name_looks_non_equity("xyz nifty etf"))
ok("a real flexi cap is NOT flagged",
   not P.name_looks_non_equity("parag parikh flexi cap fund - direct plan - growth"))
ok("a real mid cap is NOT flagged",
   not P.name_looks_non_equity("hdfc mid cap opportunities fund - direct plan - growth"))


# ------------------------------------------------------------------ plan split
eq("direct detected", P.classify_plan("hdfc mid cap fund - direct plan - growth"), "Direct")
eq("regular detected", P.classify_plan("hdfc mid cap fund - regular plan - growth"), "Regular")
eq("name mentioning neither falls to Regular (mirrors client else-branch)",
   P.classify_plan("hdfc mid cap fund - growth"), "Regular")


# ------------------------------------------------------------------ list unwrapping
eq("bare array is complete", P.unwrap_list([{"a": 1}]), ([{"a": 1}], True))
eq("wrapped .data without paging is complete",
   P.unwrap_list({"data": [{"a": 1}]}), ([{"a": 1}], True))
got, complete = P.unwrap_list({"data": [{"a": 1}], "page": 1, "totalPages": 5})
ok("paginated wrapper is flagged incomplete", got == [{"a": 1}] and complete is False)
got, complete = P.unwrap_list({"data": [{"a": 1}], "hasMore": True})
ok("hasMore wrapper is flagged incomplete", complete is False)
eq("garbage shape returns None", P.unwrap_list({"nope": 1}), (None, False))


# ------------------------------------------------------------------ funnel logic
def run_funnel(schemes):
    """Mirror stage1's filter loop without touching the network."""
    has_isin = any(isinstance(s, dict) and "isinGrowth" in s for s in schemes)
    kept = []
    for s in schemes:
        if not isinstance(s, dict) or not s.get("schemeName") or not s.get("schemeCode"):
            continue
        n = str(s["schemeName"]).lower()
        if "growth" not in n:
            continue
        if any(t in n for t in P.INCOME_TOKENS):
            continue
        if has_isin and not str(s.get("isinGrowth") or "").strip():
            continue
        if P.name_looks_non_equity(n):
            continue
        kept.append(s["schemeCode"])
    return kept


sample = [
    {"schemeCode": 1, "schemeName": "A Flexi Cap Fund - Direct Plan - Growth", "isinGrowth": "INF1"},
    {"schemeCode": 2, "schemeName": "A Flexi Cap Fund - Direct Plan - IDCW", "isinGrowth": "INF2"},
    {"schemeCode": 3, "schemeName": "A Liquid Fund - Direct Plan - Growth", "isinGrowth": "INF3"},
    {"schemeCode": 4, "schemeName": "A Mid Cap Fund - Regular Plan - Growth", "isinGrowth": "INF4"},
    {"schemeCode": 5, "schemeName": "A Legacy Fund - Direct Plan - Growth", "isinGrowth": ""},
    {"schemeCode": 6, "schemeName": "A Nifty Index Fund - Direct Plan - Growth", "isinGrowth": "INF6"},
    {"schemeCode": 7, "schemeName": "A Fund - Direct Plan - Dividend Payout", "isinGrowth": "INF7"},
    {"schemeName": "missing code - growth", "isinGrowth": "INF8"},
]
eq("funnel keeps exactly the growth/equity/live schemes", run_funnel(sample), [1, 4])

no_isin = [{"schemeCode": 9, "schemeName": "B Small Cap Fund - Direct Plan - Growth"}]
eq("blank-isin rule is skipped when the field is absent entirely",
   run_funnel(no_isin), [9])


# ------------------------------------------------------- sampling bias regression
# The first version of stage2 sampled with a fixed stride:
#     survivors[::len(survivors)//sample_n]
# That silently assumes the scheme list has no periodic structure. mfapi's does --
# schemes arrive grouped by AMC, and AMCs register funds in contiguous blocks -- so
# a stride landing in step with those blocks samples one slice of the market and
# reports it as the whole. It was caught by a fixture whose categories varied with
# code % 5 against a stride of exactly 5: every sampled fund came back rejected.
# stage2 now uses a seeded random sample. This guards that it stays that way.
def strided(pop, k):
    return pop[::max(1, len(pop) // max(1, k))][:k]


population = list(range(400))
stride_sample = strided(population, 80)
ok("a fixed stride produces a degenerate sample (all one residue class)",
   len({x % 5 for x in stride_sample}) == 1)

import random as _random  # noqa: E402
rand_sample = _random.Random(20260723).sample(population, 80)
ok("the seeded random sample spreads across residue classes",
   len({x % 5 for x in rand_sample}) == 5)

ok("stage2 source uses rng.sample, not a stride",
   "rng.sample(survivors" in (ROOT / "probe_ranks.py").read_text(encoding="utf-8"))
ok("stage2 source no longer contains the strided slice",
   "survivors[::" not in (ROOT / "probe_ranks.py").read_text(encoding="utf-8"))

eq("the seeded sample is reproducible across runs",
   _random.Random(20260723).sample(population, 10),
   _random.Random(20260723).sample(population, 10))


print(f"\n{'FAILED' if _fail else 'ALL PASSED'} ({_pass} passed, {_fail} failed)")
sys.exit(1 if _fail else 0)
