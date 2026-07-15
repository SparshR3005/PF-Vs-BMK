# Tests

```bash
python3 tests/test_fetch_tri.py     # 11 tests — TRI continuity gate, fetch resilience
node    tests/test_app.js           # 33 assertions — export, import, retry, storage, search
node    tests/test_matching.js      # 79 assertions — scheme matching, SEBI category guard,
                                    #                 benchmark mapping, rename aliases
```

No test runner, no npm install. Plain Python 3.12 and Node 20. `pytest` works too
if you prefer it (`python3 -m pytest tests/ -q`).

**Playwright is optional for these tests.** `fetch_tri.py` imports it at module
scope, but nothing here drives a real browser — the `#5` timeout tests use fakes.
So `test_fetch_tri.py` installs a minimal stub (just `TimeoutError` and a
`sync_playwright` that refuses to run) *only when playwright is genuinely absent*.
Clone and run; no install needed.

When the real package IS present it's used unchanged, so the stub can never mask a
broken install. CI installs the real one via `requirements.txt` for exactly that
reason; the scheduled fetch job additionally runs `playwright install chromium`,
because it actually launches a browser.

Verified both ways: the suite passes in a clean venv with playwright absent, and
still produces its 5 expected failures against unfixed v4 in that same clean venv —
the stub does not weaken the mutation guarantee.

Both run on every push via `.github/workflows/tests.yml`, and
`tests/test_fetch_tri.py` runs **before** the scheduled TRI fetch in
`.github/workflows/tri-fetch.yml` — if the continuity gate is broken, the job
commits nothing.

## Why this directory exists

`CHANGELOG_v4.md` claimed the continuity gate had been *"unit-tested against the
audit's adversarial fixture"* and that changes were *"re-verified in a Node
harness"*. The shipped ZIP contained **no test files**, and the gate accepted the
adversarial case. Two other v4 claims ("Alpha → XIRR spread across the Excel
export", "import hardening") were also false against the shipped code.

Untested claims drift from the code. For a tool that produces client-facing
financial reports, the dangerous failure isn't a crash — it's a confidently wrong
number. Every fix in v5 is pinned by a named test, and `CHANGELOG_v5.md` cites the
test name next to each claim.

## Rules

1. **A fix without a test is not a fix.** Don't add a line to the changelog you
   can't point to a test for.
2. **The test must fail against the broken code.** All of these were verified to
   fail against unfixed v4 before being accepted. A test that can't fail proves
   nothing.
3. **Test the real code, not a copy.** `test_app.js` extracts the actual function
   bodies out of `index.html` by name (`extractFn`) and evals them; the remaining
   assertions are source invariants matched against the real file. A test against a
   hand-copied duplicate would keep passing after the shipped code drifted — which
   is the exact failure being guarded against.

`test_fetch_tri.py` loads `fetch_tri.py` directly via `importlib`, so it tests the
shipped module. The Playwright fakes only stand in for the browser.

## Coverage map (report finding → test)

| # | Finding | Test |
|---|---|---|
| 1 | TRI end date moves backwards | `test_rejects_end_date_regression`, `test_rejects_single_day_regression` |
| 2 | Export crashes on error rows | `#2 export SURVIVES a retained error row` (+3) |
| 3 | Retry overwrites wrong holding | `#3 sibling C is NOT overwritten` (+3) |
| 4 | `Scheme Code` mis-mapped | `#4 'Scheme Code' maps to the CODE column` (+5) |
| 5 | Timeout kills whole fetch job | `test_fetch_index_survives_navigation_timeout` (+2) |
| 6 | Non-finite SIP accepted | `#6 1e309 (Infinity) is rejected` (+6) |
| 7 | Erase leaves backups | `#7 erase sweeps __backup_ keys` (+1) |
| 8 | Stale search reopens dropdown | `#8 closeResults invalidates in-flight searches` (+1) |
| 9 | Partial list marked complete | `#9 partial-list miss falls back to server search` (+1) |
| 10 | Export terminology | `#10 export label no longer says PORTFOLIO ALPHA` (+2) |

Regression guards for gates that already worked in v4 (later start, material
shrink, value drift, first run) are included so a future edit to
`continuity_problem()` can't quietly remove them.

## Known gaps

These are **not** covered — listed so nobody mistakes a green run for full
coverage:

- The XIRR solver itself (v4's review confirmed ~10% on a standard one-year case;
  no solver change was made in v5).
- Anything requiring a real DOM or a real browser: rendering, actual `localStorage`
  behaviour, real `XLSX.read` parsing, live network calls.
- `#7`'s backup sweep and `#8`/`#9` are asserted as **source invariants** (the code
  contains the guard) rather than behaviourally — they need a DOM. If you add jsdom,
  promote them.
- The report's deferred recommendations (#3 full schema validation, #4 pending-promise
  cache, #5 parser isolation).

## test_matching.js — scheme matching and benchmarks

Covers the wrong-fund import bug (a sheet reading "ICICI Prudential Large Cap
Fund" resolved to *Large & Mid Cap Fund*), the SEBI category guard, benchmark
mapping for all 39 indices, and SEBI-2.0 rename aliases.

Two rules specific to this suite:

1. **Every category string is real.** Each `"Equity Scheme - ..."` value asserted
   here was read from a live MFAPI response, never assumed. MFAPI's category field
   is dirty — live data contains `"1"`, `"1099 Days"`, `"Growth"`, `"Income"`,
   `"IDF"`, `"Payout"` and `"Formerly Known as IIFL Mutual Fund"` on legacy records.
   Those are asserted as *rejected*, because the old blocklist gate let all of them
   through to a Nifty 500 comparison.
2. **Every benchmark choice cites its source.** `Large Cap -> Nifty 100 TRI` and
   `Large & Mid Cap -> Nifty LargeMidcap 250 TRI` are both confirmed from AMC
   disclosures (comments in the test name the source). A mapping nobody verified is
   a guess with a test wrapped around it.

The suite fails cleanly (not with a stack trace) when run against an index.html
that predates this work — the constants simply aren't there, and that's reported as
a failure. Verified: against the pre-fix file the correct fund scores 72.1% (below
the 0.78 auto-accept floor) while the wrong fund scores 89.4% and wins.
