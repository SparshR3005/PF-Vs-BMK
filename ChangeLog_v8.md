# v8 — data-integrity gates that were not actually gating

Same rule as v5–v7: **every claim names the test that proves it**, and nothing is
listed as done unless the test fails against the previous code and passes against
this one.

All six defects came from an external adversarial audit that shipped executable
reproductions. Every one reproduced against v7, so none overlapped the v7 work.

## Verification

```
python3 tests/test_fetch_tri.py      # continuity: full overlap, no sampling
python3 tests/test_fetch_ranks.py    # 116 — Infinity, staleness, dedupe, NaN ranks
python3 tests/test_probe_ranks.py    #  71 — universe filters, client/Python parity
node    tests/test_app.js            #  33 — export, import, retry, storage, search
node    tests/test_matching.js       #  95 — matching, category guard, benchmarks
node    tests/test_insights.js       #  80 — ranking, NAV parsing, dedupe
```

**366 → 411 tests.** Mutation-checked: **16 of the new tests fail against v7.**

---

## 1. The TRI continuity gate checked 0.6% of committed history

**`fetch_tri.py` — `continuity_problem()`**

Two separate holes, both in the one function standing between a bad fetch and a
permanently rewritten price history.

### (a) Sampling left 99.4% of real data unchecked

The value comparison sampled ~40 points:

```python
step = max(1, len(common) // 40)
for d in common[::step]:
```

On the real committed `NIFTY500.json` — **6,857 points** — that makes `step = 171`.
**41 dates were compared and 6,816 were never looked at.** A published historical
value could change by any amount and pass simply by not being one of the 41.

Measured: in a 100-point history, multiplying the second observation by **10×**
returned `''` (valid).

### (b) A fully disjoint series was accepted

The comparison ran only `if len(common) >= 20`. A new series sharing **fewer than
20 dates skipped the check entirely** and fell through to `return ""`. So a
replacement with an earlier start, a later end, a similar row count, **zero dates
in common** and values 5× different was accepted — which is precisely the "wrong
index served under the right name" case the gate exists to catch. With zero
overlap the gate was not weak; it was absent.

**Fix:** overlap is now a **requirement**, not a precondition. At least 98% of
committed dates must survive (`CONT_MIN_DATE_OVERLAP`), the prior terminal date
must still exist, and **every** overlapping point is compared — no sampling.
Non-finite and non-numeric values are rejected outright.

Cost on real data: **1.69 ms** for a 6,857-point series, ~66 ms across all 39
indices. Probabilistic validation was never justified here.

*Proof: `test_rejects_fully_disjoint_replacement`, `test_rejects_partial_overlap_below_floor`,
`test_rejects_mutation_the_old_sampler_missed`, `test_rejects_mutation_anywhere_in_a_large_series`
(checks indices 1, 7, 1234, 2999, 3998 of a 4,000-point series),
`test_rejects_missing_prior_end_date`, `test_rejects_non_finite_committed_value`.
And so the gate does not become a blanket ban: `test_accepts_a_genuine_daily_extension`,
`test_accepts_tiny_revisions_within_tolerance`, `test_first_run_still_publishes`.*

## 2. The ranking pipeline could publish JSON the browser cannot parse

**`fetch_ranks.py` — `write_json_atomic()`, `period_return()`, `annualised()`**

`json.dump` defaults to `allow_nan=True`, which emits **bare `Infinity` / `NaN`
tokens**. Python round-trips them; the browser's `JSON.parse()` and
`response.json()` **reject them**. One extreme NAV pair could therefore take an
entire ranking category offline in the client while every server-side check passed.

Reproduced with NAVs `1e-308 → 1e308`: `period_return` returned `inf`, the writer
emitted bare `Infinity`, and a strict parser refused the file.

**`fetch_tri.py`'s writer already set `allow_nan=False`.** The two writers sharing
a name and disagreeing on safety is exactly how the value reached disk.

**Fix, three layers:**

1. `period_return()` and `annualised()` return `None` for non-finite results.
2. `compute_period_table()` re-checks finiteness before a value enters output, and
   derives `universe`, `avg` and `median` from the **ranked** population — a
   published "rank 6 of 134" whose denominator came from a different population
   than its numerator is a number a client cannot reconcile.
3. `write_json_atomic()` sets `allow_nan=False` and cleans up its `.tmp` on
   failure. Raising here is the point: the last-good file survives.

*Proof: `an overflowing return is excluded from output`, `the published universe
counts only ranked funds`, `no bare Infinity token reaches the file`, `a strict JSON
parser accepts the published file`, `write_json_atomic refuses a non-finite payload`,
`and leaves no partial .tmp behind`.*

## 3. Stale funds were ranked as if they had current data

**`fetch_ranks.py` — `period_return()`**

A category's `as_of` is the **latest NAV date across every fund in it**. For any
fund that stopped reporting, `nav_on_or_before()` carried its final NAV forward to
*both* window boundaries with no freshness limit.

Reproduced: a fund whose last NAV was 20 July 2025, measured against an `as_of` of
20 July 2026, published **`0.0%` as its 1-year return** — it reused the same stale
price at both ends. Suspensions, mergers and closures are routine in Indian MF, so
this is an ordinary event.

**Fix:** the terminal observation must be within `MAX_TERMINAL_STALE_DAYS = 7` of
`as_of` (matching `runSIP`'s NAV match window and `fetch_tri`'s `MAX_STALE_DAYS`),
and the opening observation within `MAX_BOUNDARY_STALE_DAYS = 30` of the window
open. A stale fund is excluded from that horizon rather than ranked on a dead price.

*Proof: `a year-stale fund is excluded from the 1y horizon`, `...and from every
other horizon too`, `a fund reporting within tolerance still ranks`, plus boundary
tests at exactly 7 days (included) and 8 days (excluded).*

## 4. Malformed upstream dates were laundered into real trading days

**`index.html` — `getDetail()`**

JavaScript **rolls over** impossible calendar dates instead of rejecting them:
`new Date(2025, 1, 31)` silently becomes **3 March 2025**. A malformed MFAPI row
like `31-02-2025` was therefore accepted as a real trading day and could shift both
SIP placement and valuation.

`parseInput()` already round-trips its ISO dates for exactly this reason. MFAPI's
`DD-MM-YYYY` path did not.

**Fix:** round-trip the constructed date against its inputs and reject any
mismatch. (`mf_universe.parse_dmy()` was checked and is already safe — Python's
`date()` raises on impossible dates.)

*Proof: `an impossible date (31 Feb) is dropped, not rolled forward`, `a real leap
day is kept`, `a non-leap 29 Feb is dropped`, `31 April is dropped`, `month 13 is
dropped`.*

## 5. Duplicate same-day NAVs created a phantom instant profit

**`index.html` — `getDetail()`; `fetch_ranks.py` — the NAV parse loop**

`navOnOrAfter()` binary-searches to the **first** row for a date;
`navOnOrBefore()` to the **last**. With two rows for one date they return opposite
ends, so a SIP could **buy at one NAV and be valued at another on the same day**.

Reproduced: two 1-January rows at NAV 100 and 200 made a ₹100 investment worth
**₹200 instantly**.

The server had the same disagreement with itself: `pts.sort()` on `(date, nav)`
tuples resolved a duplicated date to the **highest** NAV, while
`build_weekly_grid()` kept the **last** — so the period table and the ranking grid
could describe the same fund differently on the same day.

**Fix:** all three paths now key by calendar day and keep the **last** row.
Identical duplicates collapse silently; **conflicting** ones are counted and
surfaced to the user via a toast and a console warning, because silently picking one
is what made this unauditable.

*Proof: `duplicate same-day rows collapse to one`, `a conflicting duplicate is
flagged for the user`, `buy and valuation NAVs now agree on the same date`, `so a
same-day purchase shows no phantom gain`, `identical duplicates collapse silently`,
`rows remain date-sorted after dedupe`, `the grid resolves the duplicate to the LAST
row, as the parser does`.*

---

## A note on the supplied reproductions

`PF-Vs-BMK-browser-repro.js` will **still print the old output** after this
release. It contains its own private copies of `navOnOrBefore`/`navOnOrAfter` and
never reads `index.html`, so it can only ever demonstrate the original behaviour.
The equivalent scenarios are now asserted against the **shipped** file in
`tests/test_insights.js`.

`PF-Vs-BMK-adversarial-repro.py` raises `KeyError: 'BAD'` at check 3 — that is the
fix working: the overflowing fund no longer appears in the output at all.

## Audit items deliberately not actioned

- **Meta-CSP `frame-ancestors`** and the **SheetJS import-parser swap** are already
  covered in `WONT_FIX.md` with reasoning the audit did not engage with: GitHub
  Pages cannot set response headers, and the current SheetJS CE build is not on the
  CDNs this project already trusts.
- **"Split the 182 KB monolith into 8 modules"** fights the deliberate single-file,
  paste-the-whole-file workflow this project is built around.
- **"The test-extraction harness is brittle"** — it has now caught three real
  regressions across v7 and v8, including a scope error introduced by a fix. It is
  earning its keep.
- **Bounded concurrency, fetch timeouts, peer memoisation, cache headers** — all
  reasonable, none is a correctness defect. Worth doing, not worth bundling into a
  data-integrity release.