# v13 — the post-v12 audit, closed

Same rule as v5–v12: **every claim names the test that proves it.**

Findings and severities are the external audit's; the reproductions were re-run
independently against the committed data before anything was changed.

## Verification

```
python3 tests/test_fetch_tri.py       36   unchanged
python3 tests/test_fetch_ranks.py    152   + duplicate policy, refused-publish meta  (was 141)
python3 tests/test_probe_ranks.py     93   unchanged
python3 tests/test_mergers.py         17   tightened, see #2
node    tests/test_app.js             33   unchanged
node    tests/test_matching.js       123   + template rename back-compat  (was 121)
node    tests/test_insights.js       152   + exclusion cause, rank floor, export scope  (was 141)
```

**606 tests.** The v12 changelog's total of 579 was wrong — `test_insights.js`
already reported 141, not 138, because three guards were added without the count
being carried forward. Corrected here.

---

## 1 — `build_weekly_grid()` kept the highest NAV, not the last (High)

`pts.sort()` ran **before** the de-duplication. `pts` holds `(date, nav)` tuples, so
sorting orders them date-then-NAV-ascending and the dict fill kept the **maximum**
for a day — while `compute_period_table()` and the client's `getDetail()` both keep
the **last row in MFAPI's own order**. Two published files could describe one fund
on one day at two different NAVs.

v8 §5 claimed all three paths agreed and cited a test as proof. That test's fixture
appended the duplicate as `999` against an original of `100`, so *last* and
*highest* coincided and it passed under either policy. It could not fail against the
broken code — the one thing every test here is required to do.

**Fix:** drop the pre-sort; `pts` is appended in row order, so a forward fill keeps
the last row as received. Reproduced before and after:

```
duplicate of day 1 with the LOWER nav appearing last
  before:  grid 100.0   parser 50.0   agree? False
  after:   grid  50.0   parser 50.0   agree? True
```

*Proof: `the grid resolves a conflicting duplicate to the LAST row, not the highest`,
`...which is what the parse loop and getDetail() keep`, `so the weekly grid and the
period table agree on the same fund/day`, `a duplicate that IS the last row still
wins when it is the larger number`. Mutation-checked: the first and third fail
against the v12 dedupe.*

## 2 — The merger chain was inert, and the test permitted it (High)

The guard read `assert t0 <= splice or t0 == splice`. The second clause is subsumed
by the first, and both **pass on `t0 == splice`** — precisely the un-spliced state.
It proved the wiring existed and permitted the wiring to be inert, which is what
happened: the chain shipped, the grids kept `t0 = 2022-11-28`, and 17 merger tests
stayed green while Insights told the user its history did not cover a window the
Portfolio tab was already displaying.

**Root cause was neither refusal nor misconfiguration** — the published grid simply
predated the chain code. A manual run settles it:

```
chained 151036<-119807 from 2013-01-02
chained 151034<-112496 from 2010-02-16
```

The splice gate passed on real data, which retroactively confirms the ratio
assumption: the two series are continuous across 2022-11-28 to within 3%.

**Fix:** `assert t0 < link["splice"]` — an outcome, not a permission. If a chain can
never splice, the entry does not belong in `CHAIN` at all; an inert entry is a lie in
config, not a state to tolerate.

**This test is RED against the archived `data/ranks/` and goes green the moment the
newly spliced grids are committed.** That is intended.

## 3 — The exclusion note mis-attributed cause, in the flattering direction (Medium)

Every dropped fund was reported as *"started after your SIP began … so the comparison
is like-for-like."* Measured on the committed ELSS grids, **6 of 15 had simply
stopped reporting** (last NAV between Feb-2025 and Apr-2026). Dropping those is
survivorship bias — the opposite of like-for-like — and the pane's own footer already
warned that surviving cohorts flatter. Two sentences on one screen contradicted each
other and the wrong one was attached to the number.

The server already splits the two (`exclusion_reason()`, whose docstring describes
this exact bug being fixed there); the browser never did.

**Fix:** `rankCandidates()` classifies each exclusion by cause — stale, late, or
gapped — and `excludedNote()` renders the split, naming survivorship bias when dead
funds were dropped rather than claiming cleanliness.

*Proof: `rankCandidates classifies every exclusion by cause`, `the exclusion note no
longer claims every dropped fund started late`, `...and names survivorship bias when
dead funds were dropped`, `a dead-fund threshold exists rather than being inlined`.*

## 4 — The peer ranking annualised any window (Medium)

`fetch_ranks.py` refuses to annualise below a year on principle — *"it implies a
precision the number does not have"* — and the track-record table honours it. The
peer panel rendered directly beneath did not. Measured on `navs_MID_CAP_Direct`:

| SIP length | Instalments | Median XIRR | Top peer |
|---|---|---|---|
| 0 months | 1 | 39.12% | **231.68%** |
| 3 months | 4 | 31.67% | 61.50% |
| 12 months | 13 | 12.58% | 21.52% |

**Fix:** `MIN_RANK_DAYS = 365`, mirroring `MIN_ANNUALISE_DAYS`. Below it the rank and
peer figures are suppressed and the reason stated. The track record is point-to-point
and unaffected, so one standard now applies across the tab instead of two.

*Proof: `the peer ranking has a minimum window, mirroring MIN_ANNUALISE_DAYS`,
`...and it suppresses the rank rather than annualising a short window`.*

## 5 — A refused publish advertised an `as_of` for data it never wrote (Medium)

`cat_status` took `as_of.isoformat()` and the rejected candidate's counts regardless
of whether `safe_to_publish()` allowed the write. The client prints that field as the
pane heading, so the user read *"as of \<today\>"* above older numbers. The file states
the principle it was breaking one screen up: *a manifest that disagrees with its own
payload is worse than no manifest, because the client trusts it.*

**Fix:** `committed_meta()` reads the file's own `as_of`/`count` on refusal, and the
per-plan grid count falls back to `existing_count()`.

**And a refusal no longer exits green.** `failed` counted exceptions, not refusals, so
one refused category alongside twenty successful writes exited `0` — and because
`safe_to_publish()` compares against the *committed* count, the refusal reproduces
every night until someone looks. That is the nine-day `NIFTY_HEALTHCARE` shape on the
ranks side. Everything writable has already been written by that point, so failing
there costs no data.

*Proof: `committed_meta reads the file's OWN as_of`, `...and its own count`, `a
missing file yields no meta rather than a guess`, `an unreadable file yields no meta
rather than raising`, `the manifest quotes committed meta when a publish is refused`,
`the per-plan grid count falls back to what is on disk`, `a refused publish exits
non-zero`.*

## 6 — The Excel export ignored the sub-tab scope (Medium)

`exportExcel()` never referenced `pfScope`, so with **Live SIP** showing 13.73% the
exported file reported 14.75%, unlabelled.

**Fix:** label, don't filter. Exporting every leg is what keeps a round-trip lossless
— following the scope would silently drop ended legs from the user's own backup. The
defect was an unlabelled number the reader could not reproduce, so the sheet subtitle
now names the population and says when the screen was showing something narrower.

*Proof: `the Excel export labels the scope it covers`, `...while still exporting every
leg, so a round-trip stays lossless`.*

## Smaller items

- **`isFinite` → `Number.isFinite`** in `rankCandidates()`. `isFinite(null)` is `true`,
  so a peer whose XIRR failed to solve would have entered the pool and sorted as 0%.
  Latent, not live — a 6,460-run sweep found zero occurrences — but every other call
  site in the file already used the strict form.
- **Import template column renamed `Monthly` → `Monthly SIP`,** matching the form
  label and the Excel export, which both already said so. The importer matches on
  `h.includes("monthly")`, so every existing sheet keeps working; that back-compat is
  asserted rather than assumed.
- **Test-count correction**, above.

## Not done

- **`ranks-fetch.yml` still does not gate on the JS suites.** Deliberate for now: it
  would roughly double the job's setup time, and the JS suites do not read
  `data/ranks/`. Revisit if a rendering defect ever ships through it.
- **`render()`'s "No live SIPs" branch still returns before `checkTriFreshness()`.**
  Cosmetic; the banner keeps its prior state on a pane that shows no figures.
- **Closed-ended `BANK OF INDIA Mid Cap Tax Fund Series 1/2`** are refused by the
  wrong-fund guard (SEBI category ELSS, name claims MID_CAP). Correct behaviour for a
  name-based guard; listed for completeness.
- Everything in `WONT_FIX.md` stands, including **#13**.