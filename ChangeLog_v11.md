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
