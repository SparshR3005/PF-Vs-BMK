"""Scheme-continuation chain: splice gates, and the JS/Python drift guard.

Run: python3 tests/test_mergers.py
"""
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mf_mergers as mm  # noqa: E402


def _series(start, n, nav0=100.0, step=1.0):
    """n daily points from `start`, ascending."""
    return [(start + timedelta(days=i), nav0 + i * step) for i in range(n)]


# ------------------------------------------------------------------ splice gate
def test_a_clean_rename_splices():
    old = _series(date(2022, 11, 1), 25)             # ends 2022-11-25 @ 124.0
    new = [(date(2022, 11, 28), 124.0), (date(2022, 11, 29), 125.0)]
    joined, problem = mm.splice_series(old, new, "2022-11-28")
    assert problem == "", problem
    assert joined[0][0] == date(2022, 11, 1)
    assert joined[-1][0] == date(2022, 11, 29)
    assert len(joined) == 27


def test_the_new_code_wins_on_overlapping_dates():
    # A date present in BOTH must appear once, taking the surviving code's value —
    # two rows for one day make navOnOrAfter and navOnOrBefore disagree, which is
    # how a SIP ends up buying at one NAV and being valued at another the same day.
    old = _series(date(2022, 11, 20), 10, nav0=100.0, step=0.0)   # through 2022-11-29
    new = [(date(2022, 11, 28), 100.0), (date(2022, 11, 29), 101.0)]
    joined, problem = mm.splice_series(old, new, "2022-11-28")
    assert problem == ""
    dates = [d for d, _ in joined]
    assert len(dates) == len(set(dates)), "a date appears twice across the join"
    assert dict(joined)[date(2022, 11, 29)] == 101.0


def test_a_ratio_merger_is_refused():
    # The transferor case: units swapped, NAV restated. Splicing it would weld a
    # step-change into the history and invent performance that never happened.
    old = _series(date(2022, 11, 1), 25, nav0=100.0, step=0.0)
    new = [(date(2022, 11, 28), 210.0)]
    joined, problem = mm.splice_series(old, new, "2022-11-28")
    assert problem, "a 110% jump must be refused"
    assert "NAV jumps" in problem
    assert joined == new, "a refused splice must return the new series intact"


def test_a_small_market_move_across_the_join_is_allowed():
    old = _series(date(2022, 11, 1), 25, nav0=100.0, step=0.0)
    new = [(date(2022, 11, 28), 102.0)]           # +2.0%, inside the 3% tolerance
    assert mm.splice_problem(old, new, "2022-11-28") == ""


def test_a_move_just_past_tolerance_is_refused():
    old = _series(date(2022, 11, 1), 25, nav0=100.0, step=0.0)
    new = [(date(2022, 11, 28), 104.0)]           # +4.0%
    assert "NAV jumps" in mm.splice_problem(old, new, "2022-11-28")


def test_a_long_hole_at_the_join_is_refused():
    # A gap wider than the 7-day SIP placement window silently swallows any
    # instalment scheduled inside it.
    old = _series(date(2022, 10, 1), 5, nav0=100.0, step=0.0)     # ends 2022-10-05
    new = [(date(2022, 11, 28), 100.0)]
    problem = mm.splice_problem(old, new, "2022-11-28")
    assert "hole" in problem, problem


def test_missing_or_empty_sides_are_refused_not_crashed():
    old = _series(date(2022, 11, 1), 5)
    new = [(date(2022, 11, 28), 100.0)]
    assert mm.splice_problem([], new, "2022-11-28")
    assert mm.splice_problem(old, [], "2022-11-28")
    assert mm.splice_problem(None, new, "2022-11-28")
    # Old series entirely after the splice date: nothing to prepend.
    assert mm.splice_problem([(date(2023, 1, 1), 100.0)], new, "2022-11-28")


def test_non_positive_nav_at_the_join_is_refused():
    old = [(date(2022, 11, 25), 0.0)]
    new = [(date(2022, 11, 28), 100.0)]
    assert mm.splice_problem(old, new, "2022-11-28")


def test_chained_from_resolves_and_is_none_for_unchained_codes():
    assert mm.chained_from("151034") == "112496"
    assert mm.chained_from(151034) == "112496"      # int or str
    assert mm.chained_from("118049") is None        # L&T Large and Midcap: NOT chained
    assert mm.chained_from("999999") is None


# ----------------------------------------------------- chain map self-consistency
def test_every_chain_entry_is_well_formed():
    for new_code, link in mm.CHAIN.items():
        assert new_code.isdigit(), new_code
        assert link["from"].isdigit(), link
        assert new_code != link["from"], "a scheme cannot chain to itself"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", link["splice"]), link


def test_no_retired_code_feeds_two_survivors():
    # Two survivors claiming one predecessor would double-count the same history.
    sources = [l["from"] for l in mm.CHAIN.values()]
    assert len(sources) == len(set(sources)), sources


def test_no_chain_forms_a_cycle_or_a_multi_hop():
    # A retired code that is itself a survivor would need recursive splicing, which
    # neither implementation does. Catch it here rather than in someone's returns.
    for link in mm.CHAIN.values():
        assert link["from"] not in mm.CHAIN, f"{link['from']} is both retired and surviving"


def test_the_chain_matches_the_published_grids():
    """The surviving codes must actually be the ones whose history starts late."""
    path = ROOT / "data" / "ranks"
    if not path.is_dir():
        return
    starts = {}
    for f in path.glob("navs_*.json"):
        for code, entry in (json.loads(f.read_text(encoding="utf-8")).get("funds") or {}).items():
            starts[code] = entry.get("t0")
    for new_code, link in mm.CHAIN.items():
        t0 = starts.get(new_code)
        if t0 is None:
            continue          # not in a published category, nothing to check
        # Before the chain runs, the survivor's grid starts at (or after) the
        # merger. Once it runs, t0 moves back — both are consistent with the map;
        # a t0 well AFTER the splice with no chain applied is the broken state.
        # OUTCOME, not permission. The old assertion was
        #     t0 <= splice or t0 == splice
        # whose second clause is subsumed by the first, and which PASSES on
        # t0 == splice -- precisely the UN-spliced state. It proved the wiring
        # existed and permitted the wiring to be inert, which is what happened: the
        # chain shipped, the grids kept t0 == 2022-11-28, and 17 merger tests stayed
        # green while Insights told the user its history did not cover a window the
        # Portfolio tab was already showing.
        #
        # A splice that ran leaves t0 STRICTLY earlier than the splice date. If a
        # chain can never splice -- no history under the retired code -- the entry
        # does not belong in CHAIN at all; an inert entry is a lie in config, not a
        # state to tolerate.
        assert t0 < link["splice"], (
            f"{new_code} grid starts {t0}, not earlier than its splice date "
            f"{link['splice']} - the chain is configured but did not run. Check "
            f"the ranks log for 'chained {new_code}<-{link['from']}' or 'CHAIN REFUSED'.")


# -------------------------------------------------------------- JS drift guard
def test_index_html_chain_matches_this_module():
    """index.html carries its own copy of the map because it cannot import Python.
    Two copies drift; this test is what stops them. Same guard mf_universe.py gives
    the category filters."""
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    block = re.search(r"const SCHEME_CHAIN = \{(.*?)\n\};", html, re.S)
    assert block, "SCHEME_CHAIN not found in index.html"

    js = {}
    for m in re.finditer(
        r'"(\d+)":\s*\{\s*from:"(\d+)",\s*splice:"(\d{4}-\d{2}-\d{2})"', block.group(1)):
        js[m.group(1)] = {"from": m.group(2), "splice": m.group(3)}

    assert js, "no entries parsed out of SCHEME_CHAIN"
    py = {k: {"from": v["from"], "splice": v["splice"]} for k, v in mm.CHAIN.items()}
    assert js == py, (
        "index.html and mf_mergers.py disagree — the Portfolio tab would splice a "
        f"holding the nightly ranks job does not.\n  index.html: {js}\n  python    : {py}")


def test_index_html_tolerances_match():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    ratio = re.search(r"const MAX_SPLICE_RATIO_DRIFT\s*=\s*([\d.]+)", html)
    gap = re.search(r"const MAX_SPLICE_GAP_DAYS\s*=\s*(\d+)", html)
    assert ratio and gap, "splice tolerances not found in index.html"
    assert float(ratio.group(1)) == mm.MAX_SPLICE_RATIO_DRIFT
    assert int(gap.group(1)) == mm.MAX_SPLICE_GAP_DAYS


def test_index_html_actually_uses_the_chain():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "getDetailChained(spec.code)" in html, \
        "computeScheme must fetch through the chain, or the map is dead code"


def test_fetch_ranks_applies_the_chain():
    src = (ROOT / "fetch_ranks.py").read_text(encoding="utf-8")
    assert "mf_mergers.chained_from" in src, \
        "the nightly job must splice too, or Insights disagrees with Portfolio"
    assert "splice_series" in src


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                failed += 1
                print(f"  FAIL  {name}: {e}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  FAIL  {name}: escaped {type(e).__name__}: {e}")
    print(f"\n{'FAILED' if failed else 'ALL PASSED'} ({failed} failure(s))")
    sys.exit(1 if failed else 0)