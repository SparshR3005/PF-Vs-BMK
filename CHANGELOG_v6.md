# v6 — wrong-fund import fix, SEBI category guard, 39 benchmarks

Same rule as v5: **every claim here names the test that proves it**, and nothing is
listed as done unless the test fails against the previous code and passes against
this one.

## Verification

```
python3 tests/test_fetch_tri.py     # 11  — TRI continuity gate, fetch resilience
node    tests/test_app.js           # 33  — export, import, retry, storage, search
node    tests/test_matching.js      # 79  — matching, category guard, benchmarks
```

All three run on every push (`.github/workflows/tests.yml`).

---

## 1. Wrong fund imported from Excel (the reported bug)

A sheet row reading **"ICICI Prudential Large Cap Fund"** silently imported
**"ICICI Prudential Large & Mid Cap Fund"** — a different SEBI category with a
different benchmark. Reproduced exactly at the reported 89% before any fix.

**Root cause — not what it looked like.** SEBI's *Recategorisation 2.0* (circular
Mar-2025, deadline 30-Jun-2025) renamed schemes industry-wide to match their SEBI
category. ICICI Prudential **Bluechip Fund** became **Large Cap Fund** on
16-Jun-2025. MFAPI records the new name with a rename note attached:

> `ICICI Prudential Large Cap Fund (erstwhile Bluechip Fund) - Direct Plan - Growth`

That note poisons bigram matching. Measured against the unfixed code:

| Candidate | Score | Outcome |
|---|---|---|
| **Correct** — Large Cap Fund *(erstwhile Bluechip Fund)* | **72.1%** | below the 0.78 floor → **rejected** |
| **Wrong** — Large & Mid Cap Fund | **89.4%** | clears floor + margin → **auto-accepted** |

The correct fund was *penalised* by the rename note and lost to the wrong one. No
threshold tuning fixes that — the right answer scores below the floor while the
wrong one clears it.

**Fix (three layers, defence in depth):**

- **`stripRenameNote()`** removes `(erstwhile …)` / `(formerly …)` / `(previously …)`
  before keying. Both sides normalise to `iciciprudentiallargecap` → **exact match**,
  so the fuzzy path never runs.
  *Proof: `BUG: typed 'Large Cap Fund' now EXACT-matches the real Large Cap fund`,
  `erstwhile note is stripped from the key`.*
- **Category guard.** If the typed name claims a cap tier that contradicts the
  resolved fund's real SEBI category, the holding is refused with an explanation
  instead of a confident spread against the wrong benchmark.
  *Proof: `GUARD: 'Large Cap' name vs Large & Mid Cap fund is BLOCKED` (+7).*
- **Rename aliases.** Pre-2025 sheets still resolve: `Bluechip → Large Cap`,
  `Mid Cap Opportunities → Mid Cap`, `Equity Opportunities → Large & Mid Cap`,
  `Value Discovery → Value`, `Emerging Equities`/`Core Equity`/`Growth Opportunities
  → Large & Mid Cap`.
  *Proof: `rename: ICICI Bluechip (old sheet) resolves to Large Cap Fund` (+5).*

## 2. Ambiguous rows now offer a pick list (was: silent guess)

When a name can't be resolved confidently — or when the top hit's SEBI category
contradicts the typed name — the import preview shows a **dropdown of candidate
schemes** instead of guessing or just failing. Choosing one validates the code,
loads the real name and category, and marks the row *"Confirmed manually"*.

*Proof: `import offers candidates when it can't resolve`.*

## 3. MFAPI's dirty category field was reaching Nifty 500

`meta.scheme_category` is **not** clean. Live data contains `"1"`, `"1099 Days"`,
`"Growth"`, `"Income"`, `"IDF"`, `"Payout"` and `"Formerly Known as IIFL Mutual
Fund"` on legacy/closed records.

The old gate **blocklisted** known-bad categories and returned "supported" for
anything unrecognised — so every one of those junk values fell through to
`FALLBACK_KEY` and was **benchmarked against Nifty 500**. A scheme whose category
reads `"1099 Days"` is a fixed-maturity debt plan; printing an XIRR spread vs Nifty
500 for it is a confidently wrong number in a client report.

**Fix:** inverted to an **exact-match allowlist** (`CATEGORY_CANON`). Known
benchmarkable equity categories map to a canonical key; everything else is rejected
with a clear message. Fails closed.

Exact match is deliberate: `"mid cap"` **is** a substring of
`"Equity Scheme - Large & Mid Cap Fund"`, so a substring test would put Large & Mid
Cap funds on the Nifty Midcap 150 benchmark. The old resolver avoided this only
because `"large & mid"` happened to be listed before `"mid cap"` — correct by
ordering luck, not by construction.

*Proof: 11 accept cases, 8 junk rejections, 11 non-equity rejections,
`exact match, not substring: 'Large & Mid Cap' is not read as MID_CAP`.*

Every category string in the tests was read from a live MFAPI response.

## 4. Benchmarks: 17 → 39 indices

`INDEX_MAP`, `BENCH_FUNDS`, `SECTOR_KEYWORDS` and `CATEGORY_DEFAULTS` all extended.

**Cap-tier routing is now keyed on the exact SEBI category** (`CATEGORY_KEY_BENCH`)
rather than a substring of it. Both mappings were **verified against AMC
disclosures**, not assumed:

| Category | Benchmark | Source |
|---|---|---|
| Large Cap | **Nifty 100 TRI** | icicipruamc.com, ICICI Pru Large Cap Fund |
| Large & Mid Cap | **Nifty LargeMidcap 250 TRI** | ICICI Pru Large & Mid Cap factsheet |

Your existing rules were already correct — the matcher was applying the right rule
to the wrong scheme.

**New broad market:** Nifty 200, Next 50, Total Market, Midcap 100, Smallcap 100,
Microcap 250, MidSmallcap 400.
**New sectoral:** Healthcare (distinct from Pharma), Private Bank, PSU Bank,
Consumer Durables, Oil & Gas, Metal, Realty, Media, CPSE, Commodities, MNC, India
Defence, India Manufacturing, India Digital, Transportation & Logistics.

`SECTOR_KEYWORDS` is now **ordered most-specific-first** — `psu bank` before `bank`,
`consumer durables` before `consumption`, `oil & gas` before `energy` — the same
substring trap as #3.

Sector routing stays **name-based** by necessity: MFAPI collapses every sector fund
into the single string `"Equity Scheme - Sectoral/ Thematic"` and never says which
sector, so the category guard cannot help there.

*Proof: 8 category-route tests, 10 sector-route tests, plus BENCH_FUNDS integrity —
`no cycles in the fallback chains`, `every routable benchmark key exists in
BENCH_FUNDS`, `every fallback key points at a real benchmark`.*

## 5. Import template — Code column kept

**Kept, and it now carries its weight.** The Code column is the only field that
identifies a scheme unambiguously, and the SEBI 2.0 renaming is exactly why: names
drift, codes don't. A sheet saying "ICICI Prudential Bluechip Fund" resolves
perfectly by code.

- Example row now carries a **real code** (`122639`) instead of a blank, which read
  as "leave this empty".
- A **"How to fill"** sheet documents every column and explains why Code matters.
  It's a separate sheet on purpose — notes in the data sheet get parsed back as
  rows and surface as import errors.
- Import now selects the **"Portfolio"** sheet by name, falling back to the first
  sheet, so the guidance sheet can't be read as data.

Code remains **optional**. Name-only import still works; anything ambiguous is
flagged for confirmation rather than guessed.

*Proof: `template keeps the Code column`, `template example row carries a real code
(not blank)`, `template ships a guidance sheet`, `import prefers the Portfolio sheet
by name`.*

## 6. Housekeeping

- Deleted `data/tri/NIFTY_SMALLCAP_250.json` — an orphan: not in `INDEX_MAP`, not in
  the manifest, stale, and duplicating `NIFTY_SMALLCAP250.json`.
- `data/tri/NIFTY_NEXT_50.json` was a **second orphan** — present but absent from
  `INDEX_MAP`, so it was never refreshed. Now a first-class entry, so the fetcher
  maintains it.
- `tri-fetch.yml` timeout **25 → 45 min**. The index set more than doubled; each
  index allows 4 attempts with backoff, and a timeout kills the job and commits
  nothing.

---

## Not done (deliberate)

- **Multi-asset and hybrid funds** remain rejected. Real multi-asset benchmarks are
  per-fund composite blends (ICICI Pru Multi-Asset: Nifty 200 TRI 65% + Nifty
  Composite Debt 25% + Gold 6% + Silver 1% + iCOMDEX 3%; DSP and Axis each use
  different weights) — not a single index that can be fetched. Supporting them means
  either blending series with monthly resets and a non-NSE source for gold/silver,
  or picking an NSE hybrid index as a labelled proxy. That's a project, not a config
  change. `Hybrid Scheme - *` categories are rejected with a clear message rather
  than silently mapped to an equity index.
- **Debt funds** — same reasoning, lower value.
- **Strategy-index routing** for Value / Focused / Dividend Yield. These stay on
  Nifty 500, which mirrors actual AMC practice: most such funds disclose a broad
  benchmark, not a strategy index.

## Verify after deploying

The 22 new indices use canonical names that must match niftyindices' internal
spelling exactly — a wrong name returns an **empty result**, not an error. The
fetcher reports these as `"EMPTY result -> likely wrong canonical name; skipping"`,
and `REQUIRED_KEYS` deliberately excludes them so a bad name can't block the commit.

**Check the first scheduled run's log** and confirm each new index fetched. Any that
report EMPTY need their `name` corrected in `INDEX_MAP` against
niftyindices.com — I could not verify all 22 against the live endpoint from here.
