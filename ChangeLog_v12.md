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
