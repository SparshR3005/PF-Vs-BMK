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