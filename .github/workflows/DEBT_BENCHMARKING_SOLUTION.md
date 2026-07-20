# Debt-Fund Benchmarking Solution

**Prepared:** 20 July 2026  
**Scope:** Add actively managed Indian debt mutual funds to PF-Vs-BMK without using an equity proxy or fabricating debt returns.

## Decision

Use the **NSE Indices Historical Index Data** report for fixed-income benchmarks. NSE states on that report that all fixed-income indices, except the clean-price version of the Nifty 10-year Benchmark G-Sec, are total-return indices. Therefore, the report's `CLOSE` field can be stored as the benchmark TRI level.

This is a different data path from the Advanced Charting market feed that was previously tested for hybrid composites. The market feed contains traded-market fields such as volume and turnover; it is not the correct basis for concluding that fixed-income index history is unavailable.

## Category mapping

The utility maps the MFAPI/SEBI debt category to the closest broad Nifty fixed-income index. Because MFAPI does not provide the scheme's disclosed Tier-1 benchmark or Potential Risk Class variant, every debt comparison is displayed as an **approximate category proxy**.

| Debt category | Benchmark used |
|---|---|
| Overnight Fund | Nifty 1D rate index |
| Liquid Fund | Nifty Liquid Index |
| Ultra Short Term / Ultra Short Duration Fund | Nifty Ultra Short Duration Debt Index |
| Ultra Short to Short Term / Low Duration Fund | Nifty Low Duration Debt Index |
| Money Market Fund | Nifty Money Market Index |
| Short Term / Short Duration Fund | Nifty Short Duration Debt Index |
| Medium Term / Medium Duration Fund | Nifty Medium Duration Debt Index |
| Medium to Long Term / Medium to Long Duration Fund | Nifty Medium to Long Duration Debt Index |
| Long Term / Long Duration Fund | Nifty Long Duration Debt Index |
| Dynamic Term / Dynamic Bond Fund | Nifty Composite Debt Index |
| Corporate Bond Fund | Nifty Corporate Bond Index |
| Credit Risk Fund | Nifty Credit Risk Bond Index |
| Banking and PSU Debt Fund | Nifty Banking & PSU Debt Index |
| Gilt Fund | Nifty All Duration G-Sec Index |
| 10-year Constant Maturity Gilt Fund | Nifty 10 yr Benchmark G-Sec |

### Excluded in this release

- **Floating Interest Rates / Floater funds:** no clean broad category-level floating-rate Nifty TRI was identified. A duration or overnight proxy would be misleading.
- **Sectoral debt funds:** require a sector-specific debt benchmark; a single generic debt index is not defensible.
- Passive debt index funds, ETFs and funds of funds remain outside the utility's actively managed fund comparison scope.

## What changed

### `index.html`

- Added routing for 15 debt categories, accepting both legacy MFAPI labels and the SEBI terminology introduced in February 2026.
- Added the 15 fixed-income benchmark definitions.
- Removed supported debt-fund words from the search-list exclusion screen so debt funds can be selected.
- Debt comparisons are labelled with this note:

  > Nifty category proxy; the scheme's disclosed Tier-1 benchmark may be a CRISIL or Nifty PRC variant

- Missing debt data fails to **no benchmark comparison**. There is no fallback to equity or to another duration/credit bucket.
- Added specific rejection messages for floating-rate and sectoral debt funds.

### `fetch_tri.py`

- Added the NSE Historical Index Data endpoint for fixed-income and hybrid report-series candidates.
- Sends both `name` and `indexName` in the wrapped `cinfo` payload.
- Parses `HistoricalDate` plus `CLOSE`, including comma-formatted values and the endpoint's possible response envelopes.
- Uses adaptive date-range splitting if a large request is truncated.
- Keeps every debt/hybrid index optional, so a debt failure cannot abort or corrupt the mature equity update.
- After the first full backfill, fetches only a 45-day overlap plus new dates. The overlap must match committed values before the new slice is merged.
- Increased the scheduled workflow timeout to 90 minutes to accommodate the first historical backfill and Akamai retries.

## Deployment sequence

1. Replace the repository files with this delivery and push.
2. Confirm the normal test workflow is green.
3. Manually run **Fetch TRI data** once from GitHub Actions. The first run performs the historical backfill and may be materially heavier than later runs.
4. Inspect the Actions log for each fixed-income index's selected canonical name, row count, start date and end date.
5. Confirm that files such as `data/tri/NIFTY_LIQUID.json` and `data/tri/NIFTY_COMPOSITE_DEBT.json` were committed and that `data/tri/index.json` marks them `fresh: true`.
6. Test representative debt schemes in the browser. The fund XIRR should appear immediately; the benchmark spread appears once the corresponding TRI file exists and overlaps the SIP period.

## Validation status

The implementation passes the shipped JavaScript application tests, the expanded category/benchmark suite, Python fetcher regression tests, Python compilation and full inline-JavaScript syntax checking.

A live NSE fetch was not executed from the delivery environment because outbound DNS/browser access to `niftyindices.com` is blocked there. The first manual GitHub Actions run is therefore the required integration test for endpoint accessibility and the exact canonical spelling accepted by NSE. Candidate spellings and graceful last-good behaviour are included specifically to make that run safe and diagnosable.