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