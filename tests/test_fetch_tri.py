#!/usr/bin/env python3
"""
Regression tests for fetch_tri.py.

Run:  python3 -m pytest tests/ -q          (or: python3 tests/test_fetch_tri.py)

These exist because v4's CHANGELOG claimed the continuity gate had been
"unit-tested against the audit's adversarial fixture" while no test file was
shipped — and the gate demonstrably accepted an end-date regression. Every
test below pins a specific finding so the claim can never drift from the code
again.
"""
import datetime
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# fetch_tri.py imports playwright at module scope, but nothing under test drives a
# real browser — the timeout tests use fakes. Rather than force CI to install a
# ~100MB browser library to exercise date arithmetic, install a minimal stub when
# playwright is genuinely absent. If the real package IS present we use it, so this
# never masks a broken install in the fetch job (which does need Chromium).
if importlib.util.find_spec("playwright") is None:
    _pw = types.ModuleType("playwright")
    _sync = types.ModuleType("playwright.sync_api")

    class TimeoutError(Exception):        # mirrors playwright.sync_api.TimeoutError
        pass

    def sync_playwright(*a, **k):         # never called by these tests
        raise RuntimeError("playwright is stubbed; the fetch job installs the real one")

    _sync.TimeoutError = TimeoutError
    _sync.sync_playwright = sync_playwright
    _pw.sync_api = _sync
    sys.modules["playwright"] = _pw
    sys.modules["playwright.sync_api"] = _sync

spec = importlib.util.spec_from_file_location("fetch_tri", ROOT / "fetch_tri.py")
ft = importlib.util.module_from_spec(spec)
sys.modules["fetch_tri"] = ft
spec.loader.exec_module(ft)

BASE = datetime.date(2020, 1, 1)


def make_series(n, start=BASE, step=0.01, base_val=100.0):
    return {(start + datetime.timedelta(days=i)).isoformat(): base_val + i * step
            for i in range(n)}


def doc(series):
    return {"series": series, "start": min(series), "end": max(series)}


# ---------------------------------------------------------------- #1 end-date
def test_rejects_end_date_regression():
    """#1 THE adversarial fixture: fresh, smooth, 99.8% of rows retained, every
    committed value identical — but the tail is 4 days short. Every other gate
    passes it; only the end-date check catches it."""
    old = doc(make_series(2000))
    new = doc(dict(list(old["series"].items())[:-4]))
    problem = ft.continuity_problem(new, old)
    assert problem, "end-date regression must be rejected"
    assert "earlier than committed end" in problem
    assert len(new["series"]) / len(old["series"]) > ft.CONT_MIN_KEEP_FRACTION, \
        "fixture must survive the row-count gate, or it isn't testing #1"


def test_rejects_single_day_regression():
    """Even one lost day is lost client-facing history."""
    old = doc(make_series(2000))
    new = doc(dict(list(old["series"].items())[:-1]))
    assert "earlier than committed end" in ft.continuity_problem(new, old)


def test_accepts_identical_series():
    old = doc(make_series(2000))
    assert ft.continuity_problem(old, old) == ""


def test_accepts_normal_forward_growth():
    """The happy path must stay open: same history + new days appended."""
    old_s = make_series(2000)
    new_s = dict(old_s)
    for i in range(2000, 2005):
        new_s[(BASE + datetime.timedelta(days=i)).isoformat()] = 100 + i * 0.01
    assert ft.continuity_problem(doc(new_s), doc(old_s)) == ""


def test_first_run_has_nothing_to_protect():
    assert ft.continuity_problem(doc(make_series(500)), None) == ""


# ------------------------------------------------- pre-existing gates (guard)
def test_rejects_later_start_lost_history():
    old = doc(make_series(2000))
    new = doc(make_series(1990, start=BASE + datetime.timedelta(days=10)))
    assert "later than committed start" in ft.continuity_problem(new, old)


def test_rejects_material_shrink():
    """Same start AND same end, but half the interior points are missing — so this
    isolates the row-count gate rather than tripping the start/end checks first."""
    old_s = make_series(2000)
    keys = list(old_s)
    # keep first, last, and every other interior point -> ~50% retained
    keep = {keys[0], keys[-1]} | set(keys[1:-1:2])
    new_s = {d: v for d, v in old_s.items() if d in keep}
    assert min(new_s) == min(old_s) and max(new_s) == max(old_s)
    assert "row count shrank" in ft.continuity_problem(doc(new_s), doc(old_s))


def test_rejects_value_drift_wrong_index():
    """A different index spliced in under the same name."""
    old_s = make_series(2000)
    new_s = {d: v * 1.5 for d, v in old_s.items()}
    assert "likely a different series" in ft.continuity_problem(doc(new_s), doc(old_s))


# ---------------------------------------------------------------- #5 timeouts
def test_fetch_index_survives_navigation_timeout():
    """#5 A PWTimeout from prime()/goto must degrade to a failed index, never
    propagate out and abort every index queued behind it."""
    calls = {"n": 0}

    class FakePage:
        def goto(self, *a, **k): raise ft.PWTimeout("nav timeout")
        def reload(self, *a, **k): raise ft.PWTimeout("nav timeout")
        def wait_for_load_state(self, *a, **k): pass
        def wait_for_timeout(self, *a, **k): pass
        def evaluate(self, *a, **k): raise AssertionError("unreachable")
        mouse = type("M", (), {"move": lambda self, x, y: None})()

    class FakeCtx:
        def cookies(self): return [{"name": "ak_bmsc"}]

    orig_sleep = ft.time.sleep
    ft.time.sleep = lambda s: calls.__setitem__("n", calls["n"] + 1)
    try:
        assert ft.fetch_index(FakePage(), FakeCtx(), "NIFTY 50", "01-Jan-2026") is None
    finally:
        ft.time.sleep = orig_sleep
    assert calls["n"] >= 1, "should have retried with backoff, not raised"


def test_fetch_index_survives_evaluate_timeout():
    """#5 Same for an in-page fetch timeout."""
    class FakePage:
        def goto(self, *a, **k): pass
        def reload(self, *a, **k): pass
        def wait_for_load_state(self, *a, **k): pass
        def wait_for_timeout(self, *a, **k): pass
        def evaluate(self, *a, **k): raise ft.PWTimeout("evaluate timeout")
        mouse = type("M", (), {"move": lambda self, x, y: None})()

    class FakeCtx:
        def cookies(self): return [{"name": "ak_bmsc"}]

    orig_sleep = ft.time.sleep
    ft.time.sleep = lambda s: None
    try:
        assert ft.fetch_index(FakePage(), FakeCtx(), "NIFTY 50", "01-Jan-2026") is None
    finally:
        ft.time.sleep = orig_sleep


def test_fetch_index_survives_generic_browser_error():
    class FakePage:
        def goto(self, *a, **k): raise RuntimeError("target closed")
        def reload(self, *a, **k): raise RuntimeError("target closed")
        def wait_for_load_state(self, *a, **k): pass
        def wait_for_timeout(self, *a, **k): pass
        def evaluate(self, *a, **k): raise AssertionError("unreachable")
        mouse = type("M", (), {"move": lambda self, x, y: None})()

    class FakeCtx:
        def cookies(self): return [{"name": "ak_bmsc"}]

    orig_sleep = ft.time.sleep
    ft.time.sleep = lambda s: None
    try:
        assert ft.fetch_index(FakePage(), FakeCtx(), "NIFTY 50", "01-Jan-2026") is None
    finally:
        ft.time.sleep = orig_sleep


# ---------------------------------------------------------------- gap-scaled move
# validate_series() used to check the single-day move ONLY when the gap was <= 4
# days, leaving every longer gap completely unpoliced. A corrupt value landing
# after a holiday week therefore validated clean and would have been published,
# silently rewriting every XIRR computed against that series. The tolerance now
# scales with the gap but is never switched off.
def _series(n=300, start_val=1000.0, drift=1.0005, start=None):
    from datetime import date as _d, timedelta as _td
    start = start or _d(2020, 1, 1)
    out, cur = {}, start_val
    for i in range(n):
        out[(start + _td(days=i)).isoformat()] = cur
        cur *= drift
    return out


def _append(series, days_after, factor):
    from datetime import datetime as _dt, timedelta as _td
    last = sorted(series)[-1]
    last_d = _dt.strptime(last, "%Y-%m-%d").date()
    out = dict(series)
    out[(last_d + _td(days=days_after)).isoformat()] = series[last] * factor
    return out


def test_clean_series_validates():
    assert ft.validate_series({"series": _series()}) == ""


def test_rejects_crash_after_long_gap():
    """The bug: a 90% collapse after a 10-day gap used to pass unchecked."""
    doc = {"series": _append(_series(), 10, 0.10)}
    problem = ft.validate_series(doc)
    assert problem, "a 90% move over a 10-day gap must be refused"
    assert "implausible" in problem


def test_rejects_crash_just_past_the_old_four_day_cutoff():
    """A 5-day gap sat one day outside the old check and was unbounded."""
    doc = {"series": _append(_series(), 5, 0.60)}
    assert ft.validate_series(doc), "a 40% move over 5 days must be refused"


def test_allows_real_move_over_a_holiday_gap():
    """Scaling must not become a blanket ban: 8% over 10 days is ordinary."""
    doc = {"series": _append(_series(), 10, 0.92)}
    assert ft.validate_series(doc) == ""


def test_allows_worst_real_single_day_crash():
    """2020's worst session was ~13%; it must still publish."""
    doc = {"series": _append(_series(), 1, 0.87)}
    assert ft.validate_series(doc) == ""


def test_still_rejects_large_single_day_move():
    doc = {"series": _append(_series(), 1, 0.64)}
    assert ft.validate_series(doc), "a 36% single-day move must be refused"


def test_gap_move_never_exceeds_absolute_ceiling():
    """However long the gap, the tolerance is clamped by MAX_GAP_MOVE."""
    doc = {"series": _append(_series(), 400, 0.05)}
    assert ft.validate_series(doc), "a 95% move must be refused at any gap"


# ------------------------------------------------------- continuity: full overlap
# Two holes were found by adversarial fixtures and are pinned here.
#
# (a) The value comparison ran only `if len(common) >= 20`, so a series sharing
#     FEWER than 20 dates skipped it entirely. A fully DISJOINT replacement --
#     earlier start, later end, similar row count, ZERO dates in common, values
#     5x different -- returned '' and would have overwritten good history.
#
# (b) It then sampled ~40 points via common[::step]. On the real NIFTY500 file
#     (6,857 points) step=171, leaving 99.4% of committed history unchecked. A
#     published value could change by 900% simply by not being sampled.
def _doc(series):
    return {"start": min(series), "end": max(series), "series": series}


def test_rejects_fully_disjoint_replacement():
    from datetime import date as _d, timedelta as _td
    start = _d(2020, 1, 1)
    old = {(start + _td(days=2 * i)).isoformat(): 100.0 + i for i in range(100)}
    ns = start - _td(days=1)
    new = {(ns + _td(days=2 * i)).isoformat(): 500.0 + i for i in range(102)}
    assert not (set(old) & set(new)), "fixture must be disjoint"
    problem = ft.continuity_problem(_doc(new), _doc(old))
    assert problem, "a zero-overlap replacement must be refused"
    assert "survive" in problem or "different series" in problem


def test_rejects_partial_overlap_below_floor():
    """Half the committed dates vanishing is still a different series."""
    from datetime import date as _d, timedelta as _td
    start = _d(2020, 1, 1)
    old = {(start + _td(days=i)).isoformat(): 100.0 + i for i in range(100)}
    new = {d: v for i, (d, v) in enumerate(sorted(old.items())) if i % 2 == 0}
    new[(start + _td(days=200)).isoformat()] = 300.0
    assert ft.continuity_problem(_doc(new), _doc(old))


def test_rejects_mutation_the_old_sampler_missed():
    """Index 1 fell outside the old step-2 sample; a 10x change passed."""
    from datetime import date as _d, timedelta as _td
    start = _d(2020, 1, 1)
    old = {(start + _td(days=i)).isoformat(): 100.0 + i for i in range(100)}
    new = dict(old)
    new[(start + _td(days=1)).isoformat()] *= 10
    problem = ft.continuity_problem(_doc(new), _doc(old))
    assert problem, "a 10x change on an unsampled date must be refused"
    assert "changed" in problem


def test_rejects_mutation_anywhere_in_a_large_series():
    """Every overlapping point is compared, not a sample -- check several."""
    from datetime import date as _d, timedelta as _td
    start = _d(2000, 1, 1)
    old = {(start + _td(days=i)).isoformat(): 1000.0 + i for i in range(4000)}
    for idx in (1, 7, 1234, 2999, 3998):
        new = dict(old)
        key = (start + _td(days=idx)).isoformat()
        new[key] = old[key] * 1.5
        assert ft.continuity_problem(_doc(new), _doc(old)), \
            f"a 50% change on index {idx} must be refused"


def test_rejects_missing_prior_end_date():
    """The committed terminal date is what every published XIRR was priced on."""
    from datetime import date as _d, timedelta as _td
    start = _d(2020, 1, 1)
    old = {(start + _td(days=i)).isoformat(): 100.0 + i for i in range(100)}
    new = dict(old)
    del new[max(old)]
    new[(start + _td(days=100)).isoformat()] = 200.0
    new[(start + _td(days=101)).isoformat()] = 201.0
    assert ft.continuity_problem(_doc(new), _doc(old))


def test_accepts_a_genuine_daily_extension():
    """The gate must not become a blanket ban: appending a day is normal."""
    from datetime import date as _d, timedelta as _td
    start = _d(2020, 1, 1)
    old = {(start + _td(days=i)).isoformat(): 100.0 + i for i in range(100)}
    new = dict(old)
    new[(start + _td(days=100)).isoformat()] = 200.0
    assert ft.continuity_problem(_doc(new), _doc(old)) == ""


def test_accepts_tiny_revisions_within_tolerance():
    """Providers do restate slightly; sub-1% drift stays acceptable."""
    from datetime import date as _d, timedelta as _td
    start = _d(2020, 1, 1)
    old = {(start + _td(days=i)).isoformat(): 100.0 + i for i in range(100)}
    new = {d: v * 1.005 for d, v in old.items()}
    assert ft.continuity_problem(_doc(new), _doc(old)) == ""


def test_first_run_still_publishes():
    from datetime import date as _d, timedelta as _td
    start = _d(2020, 1, 1)
    new = {(start + _td(days=i)).isoformat(): 100.0 + i for i in range(100)}
    assert ft.continuity_problem(_doc(new), None) == ""


def test_rejects_non_finite_committed_value():
    from datetime import date as _d, timedelta as _td
    start = _d(2020, 1, 1)
    old = {(start + _td(days=i)).isoformat(): 100.0 + i for i in range(100)}
    new = dict(old)
    new[(start + _td(days=5)).isoformat()] = float("inf")
    assert ft.continuity_problem(_doc(new), _doc(old))

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
            except Exception as e:
                # An escaping exception is itself the failure this suite guards
                # against (see #5): fetch_index must never let one propagate.
                failed += 1
                print(f"  FAIL  {name}: escaped {type(e).__name__}: {e}")
    print(f"\n{'FAILED' if failed else 'ALL PASSED'} ({failed} failure(s))")
    sys.exit(1 if failed else 0)