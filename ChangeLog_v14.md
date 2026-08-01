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