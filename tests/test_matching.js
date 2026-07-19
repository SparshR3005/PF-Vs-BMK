#!/usr/bin/env node
/**
 * Regression tests for scheme matching, the SEBI category guard, and benchmark
 * mapping.
 *
 * Run:  node tests/test_matching.js
 *
 * Every category string asserted here was read from a LIVE MFAPI response, not
 * assumed. Where a test encodes a benchmark choice, the AMC source is cited in a
 * comment — the v4 lesson was that an unverified claim drifts from reality.
 *
 * These extract the real functions out of index.html by name (never a copy), so a
 * test can't keep passing after the shipped code changes underneath it.
 */
const fs = require("fs");
const path = require("path");

const HTML = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");

let pass = 0, fail = 0;
function ok(name, cond, msg) {
  if (cond) { console.log("  PASS  " + name); pass++; }
  else { console.log("  FAIL  " + name + (msg ? ": " + msg : "")); fail++; }
}

/** Pull a top-level `function NAME(...){...}` out of the HTML by brace matching. */
function extractFn(name) {
  const start = HTML.indexOf("function " + name + "(");
  if (start < 0) throw new Error("function not found in index.html: " + name);
  let i = HTML.indexOf("{", start), depth = 0, inS = null, prev = "";
  for (; i < HTML.length; i++) {
    const c = HTML[i];
    if (inS) { if (c === inS && prev !== "\\") inS = null; }
    else if (c === '"' || c === "'" || c === "`") inS = c;
    else if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return HTML.slice(start, i + 1); }
    prev = c;
  }
  throw new Error("unbalanced braces extracting " + name);
}
/** Pull a top-level `const NAME = {...};` or `[...];` block. */
function extractConst(name) {
  const m = HTML.match(new RegExp("const " + name + "\\s*=\\s*[\\[{][\\s\\S]*?\\n[\\]}];"));
  if (!m) throw new Error("const not found in index.html: " + name);
  return m[0];
}

// Against code that predates this work these constants don't exist. A thrown
// ReferenceError would abort the run and print a stack trace, which reads like a
// broken test rather than a caught regression — so report the absence as a plain
// failure and stop cleanly. (This is what makes the suite meaningful as a
// mutation check: run it against the old index.html and it goes red, not boom.)
let SCOPE;
try {
  SCOPE = [
  extractConst("CATEGORY_CANON"),
  extractConst("CATEGORY_NAME_TOKENS"),
  extractConst("CATEGORY_KEY_BENCH"),
  extractConst("CATEGORY_DEFAULTS"),
  extractConst("SECTOR_KEYWORDS"),
  extractConst("BENCH_FUNDS"),
  extractConst("FLEXI_OVERRIDES"),
  extractConst("HYBRID_CRISIL_OVERRIDES"),
  extractConst("RENAME_WORD_ALIASES"),
  'const FALLBACK_KEY = "NIFTY500";',
  'const CRISIL_NOTE = "NSE equivalent of the fund\\u2019s CRISIL benchmark";',
  'const HYBRID_KEYS = new Set(["AGGR_HYBRID","BAL_HYBRID","BAF_DAA","CONS_HYBRID","EQ_SAVINGS"]);',
  extractFn("normName"),
  extractFn("hasWord"),
  extractFn("stripRenameNote"),
  extractFn("applyRenameAliases"),
  extractFn("matchKey"),
  extractFn("bigrams"),
  extractFn("similarity"),
  extractFn("normCategory"),
  extractFn("hybridCategoryKey"),
  extractFn("categoryKey"),
  extractFn("isUnsupportedCategory"),
  extractFn("isMultiAssetCategory"),
  extractFn("hybridCrisilNote"),
  extractFn("claimedCategoryFromName"),
  extractFn("resolveBenchmarkKey"),
  ].join("\n");
} catch (e) {
  console.log("  FAIL  index.html is missing the matching/category machinery: " + e.message);
  console.log("\nFAILED (0 passed, 1 failed)");
  process.exit(1);
}

let A;
try {
  A = new Function(SCOPE + `; return {
    matchKey, similarity, stripRenameNote, applyRenameAliases, categoryKey,
    isUnsupportedCategory, isMultiAssetCategory, claimedCategoryFromName,
    resolveBenchmarkKey, BENCH_FUNDS
  };`)();
} catch (e) {
  console.log("  FAIL  could not evaluate index.html's matching machinery: " + e.message);
  console.log("\nFAILED (0 passed, 1 failed)");
  process.exit(1);
}

// Real MFAPI scheme names (verbatim from live responses).
const N_LARGECAP = "ICICI Prudential Large Cap Fund (erstwhile Bluechip Fund) - Direct Plan - Growth";
const N_LARGEMID = "ICICI Prudential Large & Mid Cap Fund - Direct Plan - Growth";
const C_LARGECAP = "Equity Scheme - Large Cap Fund";
const C_LARGEMID = "Equity Scheme - Large & Mid Cap Fund";

// ==================================================== the reported bug
(function testReportedBug() {
  const typed = "ICICI PRUDENTIAL LARGE CAP FUND - DIRECT PLAN";

  ok("erstwhile note is stripped from the key",
     !/erstwhile/.test(A.matchKey(N_LARGECAP)), A.matchKey(N_LARGECAP));

  ok("BUG: typed 'Large Cap Fund' now EXACT-matches the real Large Cap fund",
     A.matchKey(typed) === A.matchKey(N_LARGECAP),
     A.matchKey(typed) + " vs " + A.matchKey(N_LARGECAP));

  // Why it broke: the rename note dragged the CORRECT fund's score below the 0.78
  // auto-accept floor while the WRONG fund cleared it.
  const simWrong = A.similarity(A.matchKey(typed), A.matchKey(N_LARGEMID));
  ok("the wrong Large & Mid Cap fund still scores high (>0.78) — exactness is what saves us",
     simWrong >= 0.78, "sim=" + simWrong.toFixed(3));

  ok("typed name and the wrong fund do NOT key the same",
     A.matchKey(typed) !== A.matchKey(N_LARGEMID));
})();

// ==================================================== category taxonomy (live strings)
(function testCategoryTaxonomy() {
  const ACCEPT = {
    "Equity Scheme - Large Cap Fund": "LARGE_CAP",
    "Equity Scheme - Large & Mid Cap Fund": "LARGE_MID",
    "Equity Scheme - Mid Cap Fund": "MID_CAP",
    "Equity Scheme - Small Cap Fund": "SMALL_CAP",
    "Equity Scheme - Multi Cap Fund": "MULTI_CAP",
    "Equity Scheme - Flexi Cap Fund": "FLEXI_CAP",
    "Equity Scheme - Focused Fund": "FOCUSED",
    "Equity Scheme - Value Fund": "VALUE",
    "Equity Scheme - Dividend Yield Fund": "DIV_YIELD",
    "Equity Scheme - Sectoral/ Thematic": "SECTORAL",
    "ELSS": "ELSS",
  };
  Object.entries(ACCEPT).forEach(([cat, key]) => {
    ok("accepts " + JSON.stringify(cat) + " -> " + key, A.categoryKey(cat) === key, String(A.categoryKey(cat)));
  });

  // Junk values observed live on legacy/closed MFAPI records. Each of these used to
  // pass the gate and get benchmarked against Nifty 500.
  ["1", "1099 Days", "Growth", "Income", "IDF", "Payout",
   "Formerly Known as IIFL Mutual Fund", ""].forEach(junk => {
    ok("rejects junk category " + JSON.stringify(junk), A.isUnsupportedCategory(junk));
  });

  // Categories this tool still cannot benchmark (debt / index / FoF), plus the two
  // hybrid sub-categories deliberately excluded for now: arbitrage (return ≈ a
  // liquid fund, so an "alpha" is uninformative) and multi-asset (Phase B).
  ["Hybrid Scheme - Multi Asset Allocation", "Hybrid Scheme - Arbitrage Fund",
   "Debt Scheme - Medium Duration Fund",
   "Debt Scheme - Gilt Fund with 10 year constant duration",
   "Other Scheme - Index Funds", "Other Scheme - FoF Overseas"].forEach(cat => {
    ok("rejects non-equity " + JSON.stringify(cat), A.isUnsupportedCategory(cat));
  });

  // Phase A: mainstream hybrid sub-categories are now SUPPORTED and map to the
  // standard NSE composite for that category (all names verified vs NSE factsheets).
  const HYB = [
    ["Hybrid Scheme - Aggressive Hybrid Fund", "AGGR_HYBRID", "NIFTY_HYBRID_65_35"],
    ["Hybrid Scheme - Balanced Hybrid Fund", "BAL_HYBRID", "NIFTY_HYBRID_50_50"],
    ["Hybrid Scheme - Conservative Hybrid Fund", "CONS_HYBRID", "NIFTY_HYBRID_15_85"],
    ["Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage", "BAF_DAA", "NIFTY_HYBRID_50_50"],
    ["Hybrid Scheme - Equity Savings", "EQ_SAVINGS", "NIFTY_EQ_SAVINGS"],
  ];
  HYB.forEach(([cat, key, benchKey]) => {
    ok("supports hybrid " + JSON.stringify(cat), !A.isUnsupportedCategory(cat));
    ok("hybrid " + key + " routes to " + benchKey, A.categoryKey(cat) === key, A.categoryKey(cat));
    ok(benchKey + " is a real benchmark", !!A.BENCH_FUNDS[benchKey]);
  });
  // BAF must NOT be mis-read as a bare Balanced Hybrid (token order matters).
  ok("Balanced Advantage is BAF_DAA, not BAL_HYBRID",
     A.categoryKey("Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage") === "BAF_DAA");
  // MFAPI punctuation drift must not defeat routing (token match, not exact).
  ok("hybrid routes despite '-' spacing drift",
     A.categoryKey("Hybrid Scheme-Aggressive Hybrid Fund") === "AGGR_HYBRID");
  // Multi-asset is flagged for the "supported soon" message, not a hard rejection tone.
  ok("multi-asset flagged for friendly message",
     A.isMultiAssetCategory("Hybrid Scheme - Multi Asset Allocation"));
  ok("arbitrage is NOT treated as multi-asset",
     !A.isMultiAssetCategory("Hybrid Scheme - Arbitrage Fund"));

  // A hybrid fund officially on a CRISIL twin: same NSE composite, flagged approx + note.
  const sbi = A.resolveBenchmarkKey("SBI Balanced Advantage Fund - Direct Growth",
                                    "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage");
  ok("CRISIL fund maps to NSE composite but approx+note",
     sbi.key === "NIFTY_HYBRID_50_50" && sbi.approx === true && /CRISIL/.test(sbi.note || ""),
     JSON.stringify(sbi));
  // A non-CRISIL hybrid stays exact (no approx note).
  const hdfc = A.resolveBenchmarkKey("HDFC Balanced Advantage Fund - Direct Growth",
                                     "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage");
  ok("non-CRISIL hybrid is exact (no note)",
     hdfc.key === "NIFTY_HYBRID_50_50" && hdfc.approx === false && !hdfc.note, JSON.stringify(hdfc));

  ok("exact match, not substring: 'Large & Mid Cap' is not read as LARGE_CAP",
     A.categoryKey(C_LARGEMID) === "LARGE_MID");
  ok("exact match, not substring: 'Large & Mid Cap' is not read as MID_CAP",
     A.categoryKey(C_LARGEMID) !== "MID_CAP");
})();

// ==================================================== the wrong-fund guard
(function testWrongFundGuard() {
  const conflict = (name, cat) => {
    const claimed = A.claimedCategoryFromName(name), actual = A.categoryKey(cat);
    return !!(claimed && actual && claimed !== actual && actual !== "SECTORAL");
  };
  ok("GUARD: 'Large Cap' name vs Large & Mid Cap fund is BLOCKED",
     conflict("ICICI Prudential Large Cap Fund", C_LARGEMID));
  ok("GUARD: 'Large & Mid Cap' name vs Mid Cap fund is BLOCKED",
     conflict("ICICI Prudential Large & Mid Cap Fund", "Equity Scheme - Mid Cap Fund"));
  ok("GUARD: 'Large Cap' name vs Large Cap fund is allowed",
     !conflict("ICICI Prudential Large Cap Fund", C_LARGECAP));
  ok("GUARD: the real (erstwhile ...) name vs Large Cap fund is allowed",
     !conflict(N_LARGECAP, C_LARGECAP));
  ok("GUARD: sector funds are never blocked (category never names the sector)",
     !conflict("ICICI Prudential Technology Fund", "Equity Scheme - Sectoral/ Thematic"));
  ok("GUARD: a name claiming nothing checkable is allowed",
     !conflict("Parag Parikh Flexi Cap Fund", "Equity Scheme - Flexi Cap Fund"));

  ok("claim order: 'Large & Mid Cap' claims LARGE_MID, not LARGE_CAP",
     A.claimedCategoryFromName("SBI Large & Mid Cap Fund") === "LARGE_MID");
  ok("claim order: 'Large & Mid Cap' claims LARGE_MID, not MID_CAP",
     A.claimedCategoryFromName("SBI Large & Mid Cap Fund") !== "MID_CAP");
})();

// ==================================================== SEBI 2.0 renames
(function testRenameAliases() {
  const keysMatch = (oldName, newName) =>
    A.matchKey(A.applyRenameAliases(oldName)) === A.matchKey(newName);

  ok("rename: ICICI Bluechip (old sheet) resolves to Large Cap Fund",
     keysMatch("ICICI Prudential Bluechip Fund - Direct Plan", N_LARGECAP));
  ok("rename: HDFC Bluechip -> HDFC Large Cap",
     keysMatch("HDFC Bluechip Fund - Direct Plan - Growth",
               "HDFC Large Cap Fund - Growth Option - Direct Plan"));
  ok("rename: HDFC Mid Cap Opportunities -> HDFC Mid Cap",
     keysMatch("HDFC Mid Cap Opportunities Fund - Direct Plan",
               "HDFC Mid Cap Fund - Growth Option - Direct Plan"));
  ok("rename: DSP Equity Opportunities -> DSP Large & Mid Cap",
     keysMatch("DSP Equity Opportunities Fund - Direct - Growth",
               "DSP Large & Mid Cap Fund - Direct Plan - Growth"));
  ok("rename: ICICI Value Discovery -> ICICI Value Fund",
     keysMatch("ICICI Prudential Value Discovery Fund - Direct Plan",
               "ICICI Prudential Value Fund - Direct Plan - Growth"));

  ok("rename aliases don't corrupt an unrelated name",
     A.applyRenameAliases("Parag Parikh Flexi Cap Fund") === "parag parikh flexi cap fund");
})();

// ==================================================== benchmark mapping
(function testBenchmarkMapping() {
  const key = (name, cat) => A.resolveBenchmarkKey(name, cat).key;

  // Verified against AMC disclosure: ICICI Pru Large Cap Fund -> Nifty 100 TRI
  // (icicipruamc.com scheme page, read Jul 2026).
  ok("Large Cap -> Nifty 100 TRI", key(N_LARGECAP, C_LARGECAP) === "NIFTY100");
  // Verified against AMC factsheet: "Scheme benchmark is Nifty LargeMidcap 250 TRI".
  ok("Large & Mid Cap -> Nifty LargeMidcap 250 TRI",
     key(N_LARGEMID, C_LARGEMID) === "NIFTY_LARGEMIDCAP250");
  ok("Mid Cap -> Nifty Midcap 150 TRI",
     key("HDFC Mid Cap Fund", "Equity Scheme - Mid Cap Fund") === "NIFTY_MIDCAP150");
  ok("Small Cap -> Nifty Smallcap 250 TRI",
     key("SBI Small Cap Fund", "Equity Scheme - Small Cap Fund") === "NIFTY_SMALLCAP250");
  ok("Multi Cap -> Nifty500 Multicap 50:25:25 TRI",
     key("Kotak Multicap Fund", "Equity Scheme - Multi Cap Fund") === "NIFTY_MULTICAP");
  ok("Flexi Cap -> Nifty 500 TRI",
     key("Parag Parikh Flexi Cap Fund", "Equity Scheme - Flexi Cap Fund") === "NIFTY500");
  ok("ELSS -> Nifty 500 TRI", key("Mirae Asset ELSS Tax Saver Fund", "ELSS") === "NIFTY500");
  ok("Value -> Nifty 500 TRI (broad index, matching AMC practice)",
     key("ICICI Prudential Value Fund", "Equity Scheme - Value Fund") === "NIFTY500");

  // The bug this ordering prevents: a Large & Mid Cap fund must NOT land on the
  // Mid Cap benchmark.
  ok("Large & Mid Cap does NOT resolve to the Mid Cap benchmark",
     key(N_LARGEMID, C_LARGEMID) !== "NIFTY_MIDCAP150");

  // Sector routing stays name-based: the category never says which sector.
  const S = "Equity Scheme - Sectoral/ Thematic";
  ok("sector: Technology -> Nifty IT", key("ICICI Prudential Technology Fund", S) === "NIFTY_IT");
  ok("sector: Pharma -> Nifty Pharma", key("Nippon India Pharma Fund", S) === "NIFTY_PHARMA");
  ok("sector: Healthcare -> Nifty Healthcare (not Pharma)",
     key("Mirae Asset Healthcare Fund", S) === "NIFTY_HEALTHCARE");
  ok("sector: PSU Bank -> Nifty PSU Bank (specific beats generic 'bank')",
     key("Kotak Nifty PSU Bank Fund", S) === "NIFTY_PSU_BANK");
  ok("sector: Private Bank -> Nifty Private Bank (not generic Bank)",
     key("ICICI Prudential Private Bank Fund", S) === "NIFTY_PRIVATE_BANK");
  ok("sector: Banking & Financial Services -> Nifty Financial Services",
     key("SBI Banking & Financial Services Fund", S) === "NIFTY_FINSERV_OR_BANK");
  ok("sector: Consumer Durables -> Nifty Consumer Durables (not Consumption)",
     key("ICICI Prudential Consumer Durables Fund", S) === "NIFTY_CONSUMER_DUR");
  ok("sector: Infrastructure -> Nifty Infrastructure",
     key("ICICI Prudential Infrastructure Fund", S) === "NIFTY_INFRA");
  ok("sector: Defence -> Nifty India Defence",
     key("Motilal Oswal Nifty India Defence Fund", S) === "NIFTY_INDIA_DEFENCE");
  ok("sector: unknown sector -> broad-market proxy, flagged approx",
     A.resolveBenchmarkKey("Some Unheard-Of Thematic Fund", S).approx === true);
})();

// ==================================================== BENCH_FUNDS integrity
(function testBenchIntegrity() {
  const keys = Object.keys(A.BENCH_FUNDS);
  const bad = keys.filter(k => {
    const fb = A.BENCH_FUNDS[k].fb;
    return fb !== null && !A.BENCH_FUNDS[fb];
  });
  ok("every fallback key points at a real benchmark", bad.length === 0, bad.join(","));

  const noLabel = keys.filter(k => !A.BENCH_FUNDS[k].label);
  ok("every benchmark has a display label", noLabel.length === 0, noLabel.join(","));

  // Follow every fb chain to its end: a cycle would hang resolveBenchmarkTri()'s
  // guard loop and silently degrade to "no benchmark".
  const cyclic = keys.filter(k => {
    const seen = new Set(); let cur = k;
    while (cur) { if (seen.has(cur)) return true; seen.add(cur); cur = A.BENCH_FUNDS[cur].fb; }
    return false;
  });
  ok("no cycles in the fallback chains", cyclic.length === 0, cyclic.join(","));

  ok("NIFTY500 is the terminal fallback", A.BENCH_FUNDS.NIFTY500.fb === null);

  // Every key the resolver can return must exist in BENCH_FUNDS, or the holding
  // silently loses its benchmark.
  const routed = new Set();
  const CKB = HTML.match(/const CATEGORY_KEY_BENCH = \{[\s\S]*?\n\};/)[0];
  (CKB.match(/"([A-Z0-9_]+)"/g) || []).forEach(s => routed.add(s.replace(/"/g, "")));
  const SK = HTML.match(/const SECTOR_KEYWORDS = \[[\s\S]*?\n\];/)[0];
  (SK.match(/key:\s*"([A-Z0-9_]+)"/g) || []).forEach(s => routed.add(s.split('"')[1]));
  const missing = [...routed].filter(k => /^NIFTY/.test(k) && !A.BENCH_FUNDS[k]);
  ok("every routable benchmark key exists in BENCH_FUNDS", missing.length === 0, missing.join(","));

  // The indices added in v6 have no committed TRI file until the fetcher's first
  // run. resolveBenchmarkTri() walks .fb when a file is missing, so every benchmark
  // must reach a key whose file already exists — otherwise those holdings lose
  // their benchmark between deploy and first fetch.
  // Read the committed files from disk rather than hand-maintaining a list: a
  // hardcoded set goes stale the moment a .json is added or deleted, and would then
  // assert something untrue about the repo.
  const triDir = path.join(__dirname, "..", "data", "tri");
  const SHIPPED = new Set(
    fs.existsSync(triDir)
      ? fs.readdirSync(triDir).filter(f => f.endsWith(".json") && f !== "index.json")
           .map(f => f.replace(/\.json$/, ""))
      : []);
  ok("data/tri contains committed TRI files to fall back on", SHIPPED.size > 0);
  // Hybrid composites (Phase A) deliberately have fb:null and NO committed file
  // until the fetcher's first successful run. A missing hybrid benchmark must fail
  // to NO comparison (honest) rather than degrade to a pure-equity index of a
  // different risk profile — so, unlike equity/sector keys, they are ALLOWED to be
  // unbacked pre-first-fetch. Verify that intent so this exemption can't silently
  // mask a genuinely stranded equity benchmark.
  const INTENTIONALLY_UNBACKED = new Set(
    ["NIFTY_HYBRID_65_35", "NIFTY_HYBRID_50_50", "NIFTY_HYBRID_15_85", "NIFTY_EQ_SAVINGS"]);
  ok("hybrid composites are intentionally fb:null (fail to no-comparison)",
     [...INTENTIONALLY_UNBACKED].every(k => A.BENCH_FUNDS[k] && A.BENCH_FUNDS[k].fb === null));
  const stranded = keys.filter(k => {
    if (INTENTIONALLY_UNBACKED.has(k)) return false;   // by-design, asserted above
    let cur = k, guard = 0;
    while (cur && guard++ < 6) { if (SHIPPED.has(cur)) return false; cur = A.BENCH_FUNDS[cur].fb; }
    return true;
  });
  ok("every equity/sector benchmark degrades to an existing TRI before the first fetch",
     stranded.length === 0, stranded.join(","));
})();

// ==================================================== import template
(function testTemplate() {
  ok("template keeps the Code column",
     /\["Scheme","Plan","Start","End","Monthly","Code"\]/.test(HTML));
  ok("template example row carries a real code (not blank)",
     /"Parag Parikh Flexi Cap Fund - Direct Plan - Growth","Direct","2022-01-01","",5000,"122639"/.test(HTML));
  ok("template ships a guidance sheet", /"How to fill"/.test(HTML));
  ok("import prefers the Portfolio sheet by name",
     /toLowerCase\(\)==="portfolio"/.test(HTML));
  ok("import offers candidates when it can't resolve", /needsPick/.test(HTML));
})();

console.log(`\n${fail ? "FAILED" : "ALL PASSED"} (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);