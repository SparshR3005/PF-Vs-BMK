# v7 — silent data loss, unbounded corruption gate, inert wrong-fund guard

Same rule as v5/v6: **every claim here names the test that proves it**, and nothing
is listed as done unless the test fails against the previous code and passes
against this one.

## Verification

```
python3 tests/test_fetch_tri.py      # gap-scaled move gate
python3 tests/test_fetch_ranks.py    # 96 — NaN ranking, publish gates, dedupe
python3 tests/test_probe_ranks.py    # 71 — universe filters, client/Python parity
node    tests/test_app.js            # 33 — export, import, retry, storage, search
node    tests/test_matching.js       # 95 — matching, category guard, benchmarks
node    tests/test_insights.js       # 65 — ranking, grid decode, disclosure
```

**329 → 366 tests.** All six suites run on every push (`.github/workflows/tests.yml`).

---

## 1. An entire SEBI equity category was silently deleted (HIGH)

`INCOME_TOKENS` contained `"dividend"` to drop IDCW payout *plans*. That substring
also matches **Dividend Yield funds** — a growth-option equity category that
`CATEGORY_CANON` explicitly maps to `DIV_YIELD` and treats as rankable.

Every DY fund was dropped at ingest, on both sides:

- `data/ranks/` had files for all 11 rankable keys **except `DIV_YIELD`** — no
  `periods_DIV_YIELD.json`, absent from `index.json`, no Insights ranking.
- `index.html` applied the same filter in `loadSchemeList()` **and** in the
  server-search fallback, so DY funds never appeared in the picker either.

Nothing errored. The category simply did not exist. Worse, it was *half* deleted:
`Templeton India Equity Income Fund` survived only because its name happens not to
contain "dividend" — so the cohort was partial, which is worse than empty.

**Fix:** exclude the payout *plan*, keep the fund *category*. `"dividend"` is
removed from the token list and replaced with a word-bounded negative lookahead,
`\bdividend\b(?!\s+yield)`, expressed as the shared predicate
`mf_universe.name_looks_income_option()` so the two Python consumers cannot drift.
`index.html` mirrors the same rule in both filters.

*Proof: `'dividend' is not a blanket income token`, `index.html uses the same
dividend-not-yield rule`, `a dividend PAYOUT plan is still excluded`, `an IDCW plan
is still excluded`, `a Dividend Yield FUND survives the income filter`, `DIV_YIELD
is a rankable category, so it must reach the fetcher`.*

**After deploying:** the next `ranks-fetch` run will publish `periods_DIV_YIELD.json`
and `navs_DIV_YIELD_{Direct,Regular}.json` for the first time. `safe_to_publish()`
returns "no prior file" for a first run, so nothing blocks it.

## 2. The TRI corruption gate was unbounded across gaps (HIGH)

`validate_series()` policed the single-day move **only when the gap was ≤ 4 days**:

```python
if gap <= 4:                      # ← everything longer was not checked at all
    move = abs(val - prev_val) / prev_val
```

Any corruption landing after a longer gap validated clean. Measured against the old
code: a **90% single-day collapse following a 10-day gap returned `''` (valid)** and
would have been published, silently rewriting every XIRR computed against that
series. The gap is reachable on an ordinary long weekend — Fri→Mon is 3 days, one
extra holiday makes it 5.

The continuity gate does not cover this. It compares *overlapping historical*
points, so a corrupt **new tail** is all-new data and is never sampled.
`validate_series` was the only check standing there.

**Fix:** the tolerance now **scales** with the gap (random-walk `sqrt(t)`) and is
clamped by a new `MAX_GAP_MOVE = 0.60` ceiling, so it is never switched off:

```python
limit = MAX_DAILY_MOVE if gap <= 4 else min(
    MAX_GAP_MOVE, MAX_DAILY_MOVE * math.sqrt(gap / 4.0)
)
```

This also repairs a latent second bug: `prev_date`/`prev_val` were only advanced
inside the branches, so they now update unconditionally on every row.

*Proof: `test_rejects_crash_after_long_gap`, `test_rejects_crash_just_past_the_old_four_day_cutoff`,
`test_gap_move_never_exceeds_absolute_ceiling`, and — equally important, so the gate
does not become a blanket ban — `test_allows_real_move_over_a_holiday_gap` (8% over
10 days), `test_allows_worst_real_single_day_crash` (13%, 2020's worst session),
`test_still_rejects_large_single_day_move` (36%).*

## 3. The wrong-fund category guard was inert for compressed spellings (HIGH)

`matchKey()` canonicalises `midcap → mid cap`, `flexicap → flexi cap` and so on.
`claimedCategoryFromName()` did **not** — it ran on the raw name. The two therefore
disagreed about the same string:

| Typed name | `matchKey` | guard claim |
|---|---|---|
| `HDFC Mid Cap Fund` | `hdfcmidcap` | `MID_CAP` |
| `HDFC Midcap Fund` | `hdfcmidcap` | **`null`** |

`null` means the guard does not fire at all. The v6 wrong-fund fix itself is intact
(re-verified end to end — `stripRenameNote` makes the correct fund an exact match),
but the *guard behind it* had no effect for any sheet written "Largecap"/"Midcap".
Both spellings appear freely in AMFI strings and client sheets, so this was live,
not theoretical.

**Fix:** new `canonicaliseCapSpelling()` applies the same normalisation before the
claim is read. **Order is load-bearing** — `largemidcap` is rewritten *before*
`largecap`, or a Large & Mid Cap name would be mangled into a `LARGE_CAP` claim and
the guard would fire on a *correct* fund. Same most-specific-first rule as
`SECTOR_KEYWORDS`.

*Proof: 5 compressed-spelling tests, 2 spaced-form regression tests, and 4 ordering
tests including `ORDER: 'Largemidcap' claims LARGE_MID, not LARGE_CAP`.*

## 4. `rank_desc()` mis-ranked on non-finite values (MEDIUM)

`vals.index(v)` compares with `==`, and `NaN != NaN`. A single NaN either raised
`ValueError` or shifted every rank below it. Measured on the old code:
`[a=1.0, b=NaN, c=3.0]` published `{a:1, b:2, c:3}` — **`c` is genuinely rank 1**.
Published with no error and no warning. `period_return()` can emit a non-finite
value from bad upstream NAV, so this was reachable from live data.

**Fix:** non-finite entries are dropped rather than ranked (a fund whose return
cannot be computed has no defensible position, and omitting it keeps the published
denominator honest). Rewritten O(n) instead of O(n²) — `index()` rescanned the list
per fund. Measured 64 ms → 9 ms at n=4000.

`compute_period_table` now passes `len(ranks)` to `quartile()` rather than
`len(scored)`: since `rank_desc` can drop funds, the old denominator would
over-count and shift funds into the wrong quartile band.

*Proof: 12 tests including `a NaN entry is dropped rather than ranked`, `the best
finite value is rank 1 despite a NaN`, `tied values share the better rank`,
`ranked population excludes the non-finite fund`.*

## 5. Ranking cohort shrinkage was not disclosed (MEDIUM)

`rankCandidates()` requires `placed === scheduled` — methodologically correct, since
it is what stops a mid-window launch posting a flattering short number. But the
published grids hold funds with many different inception dates:
**`navs_LARGE_CAP_Direct.json` has 33 funds across 14 distinct `t0` values.** Over a
long SIP window the eligible pool collapses to the oldest handful, and nothing said
so — "**rank 2 of 3**" rendered for a category the user believes has 33 funds.

**Fix:** `rankCandidates` now returns `cohort` (all funds with a computable XIRR)
alongside `universe` (the eligible pool). Below `MIN_RANK_UNIVERSE = 8` the UI states
the position without implying a category standing, and any excluded funds are
disclosed as having started after the SIP began. Mirrors the reasoning behind
`MIN_QUARTILE_UNIVERSE` in `fetch_ranks.py`.

## 6. `"and"` and `"&"` produced different match keys (LOW)

`matchKey` strips `&` via its `[^a-z0-9]` pass, but the **word** `and` survived it:

- `Large & Mid Cap` → `iciciprudentiallargemidcap`
- `Large and Mid Cap` → `iciciprudentiallargeandmidcap`

Similarity 0.906 cleared the 0.78 floor, so it resolved correctly *today* — but only
via the **fuzzy path**, which is precisely the path that produced the v6 wrong-fund
import. `CATEGORY_CANON` already accepts both spellings; the key now does too.

*Proof: `'&' and 'and' produce the SAME match key`, `an unrelated name is still
distinct`.*

## 7. Year basis unified to 365.25 (LOW)

JS `xirr()` divided by **365**; Python `annualised()` uses **365.25**. The Insights
tab prints both side by side, so the two figures never quite reconciled if checked
against each other (~0.011 pp on a 15% 3-year number — immaterial numerically, but a
real inconsistency in a client-facing report).

`DAYS_PER_YEAR` is declared **inside** `xirr()` deliberately: the test harness
extracts functions from `index.html` one at a time, so a module-level `const` would
not be in scope. The first attempt did exactly that, `xirr` threw, and the ranking
universe silently collapsed to 0 — caught by `test_insights.js`, which is the whole
argument for extracting live functions rather than copies.

## 8. `tri-fetch.yml` now gates on the JS suites

The TRI job ran only `test_fetch_tri.py` before committing. The JS suites guard the
benchmark **routing** that this data feeds — a fund routed to the wrong index
produces a confidently wrong spread even when every TRI series is perfect. Cheap to
run, so `test_matching.js` and `test_insights.js` are now gates too.

---

## Not done (deliberate)

- Everything in `WONT_FIX.md` stands unchanged: meta-CSP, portfolio XIRR terminal
  dating, local-timezone date arithmetic, `data/tri/` pipeline population, and the
  SheetJS import-parser swap. None was re-litigated.
- `MAX_GROWTH_FACTOR`'s log message prints `>{:.0%}` on a factor of 1.60, rendering
  as "160%" where the check is "more than 1.6×". Cosmetic, left alone.
