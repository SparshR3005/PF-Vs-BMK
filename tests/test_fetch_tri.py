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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
