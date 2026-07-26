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