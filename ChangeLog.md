# Changelog

Same rule as v5–v15: **every claim names the test that proves it**, and nothing is
listed as done unless the test fails against the previous code and passes against
this one.

From v16 this is a **single file**. The nine per-release files it replaces are
appended verbatim below.

---

# v16 — the eleven-year cliff, the funds that vanished from the count, and a commit that never ran

Findings from a full-repo audit against the committed data. All three reproduce
against a fully green v15 (680 tests), so none is a regression — they are gaps no
existing test covered.

## Verification

```
python3 tests/test_fetch_tri.py       40   + future-dated rows            (was 36)
python3 tests/test_fetch_ranks.py    165   + grid window, workflow wiring (was 152)
python3 tests/test_probe_ranks.py     93   unchanged
python3 tests/test_mergers.py         17   unchanged
node    tests/test_app.js             34   unchanged
node    tests/test_matching.js       123   unchanged
node    tests/test_insights.js       193   + exclusion accounting, gridSpan (was 176)
node    tests/test_report.js          49   unchanged
```

**714 tests**, up from v15's 680. Five mutations, all caught — listed per section.

Also verified clean and **not** changed: all 39 TRI series pass `validate_series()`
and `is_fresh()` with no future-dated rows and a maximum calendar gap of 6 days;
the ≤7-day weekly-grid bound holds across all 22 `navs_*.json` with zero
non-monotonic offsets; every `periods_*.json` reconciles exactly — rank counts
equal `universe`, ranks span 1..n, and recomputed avg/median match the published
values to the rounding digit across 11 categories × 2 plans × 7 horizons. The v11
merger chain is now genuinely live in the grids (151034/151036 at `t0 =
2015-08-03`, not the un-spliced 2022-11-28), which retires the v13 §2 caveat that
its guard was RED against the archived data.

---

## 1 — The peer ranking was capped at eleven years, and the pane blamed the fund (HIGH)

**`fetch_ranks.py` — `GRID_YEARS`**

`build_weekly_grid()` truncates every fund's grid at `GRID_YEARS`, which was `11`
with the comment "10y horizon plus buffer". That framing was wrong: it is not a
display horizon, it is the hard limit on how long a SIP the browser can rank.
`rankCandidates()` has no knowledge of the cutoff.

Once a SIP's first instalment falls more than 7 days before the grid start, *every*
peer accumulates `skipped > 0`, both pool filters return empty, and the eligible
universe collapses to zero. Measured against the committed
`navs_LARGE_CAP_Direct.json`, whose grid starts 2015-08-03:

| SIP start | instalments | universe | own rank |
|---|---|---|---|
| 2015-08-01 | 132 | 21 | **4** |
| 2015-07-20 | 133 | **0** | none |
| 2014-01-15 | 151 | **0** | none |

Twelve days apart. And the pane then rendered three statements, all false:

- *"Your fund could not be ranked over this window (its history does not cover it)."*
  The fund has the history. The published grid does not.
- *"35 funds excluded: 35 started after your SIP began. Comparing only funds that
  ran your whole window keeps it like-for-like."* None of the 35 started late, and
  nothing was like-for-like because nothing was compared.
- *"No comparable peers with a full history over this window."*

Mis-attribution in the flattering direction — the v13 §3 defect class reappearing
through a third cause the classifier had no bucket for. And **the cliff moved**:
`cutoff = as_of − GRID_YEARS×365.25` advances a day every day, so a holding that
ranked correctly last month fell off this month. An August-2015 SIP had about two
weeks left.

**Fix:** `GRID_YEARS = 20`. That puts the cutoff around 2006, at or before the
inception of essentially every scheme in these cohorts, so the limit stops being
reachable in practice rather than merely being pushed back.

**Measured cost, stated rather than hand-waved:** the navs payload grows about
**1.53× in total — 3.1 MB → ~4.8 MB across all 22 files**. Only funds that
genuinely have 20 years of history grow at all; the largest single file, ELSS
Direct, goes 293 KB → ~419 KB. Client-side, `rankCandidates` walks proportionally
more grid points, so v14's measured 574 ms for ELSS cold becomes roughly 880 ms.
The figures above are an upper bound — they assume every fund currently at maximum
grid length has a full twenty years behind it.

*Proof: `the grid spans more than 11 years of history`, `...and reaches back the
configured 20`, `a SIP begun 15 years ago starts on or after the grid t0`, `the grid
still honours the max-gap bound at 20 years`, `...and the window really is the
configured 20 years, not the old 11`.*
*Mutation M1 — revert `GRID_YEARS` to 11 and four assertions go red.*

The pre-existing `history is capped at the retention window (~11y)` assertion had
the old cap hard-coded. It is now expressed against `R.GRID_YEARS`, because a test
that has to be hand-edited whenever the code changes has stopped describing the
code it guards.

**Not fixed here, deliberately:** past twenty years the same three false sentences
would still print. The messaging fix (classify the cause as "outside the published
peer window", and/or rank over the overlap and say so) was scoped out of this
release in favour of moving the limit out of reach. If a pre-2006 SIP ever appears,
that becomes live again.

## 2 — The most dead funds escaped the exclusion disclosure entirely (MEDIUM)

**`index.html` — `rankCandidates()`, new `classifyExclusions()`**

`rows` is built only from funds where `runSIP` returned a finite XIRR. A fund dead
enough that **no instalment could be placed at all** never entered `rows`, so it
was counted in neither `universe`, nor `cohort`, nor `excluded`. It simply
vanished, while the note continued to present itself as accounting for the
category.

Measured on the committed ELSS Direct grid (52 funds), driving the real function:

| window | pool | the note said | actually dropped | **unaccounted** |
|---|---|---|---|---|
| 1y | 46 | "3 funds excluded" | 6 | **3** |
| 1.5y | 46 | "5 funds excluded" | 6 | **1** |
| 2y | 45 | "7 funds excluded" | 7 | 0 |

The hidden funds were `120079` HSBC Tax Saver, `132933`/`133364` SBI Long Term
Advantage Series I & II, and `133324` Sundaram Long Term Tax Advantage — every one
a dead scheme, which is exactly what v13 §3 added the split to surface.

The inversion is the point: **a fund stale enough to be excluded got counted; a
fund stale enough to place nothing did not.** Coverage of the disclosure was worst
precisely where the survivorship bias was worst.

**Fix:** every fund in the document gets a span record, and `classifyExclusions()`
runs over all of them. `universe + excluded === cohort` is now an invariant rather
than a coincidence, and `cohort` means the whole published cohort instead of "the
funds that happened to produce a run".

A never-placed fund is **not** assumed dead. It can equally be a late starter —
a stopped SIP's window can close before that fund's grid opens — so the same
stale/late/other rule is applied to everyone.

*Proof: `1y/1.5y/3y/8y: universe + excluded accounts for every published fund`,
`...cohort is the whole published cohort, not just the funds that ran`, `the 1-year
window discloses every dropped fund, not a subset`, `...and they are reported as
dead funds, which triggers the survivorship caveat`, `the rendered note reaches the
survivorship-bias caveat`, `...and its total matches the classifier`, `the late fund
is counted even though it placed nothing`, `...and it is classified LATE, not stale`.*
*Mutation M2 — classify over `rows` instead of `spans` and six assertions go red.*

## 3 — A refused publish threw away every file that had published (HIGH)

**`.github/workflows/ranks-fetch.yml`**

`fetch_ranks.py` deliberately writes everything publishable and *then* returns 1 —
on a refused publish gate (v13 §5) or a per-category exception. Its own comment
says *"everything that could be written already has been by this point, so failing
there costs no data."*

**That was false as wired.** The fetch ran as a plain step, so a non-zero exit
failed it, so **"Commit updated data" never ran**, and every category that
published successfully was discarded with the runner.

It also latched, and worse than v13 §5 anticipated: `safe_to_publish()` compares
against the **committed** count, so with nothing committed the count never updated,
the refusal reproduced the next night, and **all eleven categories** stopped
updating — not just the refused one. Reachable without anything exotic:
`get_json()` drops a fund after three failed retries, so a partial mfapi wobble
during discovery shrinks a cohort past the 80% / 3-fund gate.

`tri-fetch.yml` already had the right shape and said why — its `--audit` step runs
*after* the commit, because "failing earlier would mean one wedged index blocks the
38 healthy ones". `fetch_tri.py` is correctly fail-closed for the same reason: its
`sys.exit(1)` sits at line 642, **before** the writes at 647 and 677, so there is
genuinely nothing to commit. `fetch_ranks.py` is the opposite shape and needed the
opposite wiring.

**Fix:** the fetch step takes `id: fetch` and `continue-on-error: true`, the commit
runs unconditionally, and a final step re-raises the failure on
`steps.fetch.outcome == 'failure'`. The regression-test gate is deliberately **not**
soft — a red suite must still stop both the fetch and the commit.

*Proof: `the commit step comes after the fetch step`, `the fetch step is allowed to
fail soft, so the commit still runs`, `...and carries an id the failure step can
reference`, `a later step re-raises the fetch failure`, `...and that step actually
exits non-zero`, `the regression-test gate is NOT continue-on-error`, `the commit
step is unconditional (no if: that could skip it)`.*
*Mutation M3 — delete `continue-on-error` and the guard goes red.*

## 4 — Smaller items

**`fetch_tri.py` — future-dated rows are now dropped at parse.** `fetch_ranks.py`
already guards this ("MFAPI occasionally serves a malformed row"); the TRI fetcher
did not, and two writers disagreeing about a safety property is exactly how the
bare-`Infinity` value reached disk in v8 §2. The blast radius here is larger than a
single row: `doc["end"]` is the max date in the series, so one future-dated row
makes `is_fresh()` false, which fails that index — and if the index is in
`REQUIRED_KEYS`, the soft completeness gate exits 1 before anything is written and
**all 39 series are skipped for the night**. `--force` is no escape; it bypasses the
continuity gate, not the freshness one. Two days of slack absorbs IST-vs-UTC skew.
This is hardening, not a reproduced failure: no committed TRI file contains a
future date. *Proof: `test_a_future_dated_row_is_dropped_at_parse`, `test_the_future_row_no_longer_kills_the_whole_run`, `test_a_row_inside_the_skew_window_is_kept`, `test_ordinary_rows_are_untouched_by_the_guard`. Mutation M4 — remove the guard, two go red.*

**`index.html` — `gridToNavArr()` was built twice per fund.** `rankCandidates()`
called it once to run the SIP and again purely to read `arr[0].date` and
`arr[arr.length-1].date`. Those are just `t0` and `t0 + d[last]`, so the second
build was pure allocation on the path v14 measured at 574 ms for ELSS alone. New
`gridSpan()` derives both without materialising the array — and, more importantly,
gives a span to funds that produced no run at all, which is what made §2 fixable.
*Proof: `gridSpan endpoints equal gridToNavArr's on every published fund` — checked
against every fund in the committed LARGE_CAP Direct grid, so the refactor is
asserted to be meaning-preserving rather than assumed to be.*

**`index.html` — `buildInsights()` named one fund as both best and weakest.** With a
single comparable holding, `withAlpha[0]` and `withAlpha[length-1]` are the same
row, so the line read "Best performer: X. Weakest: X." — two findings where there is
one fund. *Proof: `buildInsights special-cases a one-holding portfolio`, `...and says
so instead of printing the same fund twice`. Mutation M5 — red.*

**`tests/test_insights.js` — the single-call-site drift guard counted mentions, not
calls.** It matched `rankCandidates(` in raw HTML, so a *comment* naming the
function failed it. A drift guard that goes red on documentation trains you to
delete documentation. It now strips block comments and whole-line `//` comments
first; `//` is only treated as a comment at the start of a line, so URLs inside
string literals cannot mask a real call. Re-mutation-checked: inserting a genuine
second call site still turns it red.

**`.github/workflows/tests.yml`** — `mf_mergers.py` added to the `py_compile` step.
It was the only module not syntax-checked directly (it was exercised indirectly via
`test_mergers.py`'s import).

---

## Not done (deliberate)

- **The post-20-year messaging**, above.
- **`probe_ranks.py` is still in the tree.** Its own docstring calls it disposable
  and `ranks-probe.yml` says to delete the workflow "once the numbers are known".
  But `tests/test_probe_ranks.py` imports it *and* asserts on its source, and that
  suite is the `mf_universe.py` ↔ `index.html` drift guard which gates both
  `tests.yml` and `ranks-fetch.yml`. Removing the probe means first moving the
  drift-guard assertions into a suite that does not depend on it. Not bundled into
  a correctness release.
- **Everything in `WONT_FIX.md` stands**, including **#13**, local-timezone date
  arithmetic.
- **v9's deferred items stand unchanged**: the Insights `as-of` taken from
  `items[0].valueDate` after the alpha sort (cosmetic), and `loadStore()`'s
  inherited-key guard (reachable only by hand-editing `localStorage`; the portfolio
  create/rename validators already reject every `Object.prototype` name, though they
  report it as "already exists").


---

# Earlier releases

Consolidated verbatim from the nine per-release files this replaces
(`CHANGELOG_v7.md` … `ChangeLog_v15.md`), newest first, so the whole
history is one read. That matters for this project's own process rule —
*read all documentation in full before filing an audit finding* — which a
truncated read of one of nine files broke once already (v9, withdrawn
finding). Nothing below has been edited.


---

# v15 — labels, button size, and dd-mm-yyyy dates

Same rule as v5–v14: **every claim names the test that proves it.**

Requested changes, not an audit. One of them reverses a v4 decision; that section
says so plainly rather than burying it.

## Verification

```
python3 tests/test_fetch_tri.py       36   unchanged
python3 tests/test_fetch_ranks.py    152   unchanged
python3 tests/test_probe_ranks.py     93   unchanged  (parses index.html at runtime)
python3 tests/test_mergers.py         17   unchanged  (parses index.html at runtime)
node    tests/test_app.js             34   #10 repointed at the disclosure  (was 33)
node    tests/test_matching.js       123   unchanged
node    tests/test_insights.js       176   + label, date and button guards   (was 166)
node    tests/test_report.js          49   + per-sheet disclosure, date round-trip (was 29)
```

**680 tests**, up from v14's 649. Eight mutations, all caught.

---

## 1 — "XIRR spread" → "Alpha", with the disclosure moved to the footnote

The on-screen table has always said **Alpha**; only the workbook said "XIRR
spread". The sheet was the odd one out, so this makes the export match the screen.

**This reverses part of v4 §10.** v4 renamed the KPI card from `PORTFOLIO ALPHA`
to `XIRR SPREAD VS BENCHMARK PROXY (p.a.)`, and two `test_app.js` assertions pinned
that wording. The card now reads **`ALPHA (p.a.)`**.

The concern v4 had was never the word — it was a large headline figure implying
Jensen's α with nothing nearby to say otherwise. The on-screen table gets away with
"Alpha" because a footnote sits directly beneath it. So the guard was repointed at
the property rather than the string: **a sheet may state Alpha only if the same
sheet disclaims Jensen's α.** That is now enforced per rendered sheet on the real
workbook, not by a file-wide string search — a search would stay green if the cover
kept its Alpha row and lost its footnote.

Four sheets state Alpha and all four carry the disclaimer:

```
PASS  'Report Summary' states Alpha AND disclaims Jensen's alpha on the same sheet
PASS  'Portfolio — All' ...
PASS  'Portfolio — Live SIP' ...
PASS  'Method & Legend' ...
```

`Method & Legend` gained a full definition: a return spread in pp per year, with no
adjustment for volatility, beta or drawdown, and the note that a fund can show
positive Alpha purely by taking more risk than its index.

Also renamed for consistency: the cover metric row, the legend's threshold column
header, and one line of `buildInsights()` prose that still read "pp per year XIRR
spread" — that one is shared with the on-screen summary card, so the screen changes
too.

*Mutations N1 (drop the cover disclaimer) and N2 (drop the Portfolio footnote
disclaimer) — both go red, each naming the specific sheet.*
*Mutation N4 (revert the column to "XIRR spread") — red.*

## 2 — Bare column headings

`Benchmark (TRI proxy)` → `Benchmark`, `Fund XIRR (full)` → `Fund XIRR`.
`Fund XIRR (comparable)` keeps its qualifier — it is a different number and would
otherwise collide with the column beside it.

Dropping a parenthetical from a heading does not make the thing stop being true, so
both qualifiers were discharged into the Portfolio footnote instead:

- that the benchmark is a **proxy chosen by this tool from the fund's SEBI
  category**, not necessarily the fund's own disclosed benchmark;
- that Fund XIRR covers the **full SIP history**, while the comparable column
  restricts it to the months the benchmark also covers — and that the comparable
  pair is the only one Alpha is computed from.

The header-row assertion checks the header row specifically, **not** the sheet text.
An earlier draft searched the whole sheet for "TRI proxy" and "full"; it failed
against correct code, because the footnote and the Key Insights prose legitimately
use both words. Left as written, that test would have pressured exactly the
disclosures this section adds out of the file to make itself pass.

*Proof: `headings dropped their parenthetical qualifiers` (exact header row),
`the spread column is headed Alpha, not XIRR spread`, `the proxy disclosure
survived the loss of '(TRI proxy)'`, `the full-vs-comparable distinction survived
the loss of '(full)'`.*
*Mutation N3 — restore `(TRI proxy)` and the header-row equality goes red, printing
both rows.*

## 3 — Export Report button sized to the tabs

`.btn.compact` was `padding:6px 13px; font-size:12px`. It is now
`padding:9px 22px; font-size:.94rem; font-weight:500` — `.tabbtn`'s own metrics, so
the control reads as a peer of the tabs it sits beside.

Still `.compact` and not `.small`: `.small` is the destructive style (red border and
text — Clear all, Delete, Erase browser data, the row `×`).

The guard compares `.btn.compact` **to `.tabbtn`** rather than to a literal, so it
also fires if `.tabbtn` is restyled and the button left behind.

*Proof: `...sized as a peer of the tabs, using a modifier that is NOT the
destructive .small`, `...and .compact's font metrics actually equal .tabbtn's`.*
*Mutation N7 — red.*

## 4 — dd-mm-yyyy for stored dates, everywhere they are shown

The scheme sub-line now reads `₹1,500/mo from 14-01-2020 to 17-04-2022`.

New `fmtISO()` is the single definition. Applied at every point a stored ISO string
reached a reader: the leg note (which the table **and** the workbook's SIP column
both read), the retained-error row's sub-line, the import preview's Start column,
and the ranks `as_of` on the Insights pane, the Insights sheets and the cover.

`fmtDate()` is untouched — `01 Aug 2026` was explicitly kept, and a guard pins its
exact body so a later tidy-up cannot quietly fold it into `fmtISO`.

**Nothing stored changed.** `startStr`/`endStr` on disk, `signature()` keys and the
Import template all stay ISO. This is display only.

**Dashes, not slashes — and that is forced, not chosen.** `normaliseDateCell()`
reads a dashed `DD-MM-YYYY` as day-first, but rejects the slashed form as ambiguous
whenever both parts are ≤ 12. Verified behaviourally against the shipped parser
rather than asserted:

```
PASS  2020-01-14 displays as 14-01-2020 and re-imports unchanged
PASS  2022-04-17 displays as 17-04-2022 and re-imports unchanged
PASS  2019-11-03 displays as 03-11-2019 and re-imports unchanged
PASS  2021-06-08 displays as 08-06-2021 and re-imports unchanged
PASS  2024-02-29 displays as 29-02-2024 and re-imports unchanged
PASS  ...whereas the slashed form would be rejected as ambiguous
```

`08/06/2021` returns `ambiguous date; use YYYY-MM-DD or DD-MM-YYYY`. `14/01/2020`
happens to parse only because 14 > 12. Slashes would have broken re-import for
roughly a third of the calendar.

*Proof: the six assertions above, plus `there is one ISO-to-display date helper`,
`the leg note renders its dates through it`, `no raw ISO string is still printed to
the reader`, `stored dates stay ISO, so signature() keys and the importer are
untouched`, `the SIP column shows dd-mm-yyyy, not ISO`, `...and the day component
really is first`, `the Insights track-record as-of is reformatted too`.*
*Mutations N5 (raw ISO in the leg note), N6 (reformat `fmtDate` too) and N8
(switch to slashes) — all red.*

## Not done

- **The two `<input type="date">` fields are unchanged.** The browser renders those
  per OS locale; no attribute or CSS controls the format. Showing `dd-mm-yyyy`
  there means a text input with hand-rolled parsing and the loss of the native
  calendar picker. Not worth it without a request.
- **The Import template still writes ISO.** It is an input file for the importer,
  not a report. The importer would accept `dd-mm-yyyy` (§4 proves it), so this can
  change on request — but ISO is unambiguous to every reader and every spreadsheet
  locale, which a template benefits from more than a report does.
- Everything in `WONT_FIX.md` stands.


---

# v14 — the Excel export becomes a report

Same rule as v5–v13: **every claim names the test that proves it.**

This one is a requested change rather than an audit response, so the framing is
different: each section says what was wrong with the old output, what replaced it,
and which mutation proves the new guard actually bites.

## Verification

```
python3 tests/test_fetch_tri.py       36   unchanged
python3 tests/test_fetch_ranks.py    152   unchanged
python3 tests/test_probe_ranks.py     93   unchanged  (parses index.html at runtime)
python3 tests/test_mergers.py         17   unchanged  (parses index.html at runtime)
node    tests/test_app.js             33   unchanged
node    tests/test_matching.js       123   unchanged
node    tests/test_insights.js       166   + report shape, drift guards, button  (was 152)
node    tests/test_report.js          29   NEW — end-to-end, real workbook
```

**649 tests**, up from v13's 606.

`tests/test_report.js` is new in kind, not just in count. It loads the entire
inline script out of `index.html` into a VM with a stub DOM, points `fetch()` at
the committed `data/tri` and `data/ranks` files, drives the **real**
`exportReport()`, and reads the resulting workbook back with the same
`xlsx-js-style@1.2.0` build the page loads from the CDN. Nothing about the report
is re-implemented in the test, so the suite cannot pass against a shipped file
that no longer does what it says.

---

## 1 — The export contained none of the Insights tab (the actual request)

The v13 workbook was `Summary` / `Holdings` / `Legend`. `Summary` carried KPI cards
and `buildInsights()` lines; **no sheet carried anything from the Insights tab** —
no track record, no category rank, no peer list, no exclusion note. Everything that
tab computes was reachable only by expanding a row on screen.

**Fix:** the workbook now mirrors both tabs. Per sub-tab there is a Portfolio sheet
and an Insights sheet; the Insights sheet carries, per scheme, the track-record
table (6m→10y: Return, Category Avg, Annualized, Rank), the your-window heading,
the rank sentence, the exclusion breakdown and the Top-5 peer list with each peer's
gap against your own XIRR — ordered worst spread first, as on screen.

*Proof: `Insights carries the track-record table`, `...every published horizon`,
`...the peer window line`, `...the top-peer list`, `...and the fund's own position
in it`, `Insights ranks one row per scheme, not per leg`.*
*Mutation M8 — delete the `reportInsightsSheet` append and `sheet order and names`
goes red, reporting the v13-shaped four-sheet workbook it produced instead.*

## 2 — The exported table did not reconcile to the screen

The Portfolio pane renders `groupHoldings(scoped)` — one **pooled** row per scheme.
`exportExcel()` iterated `valuedSchemes()` — one row per **SIP leg**. A 13-fund
portfolio entered as 23 legs exported 23 rows against 13 on screen, and
`buildInsights()` inherited the same list, so "Best performer vs its TRI" could name
one fund twice because each of its legs carried its own alpha.

**Fix:** the report reads the same pooled groups the screen does, and
`buildInsights()` is fed `groups`. The SIP column carries the pooled row's own
description — `2 SIP legs pooled · ₹1,500 · ₹10,000/mo · from 2018-02-05` — from
`legNoteText()`, which the table renderer now also calls, so the sentence explaining
a pooled row has exactly one definition.

**No leg-level sheet.** The utility does not show legs, so the workbook does not
either. This reverses the reasoning of v13 §6, which kept leg-level rows for
round-trip losslessness — that argument was already weak, because the real
round-trip is the **Import template** button, which carries the `Code` column the
export never had, and codes are what survive SEBI's renaming.

*Proof: `the All sheet pools the two legs into one row`, `...and says how many legs
are behind it`, `the report mirrors the screen: pooled groups, not raw legs`, `the
leg note has one definition, read by the table and the sheet`, `there is no
leg-level sheet — the utility does not show legs either`.*
*Mutation M4 — render `d.legs` instead of `d.groups` and `the All sheet pools the
two legs into one row` goes red.*

## 3 — One unlabelled population, replaced by two labelled ones

v13 §6 chose to **label** rather than filter: export every leg and say so in the
subtitle. That was the right call given one sheet, but the underlying defect —
a headline XIRR the reader cannot reproduce from the screen they exported from
(measured then: 13.73% on Live SIP against 14.75% across all legs) — is better
fixed by showing both.

**Fix:** when any holding has an end date, the workbook carries
`Portfolio — All`, `Portfolio — Live SIP`, `Insights — All` and
`Insights — Live SIP`. When none does, the two sub-tabs are identical and the
sheets are named plainly: `Portfolio` and `Insights`. A `— All` suffix with nothing
to contrast against invites the reader to hunt for a sheet that was never going to
exist.

Sheet sets: **4 sheets** (`Report Summary`, `Portfolio`, `Insights`,
`Method & Legend`) or **6** with both views.

*Proof: `six sheets when a Live SIP view exists`, `sheet order and names`, `four
sheets when nothing has an end date`, `...and the sheets are named plainly, with no
'All' to contrast against`, `the Live SIP sheet drops the fully-ended scheme`,
`...and keeps the scheme that is still being funded`, `the Live SIP Insights sheet
drops the ended scheme`.*
*Mutations M1 (always use the two-scope names) and M2 (build the Live sheet from
the All population) — both go red.*

## 4 — Each Portfolio sheet carries its own KPI block

The cards, verdict chip and Key Insights sit on the sheet whose population they
describe, not on the cover. A reader on `Portfolio — Live SIP` must be able to
reconcile the headline figures to the table directly beneath them; a single shared
block could only ever be right for one of the two views.

**This test was wrong on its first draft and is worth recording.** The original
assertion read the KPI card and the PORTFOLIO row out of the *same* sheet and
compared them. Under the mutation that feeds every sheet the All-scope metrics,
both moved together and the test stayed **green** — it proved internal consistency,
not correctness. That is precisely the v13 §2 failure mode: a test that passes under
both the correct and the broken state.

The assertion now derives the expected figure in the harness, from the holdings it
built, with no help from the workbook or from `reportScopeData()`:

```
before (same-sheet comparison):   M3 not caught — 27 passed, 0 failed
after  (independent derivation):  M3 caught    — FAIL  Live SIP: the KPI card
                                                 equals the LIVE invested total
```

*Proof: `All: the KPI card equals the invested total computed independently`,
`Live SIP: the KPI card equals the LIVE invested total, computed independently`,
`All: the PORTFOLIO row agrees with its own cards`, `Live SIP: the PORTFOLIO row
agrees with its own cards`, `the two views really are different populations, so one
KPI block could not serve both`, `each Portfolio sheet computes its own metrics for
its own scope`.*

## 5 — Drift guards, because the sheet now restates the screen

A report that repeats what a pane already renders is two implementations of one
claim. Five things were collapsed to one definition each, and the collapse is
asserted rather than assumed — the same protection `mf_universe.py` gives the
Python category filters.

| One definition | Read by |
|---|---|
| `insightFacts()` — the only `rankCandidates` call site in the file | pane + sheet |
| `rankSentence(sum, planLabel, em)` — `em` bolds for HTML, absent for Excel | pane + sheet |
| `excludedParts()` → `excludedNote()` (HTML) / `excludedText()` (plain) | pane + sheet |
| `periodRows()` → `periodTableHtml()` renders it | pane + sheet |
| `legNoteText()` | table renderer + sheet |

The `periodTableHtml` refactor is **output-identical**: all 152 pre-existing
`test_insights.js` assertions passed before a single new one was added, including
the seven that pin its exact markup and the CAGR-vs-cumulative colouring rule.

*Proof: `rankCandidates has exactly ONE call site, inside insightFacts`, `the rank
sentence has one definition, parameterised by medium`, `the exclusion counts have
one definition, rendered two ways`, `the track-record table is data first, HTML
second`, `the Insights sheets read the same facts the pane does`, `the rank sentence
in the sheet is the one insightFacts produced`.*
*Mutation M5 — add a second `rankCandidates` call inside the sheet writer and the
call-site guard goes red.*

## 6 — The button

`⤓ Export Report`, at the smaller size, on the tab row and hard right. Out of the
top action bar entirely, which also means it is reachable from both tabs.

Two things worth stating:

- **`.btn.small` was not reused.** It is the *destructive* style — red border and
  text, used by Clear all, Delete, Erase browser data and the row `×` buttons.
  Reusing it would paint a harmless action as a dangerous one. New `.btn.compact`
  sets metrics only; `.ghost` still supplies the brass outline.
- **The button is outside `role="tablist"`.** A non-tab child of a tablist is
  announced as a tab ("3 of 3") and is caught by the tabbar's ArrowLeft/ArrowRight
  handler. `.tabrow` is a plain flex container that *holds* the tablist. Visually
  identical; behaviourally correct.

The export is now async — it fetches per-category rank files and re-runs the peer
maths — so it disables itself and reads **Building report…** while it works.
Measured against the committed grids: five holdings across five categories over a
115-instalment window cost ~0.8 s of compute (ELSS alone, 52 funds, 574 ms on a cold
JIT) on top of ~1.09 MB of JSON; a portfolio spanning all 11 categories in both
plans would pull 3.4 MB. Files are fetched four at a time before any ranking runs,
and `loadRanksJson` caches by filename, so the two sub-tabs share the download.

*Proof: `the export button reads Export Report`, `...at the smaller size, using a
modifier that is NOT the destructive .small`, `...sitting on the tab row, outside
the tablist so arrow keys still work`, `the export is async and says so while it
works`.*
*Mutations M6 (swap `compact` for `small`) and M7 (drop the `.tabrow` wrapper) —
both go red.*

## 7 — A report sheet must not be mistaken for an import template

Renaming the holdings sheet to `Portfolio` collides with the importer, which
*prefers* a sheet of exactly that name over the first sheet. The hazard did not
exist while the sheet was called `Holdings`.

It is safe, but by construction rather than luck: a report sheet opens with a title
band, so row 1 is not a header row and the importer's classifier finds no Scheme /
Start / Monthly SIP columns, rejecting the file outright rather than half-reading
pooled amounts and the PORTFOLIO total row as holdings. Asserted, not assumed.

*Proof: `a report sheet is REJECTED by the importer rather than half-read as
holdings`.*

## Smaller items

- **Filename** `SIP_vs_Benchmark_…` → `SIP_Report_…`. *Proof: `the filename says
  Report, not the old vs-Benchmark name`.*
- **`FMT_PCTNUM`** (`0.00"%"`) added. `FMT_PCT` multiplies by 100, so the
  track-record figures — which are already in percent — would have printed 630.00%
  for 6.30%. *Proof: `a percentage that is already in percent gets its own number
  format`.*
- **`insightsItems(scope)`** takes an explicit scope, defaulting to `pfScope`. The
  report builds both views in one pass and cannot rely on whichever the screen
  happens to be showing.
- **`Matched` added to the legend.** `perfBand()` has always returned a distinct
  exactly-flat state; the legend listed only four of its five bands.
- **`tests.yml`** installs `xlsx-js-style@1.2.0` with `--no-save` and runs the new
  suite. `tests/test_report.js` SKIPs cleanly without the package, but a test that
  silently skips in CI proves nothing.
- **`.gitignore`** now ignores `node_modules/`.

## Not done

- **`ranks-fetch.yml` still does not gate on the JS suites.** Unchanged from v13,
  and the reasoning holds: it would roughly double the job's setup time, and the JS
  suites do not write `data/ranks/`.
- **The report does not follow `pfScope`.** It always writes every view the
  portfolio supports. Following the visible sub-tab would make the file's contents
  depend on which button was last clicked.
- **Peer lists are capped at `TOP_N` (5) in the sheet, as on screen.** A full
  ranking table per holding is a different document.
- Everything in `WONT_FIX.md` stands, including **#13**.


---

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


---

# v12 — MFAPI's plural category strings

Same rule as v5–v11: **every claim names the test that proves it.**

Ships on top of v11.

## Verification

```
python3 tests/test_probe_ranks.py    #  93 — + singular/plural category  (was 71)
python3 tests/test_mergers.py        #  17 — unchanged
python3 tests/test_fetch_tri.py      #  36 — unchanged
python3 tests/test_fetch_ranks.py    # 141 — unchanged
node    tests/test_app.js            #  33 — unchanged
node    tests/test_matching.js       # 121 — unchanged
node    tests/test_insights.js       # 138 — unchanged
```

**579 tests.** Mutation-checked: **10 of the 22 new assertions fail** against the
previous `mf_universe.py` + `index.html`.

---

## The failure

```
Not imported — Invesco India Contra Fund - Regular Plan - Growth:
  is a "Equity Schemes - Contra Fund" scheme; this tool only benchmarks
  actively-managed equity funds against a Nifty equity TRI
```

Contra and Mid Cap are categories this tool **fully supports** and had been
importing for months. Four holdings — three Invesco Contra legs and Invesco
Midcap — were rejected by a guard meant for debt and hybrid funds.

## The cause

MFAPI serves **both** `"Equity Scheme - X"` and `"Equity Schemes - X"` for the same
SEBI category, and flips schemes between the two without notice.

`CATEGORY_CANON` is keyed on the singular. Anything plural misses every key,
`categoryKey()` returns `null`, `isUnsupportedCategory()` fires, and
`computeScheme()` throws — so the fund cannot be added to a portfolio at all.

**This is a known problem that was fixed too narrowly.** v8 hit it on thematic
funds and added a single plural *key*:

```js
// MFAPI also serves a PLURAL variant on ~20 live schemes ("Equity Schemes -
// Thematic Fund"). Without this key categoryKey() returns null ...
"equity schemes - thematic fund":        "SECTORAL",
```

That fixed those twenty schemes and left every other category exposed. The plural
has since spread to Contra and Mid Cap. Enumerating plural keys only ever fixes
the categories that have already broken — one bug report at a time.

**Not caused by v9–v11:** the plural-key count is identical (1) in the v8
`index.html` and in v11's.

## The fix

Fold the plural away in the **normaliser**, so every category is covered at once —
including ones MFAPI has not flipped yet:

```js
.replace(/^equity schemes -/, "equity scheme -")
```

and the identical rule in `mf_universe.py`'s `norm_category()`. Both must change
together: a fund the client accepts but the nightly job rejects silently vanishes
from the rankings with nothing erroring.

The fold is **anchored** and category-specific. `Debt Schemes - Liquid Fund` and
`Other Schemes - Index Funds` are still rejected, and
`Fund of Equity Schemes - Contra Fund` is left alone — the guard's whole purpose is
refusing things a Nifty equity TRI cannot fairly benchmark, and that is unchanged.

The v8 `"equity schemes - thematic fund"` key is now redundant but kept: it is
harmless, and it documents where this was first found.

*Proof: nine plural categories resolving correctly (`'Equity Schemes - Contra Fund'
resolves to CONTRA` and siblings), two singular ones still resolving, six non-equity
strings still rejected, `the fold is anchored at the start of the string`,
`index.html folds the plural too — or the client and the nightly job disagree`, and
`...and the fold is anchored there as well`.*


---

# v11 — HSBC Midcap: joining a scheme's history back across its merger

Same rule as v5–v10: **every claim names the test that proves it.**

Builds on v10. This `index.html` carries v9, v10 and v11 together.

## Verification

```
python3 tests/test_mergers.py        #  17 — NEW: splice gates + JS/Python drift guard
python3 tests/test_fetch_tri.py      #  36 — unchanged
python3 tests/test_fetch_ranks.py    # 141 — unchanged
python3 tests/test_probe_ranks.py    #  71 — unchanged
node    tests/test_app.js            #  33 — unchanged
node    tests/test_matching.js       # 121 — unchanged
node    tests/test_insights.js       # 138 — + JS splice parity  (was 125)
```

**557 tests.** Mutation-checked three ways, all fired:

- retired code changed in `index.html` only → `test_index_html_chain_matches_this_module`
- ratio tolerance changed in `index.html` only → `test_index_html_tolerances_match` **and**
  two JS assertions (`a 4% move is refused`, `ratio tolerance matches mf_mergers.py`)

---

## The problem

`HSBC Midcap Fund - Regular Growth` (151034) has no NAV before **2022-11-28**. A SIP
that ran 2020-01-15 → 2022-04-18 could not place a single instalment, so `runSIP`
returned null and the holding was dropped at import:

> No valid SIP instalment could be placed for HSBC Midcap Fund - Regular Growth

Not a bug — the utility refused correctly. The history was simply somewhere else.

## What the data showed

Every HSBC/L&T code in the published grids:

| Codes | First NAV | What they are |
|---|---|---|
| 101594, 102252, 104707, 120030/46/79 | 2015-07-27 (grid start) | original HSBC — kept their codes |
| 146771/2, 148409/11 | their launch dates | original HSBC, later launches |
| **151034/36, 151076/78, 151110/13, 151130/33** | **all 2022-11-28** | **renamed L&T schemes** |

Midcap, ELSS Tax Saver, Value and Small Cap all starting on the same day is the
signature of **new AMFI codes**, not of four simultaneous launches. The pre-merger
history lives under the retired L&T codes, which mfapi still serves.

Finding them took three attempts because `/mf/search` caps at 15 results and the
retired schemes are spelt **"L&T Mid Cap Fund"** — with a space. Searching
"L&T Midcap" returns only `L&T Large and Midcap Fund`, a **different scheme**, which
is deliberately *not* chained.

- `112496` L&T Mid Cap Fund - Regular Plan - Growth → `151034`
- `119807` L&T Mid Cap Fund - Direct Plan - Growth → `151036`

## Why a plain concatenation is right

HSBC Midcap opens at **₹210.96** (Regular) and **₹231.80** (Direct). A newly
launched scheme starts at its ₹10 NFO price; opening in the 200s identifies the
series as the **renamed continuation** of the survivor. HSBC's notice says renamed
schemes had a name change only, with no change in NAV or investment value — it was
the schemes merged *into* a survivor that had NAV recomputed. L&T Mid Cap was the
survivor, so units carry 1:1 and **no ratio adjustment applies**.

Which is an assertion about data, so it is checked rather than trusted.

## The splice gate

`splice_problem()` / `spliceProblem()` refuse the join unless:

- **the NAV is continuous across it** — ratio within 3% of 1.0. A rename carries NAV
  through untouched, so a real jump means either a ratio merger or the wrong retired
  code. Splicing it would weld a step-change into the middle of a return history and
  invent performance that never happened.
- **the join opens no hole wider than 10 days** — beyond the 7-day SIP placement
  window an instalment scheduled in the gap silently vanishes.
- **both sides actually have rows** on their side of the splice date.

A refused splice returns the **new series intact**, so it degrades to exactly v10's
behaviour. It never drops a fund and never fabricates history — the same posture as
`fetch_tri.py`'s continuity gate.

Rows before the splice date come from the retired code, rows on/after from the
surviving one, so a date present in both is counted once with the survivor's value.
That matters: two rows for one day make `navOnOrAfter` and `navOnOrBefore` disagree,
and a SIP then buys at one NAV and is valued at another on the same day.

*Proof: `test_a_clean_rename_splices`, `test_a_ratio_merger_is_refused`, `test_a_small
_market_move_across_the_join_is_allowed`, `test_a_move_just_past_tolerance_is_refused`,
`test_a_long_hole_at_the_join_is_refused`, `test_the_new_code_wins_on_overlapping_dates`,
`test_missing_or_empty_sides_are_refused_not_crashed`, `test_non_positive_nav_at_the
_join_is_refused`, plus the nine JS parity assertions in `test_insights.js`.*

## Both sides splice, or neither

`fetch_ranks.py` applies the same chain when building peer grids. Splicing only the
portfolio side would leave Insights reporting *"its history does not cover this
window"* for a fund whose Portfolio row starts in 2020 — two panes disagreeing about
one fund, the defect class v9 #2 and v10 #2 were both about.

`index.html` cannot import Python, so it carries its own copy of the map. Two copies
drift, so `test_mergers.py` parses `index.html` at runtime and fails if the maps or
the tolerances diverge — the guard `mf_universe.py` already gives category filters.
The suite runs in `tests.yml` and as a gate in `ranks-fetch.yml`.

*Proof: `test_index_html_chain_matches_this_module`, `test_index_html_tolerances_match`,
`test_index_html_actually_uses_the_chain`, `test_fetch_ranks_applies_the_chain`,
`test_no_retired_code_feeds_two_survivors`, `test_no_chain_forms_a_cycle_or_a_multi_hop`.*

---

## Not done

- **The other three L&T pairs** (ELSS Tax Saver, Value, Small Cap) are visible in the
  data and unquestionably the same case, but their retired codes have not been
  verified against mfapi. Adding them is two lines each in `mf_mergers.py` plus the
  matching lines in `index.html` — **no code is guessed.**
- **The first run must be checked, not assumed.** See below.
- Everything in `WONT_FIX.md` stands, including **#13**.

## What to check on the first run

The splice ratio could not be verified offline: the sandbox cannot reach mfapi, so
`112496`'s last NAV is unknown here. The gate is what makes shipping this safe — if
the ratio is wrong the splice is refused and nothing regresses — but you should
confirm it actually engaged.

1. **Portfolio tab.** HSBC Midcap should now import and value. Expect roughly
   ₹84,000 invested and a current value near ₹2.42L, matching the NJ Wealth figures
   (533.180 units × the current NAV).
2. **Nightly ranks log.** Look for `chained 151034<-112496 from <date>`. If it says
   `CHAIN REFUSED`, the message names the reason — paste it back and the tolerance or
   the code gets corrected rather than loosened.


---

# v10 — one row per scheme, and a SIP schedule that admits amounts change

Same rule as v5–v9: **every claim names the test that proves it**, and nothing is
listed as done unless the test fails against the previous build and passes here.

Supersedes the v9 `index.html`. If v9 was never committed, this file carries both.

## Verification

```
python3 tests/test_fetch_tri.py      #  36 — unchanged
python3 tests/test_fetch_ranks.py    # 141 — unchanged
python3 tests/test_probe_ranks.py    #  71 — unchanged
node    tests/test_app.js            #  33 — unchanged
node    tests/test_matching.js       # 121 — unchanged
node    tests/test_insights.js       # 125 — pooling, scope, schedules  (was 99)
```

**527 tests.** The six shipped-file guards all fail against v8/v9, and
`test_insights.js` cannot even load against it (`function not found:
uniformSchedule`) — the signature change is that structural.

---

## 1. A SIP schedule is now `[{date, amount}]`, not dates plus one flat amount

**`index.html` — `runSIP()`, `rankCandidates()`**

The portfolio behind this change runs 23 SIP legs across 13 funds because the
monthly amount changed over time: 1,500 → 10,000 → 3,000 in the same scheme.
`runSIP(navArr, dates, amount, valueDate)` could not express that, and
`rankCandidates()` handed **every peer a single flat amount**.

That is not a like-for-like ranking. Money-weighted return depends on *when* each
rupee went in; a 10,000 leg landing in 2022 dominates the result in a way a flat
schedule can never reproduce. Peers were being run on a contribution pattern the
holding never had.

`runSIP(navArr, schedule, valueDate)` now takes the schedule the holding actually
ran, and peers receive that same schedule. `uniformSchedule(dates, amount)` covers
the ordinary single-amount case, so existing call sites are unchanged in behaviour.

Only four call sites existed: `fundFull`, `fundCmp`, `benchCmp`, and the peer loop.

*Proof: `the schedule carries per-date amounts, not one flat figure`, `two legs
sharing a date sum their amounts`, `runSIP takes a schedule`, `rankCandidates takes
a schedule`, `peers are run on the holding's own schedule`.*

## 2. The Portfolio and Insights panes show one row per scheme

**`index.html` — `poolRuns()`, `groupHoldings()`, `render()`, `insightsItems()`**

23 rows with the same fund name repeated three times was unreadable, and Insights
was worse than unreadable: it listed each fund once per leg and ranked each slice
against peers who had run the whole window.

Legs of one scheme are **one position**, and its return is a single XIRR over the
union of their cash flows — **not the average of the legs' XIRRs**, which is wrong
whenever the legs differ in size or timing. The test proves both halves: pooled
matches a single run over the merged schedule to within `1e-9`, and differs from
the mean of the legs by more than `1e-6` on real Nifty Midcap 150 data.

`poolRuns()` needs no NAV array and no re-valuation. Every leg of a scheme shares
one NAV series and therefore one struck valuation date, so each leg's
`currentValue` is already `units_i × nav`; summing gives `(Σ units) × nav`. That
equivalence is guarded rather than assumed — legs struck on different dates refuse
to pool instead of adding rupees measured at two different prices.

Groups deliberately mimic a scheme object, so `schemeView()`, `perfBand()`,
`triWindowFlag()` and the row renderer work on them unchanged.

Run end-to-end against the real 23-row sheet: **12 grouped rows under All, 9 under
Live SIP**, with `PORTFOLIO` equal to the sum of the rows drawn above it in both.
(The 13th is HSBC Midcap — see below.)

*Proof: `pooled XIRR equals a single run over the merged schedule`, `...and is NOT
the mean of the legs' XIRRs`, `legs struck on different dates refuse to pool`, `a
single leg pools to itself`, `three legs across two funds collapse to two rows`,
`the collapsed row's invested equals the sum of its legs`, `a group still satisfies
schemeView()`, `Insights shows one row per scheme, not per leg`, `the Portfolio pane
renders groups, not raw legs`, `the PORTFOLIO total row is computed from the same
groups`.*

## 3. `All` / `Live SIP` sub-tabs

**`index.html` — `scopeLegs()`, `scopeApplies()`, `renderScopeBar()`**

**`Live SIP` keeps only legs with no end date** — the leg you are still funding,
not every leg of a scheme that happens to have one. Canara Robeco's live row
reports ₹1,56,000 invested at 9.24%, against ₹3,28,000 at 11.58% for the whole
position. The tab answers "how is the money I am putting in *now* doing", so an
ended leg's history must not inflate it.

The control renders in both panes and shares one state: switching tabs while
looking at Live SIP and silently landing on All would misreport every figure on
arrival. It appears only once at least one holding has an end date — with none,
the two views are identical and the control is noise. If *every* holding has one,
the pane says so rather than drawing a table of zeros.

`portfolioMetrics()` now takes the rows being displayed, so the `PORTFOLIO` line
recomputes per sub-tab. The headline XIRR legitimately differs between All (14.75%)
and Live SIP (13.73%) — that is the filter working, not a discrepancy.

Export is untouched and still writes **every leg**, so a round-trip preserves the
23 rows rather than collapsing the file to 13.

*Proof: `scopeApplies() is true once any leg has an end date`, `scopeApplies() is
false when nothing has an end date`, `live scope drops the ended leg`, `the
multi-leg fund survives with only its live leg`, `...and its invested is the live
leg alone, not the whole position`, `Live SIP drops the fully-stopped scheme`.*

## 4. Ended legs stop at their own end date

Carried forward from v9 and now enforced in `groupSchedule()`: each leg is bounded
by **its own** `endStr`. The scheme object has never had an `end` property, and
reading one scheduled stopped SIPs all the way to today — 103 instalments where 37
were correct, printing 13.41% where the row header said 14.90%.

*Proof: `the ended leg stops at ITS end date, not today`, `the live leg runs to the
valuation date`, `index.html no longer reads the non-existent s.end`.*

---

## Not done

- **The HSBC Midcap merger chain.** `HSBC Midcap Fund - Regular Growth` (151034)
  has no NAV before 2022-11-28, so a SIP that ended 2022-04-18 cannot place a
  single instalment and the holding is dropped at import. The scheme is the renamed
  survivor of L&T Midcap — its first NAV is ₹210.96, not a ₹10 NFO price, which is
  what proves the series is a continuation and that the splice needs no ratio
  adjustment. Blocked only on the pre-merger scheme code: `/mf/search` caps at 15
  results and neither probe surfaced it. **No code is hard-coded on a guess.**
- **The chain in `fetch_ranks.py`.** Once the portfolio side splices, the peer grid
  must too, or Insights will report "history does not cover this window" for a fund
  whose Portfolio row starts in 2020 — two panes disagreeing about one fund, the
  same defect class as v9 #2.
- Everything in `WONT_FIX.md` stands, including **#13, local-timezone date
  arithmetic**.


---

# v9 — a benchmark that had been dead for nine days, and a tab ranking the wrong window

Same rule as v5–v8: **every claim names the test that proves it**, and nothing is
listed as done unless the test fails against v8 and passes against this one.

## Verification

```
python3 tests/test_fetch_tri.py      #  36 — continuity, name drift, staleness audit
python3 tests/test_fetch_ranks.py    # 141 — unchanged
python3 tests/test_probe_ranks.py    #  71 — unchanged
node    tests/test_app.js            #  33 — unchanged
node    tests/test_matching.js       # 121 — matching, routing, benchmark reachability
node    tests/test_insights.js       #  99 — ranking, NAV parsing, SIP window, plan
```

**411 → 426 tests.** Mutation-checked: **15 of the new tests fail against v8** —
11 in `test_insights.js`, 3 in `test_matching.js`, 1 in `test_fetch_tri.py`.

---

## 1. `NIFTY_HEALTHCARE` stopped updating on 20 Jul and nothing said so (HIGH)

**`fetch_tri.py` — `INDEX_MAP`**

`INDEX_MAP["NIFTY_HEALTHCARE"]["name"]` had been edited from `"NIFTY HEALTHCARE"`
to `"NIFTY HEALTHCARE INDEX"`. The endpoint answers a wrong canonical name with an
**empty list, not an error**, so `fetch_index()` logged its "likely wrong canonical
name; skipping" line, the index kept its last-good file, and — because healthcare
is an *optional* index — `main()` exited **0**.

The committed data proves both the cause and the date. `rows_to_doc()` writes
`"index": name` verbatim from the config, so every published file carries the exact
string that last fetched it:

```
KEY                CONFIG name              COMMITTED index      end
NIFTY_HEALTHCARE   NIFTY HEALTHCARE INDEX   NIFTY HEALTHCARE     2026-07-17
```

Exactly **1 of 39** indices had drifted, and it is exactly the one the manifest
flagged `fresh: false`. Row count confirms it: 5,281 against 5,286 for every other
index sharing the 2005-04-01 start — precisely the five trading days 20–24 July.

**Fix, three parts:**

1. The name is reverted to `"NIFTY HEALTHCARE"`.
2. `test_committed_index_names_match_the_configured_canonical_names()` diffs every
   `INDEX_MAP` entry against the `index` field of the file it produced. The same
   edit now goes red on the next push instead of nine days later.
3. `fetch_tri.py --audit` reads the committed manifest and exits non-zero once any
   index has been frozen beyond `MAX_OPTIONAL_STALE_DAYS` (10). `tri-fetch.yml`
   runs it as a step **after** the commit, so a wedged sector index turns the
   Action red without ever blocking the 38 that fetched correctly.

The soft gate itself is unchanged and still correct: a bad optional fetch must not
overwrite good broad-market data. What was missing was escalation — `fetch_ranks.py`
already ends with `if failed: return 1` for exactly this reason, and the two jobs
now agree.

*Proof: `test_committed_index_names_match_the_configured_canonical_names`,
`test_every_index_map_entry_has_a_file_named_after_its_key`,
`test_audit_passes_when_every_series_is_current`,
`test_audit_fails_on_a_persistently_stale_optional_index`,
`test_audit_marks_a_stale_required_index_as_required`,
`test_audit_fails_on_an_entry_with_no_end_date`,
`test_audit_respects_a_custom_ceiling`,
`test_audit_fails_loudly_on_a_missing_or_unreadable_manifest`.*

## 2. The Insights tab ignored every SIP end date (HIGH)

**`index.html` — `insightsItems()`, `fillInsightDetail()`**

`insightsItems()` built its item with `end: s.end`. **The scheme object has never
had an `end` property** — `computeScheme()` stores the SIP end as the string
`endStr`. `end:` appeared exactly once in 3,548 lines, as that read.

So `item.end` was always `undefined`, and

```js
const dates = scheduleDates(item.start, item.end||item.valueDate);
```

always fell through to the fund's **latest NAV date**. Every holding with an end
date was ranked, and its peer list built, over a window the user never invested
through. Measured against the committed `navs_LARGE_CAP_Direct.json`:

| SIP | instalments scheduled | own XIRR shown |
|---|---|---|
| 2018-01 → 2021-01 | 103 (should be 37) | 13.41% (should be 14.90%) |
| 2022-03 → 2023-09 | 53 (should be 19) | 9.32% (should be 12.23%) |

Worse than the size of the error is that it contradicted itself on screen: the row
header prints `item.xirr`, which **is** correctly end-bounded because it comes from
the portfolio. So the same fund showed two different XIRRs in the same expanded
row, up to ~3 pp apart, and the footer's claim that "peers are ranked over **your**
SIP dates" was false for exactly those holdings.

**Fix:** `end: s.endStr ? parseInput(s.endStr) : null`. The valuation date is
deliberately left at the fund's latest NAV — that matches `computeScheme()`, which
values a stopped SIP at today's price because the units are still held. Only the
schedule was wrong. When an end date is present the pane now states the window
explicitly, so the figures can be reconciled against the portfolio tab.

*Proof: `an end-dated holding exposes a real Date for item.end`, `...and it is the
stored endStr, not the valuation date`, `an open-ended holding leaves item.end
null`, `a stopped SIP schedules only its own instalments`, `...which is far short of
the to-today schedule the old code used`, `index.html no longer reads the
non-existent s.end`, `index.html derives the insights end date from endStr`.*

## 3. `NIFTY_INDIA_DIGITAL` was fetched nightly and unreachable (LOW)

**`index.html` — `SECTOR_KEYWORDS`**

The TRI file was fetched every night, validated, committed — and no route in the
file could select it. The bare word `"digital"` belongs to the `NIFTY_IT` entry,
and no index listed `NIFTY_INDIA_DIGITAL` as its `.fb` target either.

**Word order is the whole distinction, and getting it wrong would have been worse
than leaving it dead.** "Digital India" funds are *technology* funds: Aditya Birla
Sun Life's own SID describes it as "an open ended equity scheme investing in the
Technology, Telecom, Media, Entertainment and other related ancillary sectors", and
its stated benchmark is BSE Teck TRI. Only a fund named after the **index**
("… India Digital …") belongs on `NIFTY_INDIA_DIGITAL`.

So the new rule matches `"india digital"` only, and sits **above** the IT entry
because that entry also matches the substring `"digital"` — the same
most-specific-first ordering rule as `"psu bank"` before `"bank"`.

A reachability audit was added alongside it: every `BENCH_FUNDS` key must be
selectable by some route, except the six that are intentionally fallback-only
(`NIFTY50`, `NIFTY200`, `NIFTY_NEXT_50`, `NIFTY_TOTAL_MARKET`, `NIFTY_MIDCAP100`,
`NIFTY_SMALLCAP100`).

*Proof: `a fund named after the index reaches Nifty India Digital TRI`,
`...regardless of the AMC prefix`, `Aditya Birla Digital India stays on Nifty IT (it
is a technology fund)`, `Tata Digital India stays on Nifty IT`, `a plain technology
fund is unaffected`, `india-digital rule is declared before the IT rule`, `no
benchmark is fetched nightly but unreachable by every route`.*

## 4. The continuity gate had no escape hatch (LOW)

**`fetch_tri.py` — `main()`**

`continuity_problem()` refuses any series missing the prior terminal date. That is
right almost always and unrecoverable the rest of the time: if NSE ever revises or
withdraws a committed date, **every future run fails on it and the index is wedged
permanently** with no way to publish a correction. `fetch_ranks.py` already carried
`--force` for exactly this case; `fetch_tri.py` had no argparse at all.

Added: `--force` (bypass continuity, logging the override), `--dry-run`, `--only KEY`
(repeatable, for reproducing one index), and `--audit` from finding #1. `--only`
skips the required-index gate — a subset run cannot satisfy it by construction —
and never writes the manifest, since a partial run cannot describe the whole
dataset without republishing every absent index as stale.

*Proof: `test_force_is_available_as_a_continuity_escape_hatch`.*

## 5. An unranked fund was always blamed on its history (LOW)

**`index.html` — `fillInsightDetail()`**

One message covered two different failures: "Your fund could not be ranked over
this window (its history does not cover it)." That is right when the fund is in the
loaded cohort but too young for the window, and **wrong** when the fund simply is
not in that cohort file at all.

The second case is reachable through plan inference. `insightsItems()` defaults an
undetectable plan to `"Regular"` — correct, because MFAPI names a Direct plan
explicitly but usually says nothing for Regular — so a Direct holding whose plan
could not be read loads `navs_<CAT>_Regular.json`, finds no matching code, and was
told its history was too short. That sends the user to check a date range when the
real problem is the plan.

**Fix:** `item.planInferred` records whether the plan was stated or assumed, and
the pane now checks whether the code is present in the loaded grid at all. Absent
from the cohort and absent from the eligible pool now read differently, and an
inferred plan says so and names the remedy.

The `"Regular"` default is unchanged — it is the right guess, it just is not a
fact, and the UI no longer presents it as one.

*Proof: `an undetectable plan still defaults to Regular`, `...but is flagged as
inferred, not stated`, `a plan read from the name is NOT flagged as inferred`, `an
explicitly stored plan is NOT flagged as inferred`, `fillInsightDetail distinguishes
a missing plan cohort from a short history`.*

---

## Not done (deliberate)

- Everything in `WONT_FIX.md` stands unchanged. In particular **#13,
  local-timezone date arithmetic**, was re-raised during this review and re-closed:
  the DST failure is real and reproducible (a 7-calendar-day gap spanning a
  fall-back measures 7.0417 days under `TZ=America/New_York` and `navOnOrAfter`
  skips the instalment), but India has no DST and the fix touches every date path.
  The existing reasoning was not improved on. Logged here only so the next audit
  cycle finds it already answered.
- **Prototype keys in the portfolio store.** `loadStore()`'s
  `if(!store.portfolios[store.active])` guard is truthy for inherited names, so a
  corrupted store carrying `active:"constructor"` survives and `hydrateActive()`
  throws. Reachable only by hand-editing `localStorage`, and the import path — the
  one that takes untrusted input — already has its own `Object.prototype` tripwire.
  Left alone.
- **The Insights `as-of` date** is taken from `items[0].valueDate`, which after the
  alpha sort is the worst laggard rather than the newest holding. Cosmetic; every
  per-holding figure is already dated correctly.


---

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


---

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