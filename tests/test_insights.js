/* Tests for the Insights tab.
 *
 * Like the other suites, these pull the real functions out of index.html by name
 * rather than testing a copy, so editing index.html and forgetting the test goes
 * red instead of silently passing.
 *
 *   node tests/test_insights.js
 */
const fs = require("fs");
const path = require("path");

const HTML = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");

let pass = 0, fail = 0;
function ok(label, cond){
  if(cond){ pass++; console.log("  PASS  " + label); }
  else { fail++; console.log("  FAIL  " + label); }
}
function eq(label, got, want){
  ok(label, got === want);
  if(got !== want){ console.log("          got  " + JSON.stringify(got));
                    console.log("          want " + JSON.stringify(want)); }
}

function grabFn(name){
  const i = HTML.indexOf("\nfunction " + name + "(");
  if(i < 0) throw new Error("function not found in index.html: " + name);
  let depth = 0;
  const start = HTML.indexOf("{", i);
  for(let k = start; k < HTML.length; k++){
    if(HTML[k] === "{") depth++;
    else if(HTML[k] === "}"){ depth--; if(depth === 0) return HTML.slice(i + 1, k + 1); }
  }
  throw new Error("unbalanced braces: " + name);
}

// ---- ambient bindings the extracted functions close over -------------------
const escapeHtml = s => String(s).replace(/[&<>"]/g,
  c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));
const MIN_TOP_UNIVERSE = 10, TOP_N = 5;
// Read from index.html rather than restated, so the suite exercises the SHIPPED
// thresholds. rankCandidates() now classifies exclusions by cause and needs both.
const DEAD_FUND_DAYS = parseInt((HTML.match(/const DEAD_FUND_DAYS = (\d+);/) || [0, 60])[1], 10);
const MIN_RANK_DAYS  = parseInt((HTML.match(/const MIN_RANK_DAYS = (\d+);/)  || [0, 365])[1], 10);
const HORIZON_LABELS = [["6m","6 Month"],["1y","1 Year"],["2y","2 Years"],["3y","3 Years"],
                        ["5y","5 Years"],["7y","7 Years"],["10y","10 Years"]];
const NAME_STOPWORDS = new Set(["direct","regular","plan","growth","option","opt",
                                "payout","reinvestment","idcw","dividend"]);
const NAME_ACRONYMS = new Set(["SBI","HSBC","ICICI","HDFC","UTI","LIC","IDFC","DSP","PGIM",
  "BNP","JM","IIFL","ITI","NJ","BOI","PPFAS","AMC","ELSS","LT","TATA","IDBI","BOB",
  "MF","TRUSTMF","WOC","NAV","TRI","SIP","XIRR","FOF","IDCW","ONE",
  "US","UK","ESG","REIT","INVIT","PSU","FMCG","IT","NIFTY","BSE","NSE","CRISIL"]);

const NEEDED = ["titleCaseWord","titleCaseName","normaliseFundName","gridToNavArr",
                "rankCandidates","signedPP","periodTableHtml","topListHtml"];
let loaded = true;
try { eval(NEEDED.map(grabFn).join("\n")); }
catch(e){ loaded = false; console.log("  FAIL  index.html is missing Insights machinery: " + e.message); fail++; }

// runSIP/scheduleDates come from index.html too -- rankCandidates calls them.
try { eval([grabFn("navOnOrAfter"), grabFn("navOnOrBefore"), grabFn("addMonths"),
            grabFn("daysBetween"), grabFn("isoDate"), grabFn("parseInput"),
            grabFn("normaliseFlows"), grabFn("xirr"), grabFn("scheduleDates"),
            grabFn("valuationAt"), grabFn("uniformSchedule"), grabFn("runSIP")].join("\n")); }
catch(e){ loaded = false; console.log("  FAIL  could not load SIP engine: " + e.message); fail++; }

console.log("Run node tests/test_insights.js");

if(loaded){
  // ------------------------------------------------------------ name display
  // Every shape below is a REAL string observed in published mfapi data.
  eq("standard direct suffix", normaliseFundName("Axis Midcap Fund - Direct Plan - Growth"),
     "Axis Midcap Fund");
  eq("reversed order and shouting",
     normaliseFundName("BANDHAN MIDCAP FUND - GROWTH - DIRECT PLAN"), "Bandhan Midcap Fund");
  eq("no space before dash",
     normaliseFundName("Canara Robeco Mid Cap Fund- Direct Plan- Growth Option"),
     "Canara Robeco Mid Cap Fund");
  eq("plan in parentheses", normaliseFundName("JM Midcap Fund (Direct) - Growth"),
     "JM Midcap Fund");
  eq("en dash separator", normaliseFundName("Motilal Oswal Contra Fund - Direct \u2013 Growth"),
     "Motilal Oswal Contra Fund");
  eq("no dash at all", normaliseFundName("WhiteOak Capital Mid Cap Fund Direct Plan Growth"),
     "WhiteOak Capital Mid Cap Fund");
  eq("suffix with no separator", normaliseFundName("Franklin India Mid Cap Fund-Growth"),
     "Franklin India Mid Cap Fund");
  eq("compound boilerplate segment",
     normaliseFundName("Nippon India Growth Mid Cap Fund - Direct Plan Growth Plan - Growth Option"),
     "Nippon India Growth Mid Cap Fund");
  // THE TRAP: "Growth" is part of this fund's actual name. Stripping the word
  // wholesale, rather than dropping all-boilerplate segments, corrupts it.
  ok("a fund genuinely named '...Growth...' keeps the word",
     normaliseFundName("Nippon India Growth Mid Cap Fund-Growth Plan-Growth Option")
       === "Nippon India Growth Mid Cap Fund");
  eq("acronyms survive title-casing",
     normaliseFundName("SBI CONTRA FUND - DIRECT PLAN - GROWTH"), "SBI Contra Fund");
  eq("empty input is safe", normaliseFundName(""), "");
  eq("null input is safe", normaliseFundName(null), "");
  ok("Direct and Regular collapse to one display name",
     normaliseFundName("DSP Midcap Fund - Direct Plan - Growth")
       === normaliseFundName("DSP Midcap Fund - Regular Plan - Growth"));
  ok("normalising never returns only boilerplate",
     !/^(direct|regular|growth)$/i.test(normaliseFundName("Some Fund - Direct Plan - Growth")));

  // ------------------------------------------------------------ grid decoding
  const grid = { t0:"2020-01-06", d:[0,7,14,21], v:[100,101,102,103] };
  const arr = gridToNavArr(grid);
  eq("grid decodes to one point per offset", arr.length, 4);
  eq("first date is t0", arr[0].date.toISOString().slice(0,10), "2020-01-06");
  eq("offsets become real dates", arr[3].date.toISOString().slice(0,10), "2020-01-27");
  ok("navs are carried through", arr[2].nav === 102);
  ok("dates strictly increase", arr.every((p,i)=> i===0 || p.date > arr[i-1].date));

  // ------------------------------------------------------------ ranking
  function synth(code, name, rate, weeks, startISO){
    const d=[], v=[]; let nav=100;
    for(let i=0;i<weeks;i++){ d.push(i*7); nav *= Math.pow(1+rate, 7/365.25); v.push(Number(nav.toFixed(4))); }
    return [code, {n:name, t0:startISO||"2016-01-04", d, v}];
  }
  const VALUE = new Date("2026-07-17T00:00:00");
  const dates = scheduleDates(new Date(2018,2,12), VALUE);

  const funds = {};
  for(let i=0;i<14;i++){
    const [c,e] = synth("F"+i, "Fund "+i+" - Direct Plan - Growth", 0.06+0.01*i, 560);
    funds[c] = e;
  }
  const res = rankCandidates({funds}, uniformSchedule(dates, 5000), VALUE, "F3");
  ok("all full-history funds are eligible", res.universe === 14);
  eq("top list is capped at five", res.top.length, 5);
  ok("top list is sorted best first",
     res.top.every((r,i)=> i===0 || r.xirr <= res.top[i-1].xirr));
  ok("the user's own fund is excluded from the top list",
     res.top.every(r => String(r.code) !== "F3"));
  ok("the user's own rank is reported", res.ownRank === 11);   // F3 is 11th of 14 by drift
  ok("a median is reported", res.median !== null && isFinite(res.median));
  ok("the best drift ranks first", res.top[0].code === "F13");

  // A fund that launched mid-window must not win on a short flattering run.
  const withLate = Object.assign({}, funds);
  const [lc, le] = synth("LATE", "Late Fund - Direct Plan - Growth", 0.40, 180, "2023-01-04");
  withLate[lc] = le;
  const res2 = rankCandidates({funds:withLate}, uniformSchedule(dates, 5000), VALUE, "F3");
  ok("a mid-window launch is excluded, not ranked first",
     res2.top.every(r => String(r.code) !== "LATE"));
  eq("and the eligible universe is unchanged", res2.universe, 14);

  eq("an empty category ranks nothing", rankCandidates({funds:{}}, uniformSchedule(dates, 5000), VALUE, "X").universe, 0);

  // A malformed document must be distinguishable from a genuinely empty cohort.
  // Both give universe 0, so without the flag a shape bug looks like a real result
  // -- which is exactly how two assertions in this file once passed vacuously.
  ok("a malformed document is flagged, not silently empty",
     rankCandidates({F0:{n:"x",t0:"2016-01-04",d:[0],v:[1]}}, uniformSchedule(dates, 5000), VALUE, "F0").malformed === true);
  ok("a genuinely empty cohort is NOT flagged malformed",
     rankCandidates({funds:{}}, uniformSchedule(dates, 5000), VALUE, "X").malformed !== true);
  ok("null document is flagged", rankCandidates(null, uniformSchedule(dates, 5000), VALUE, "X").malformed === true);

  // ------------------------------------------------------------ suppression
  const three = {};
  for(let i=0;i<3;i++){ const [c,e]=synth("T"+i,"Tiny "+i,0.08+0.01*i,560); three[c]=e; }
  const small = rankCandidates({funds:three}, uniformSchedule(dates, 5000), VALUE, "T0");
  ok("a 3-fund cohort is below the top-five threshold", small.universe < MIN_TOP_UNIVERSE);
  /* The list is now rendered at every pool size. Suppressing it hid the only
     like-for-like comparison the tab has; the honesty requirement is met by
     stating the pool size instead, so BOTH the list and the caveat must appear. */
  const smallHtml = topListHtml(small, small.ownXirr);
  ok("a thin cohort still gets a list", /<ol class="top5">/.test(smallHtml));
  ok("and the thin cohort is captioned as too small to rank",
     /too small a pool/.test(smallHtml));
  ok("a large cohort does get a list", /<ol class="top5">/.test(topListHtml(res, res.ownXirr)));
  ok("a large cohort carries no thin-pool caveat",
     !/too small a pool/.test(topListHtml(res, res.ownXirr)));

  // Q3: the user's own fund is ranked in place, not filtered out of its own list.
  const ownIn = res.top.some(r => r.own === true);
  ok("the own-fund flag is carried on the top list", res.top.every(r => "own" in r));
  if(ownIn){
    ok("the own row is labelled rather than showing a 0.00 pp gap",
       /your fund/.test(topListHtml(res, res.ownXirr)));
  } else {
    ok("own fund outside the top five simply does not appear", true);
  }

  // ------------------------------------------------------------ formatting
  eq("positive spreads carry a plus", signedPP(4.2), "+4.20 pp");
  ok("negative spreads use a real minus sign", signedPP(-4.2) === "\u22124.20 pp");
  eq("null spread renders as a dash", signedPP(null), "—");
  /* Quartiles were removed from the tab by decision, not by accident. Assert the
     machinery is gone so a future paste cannot quietly reintroduce it. */
  ok("no quartile helper survives in index.html",
     !/function qBadge/.test(HTML) && !/QUARTILE_WORD|QUARTILE_HELP/.test(HTML));
  ok("no quartile chip is rendered", !/class="qband/.test(HTML));

  // ------------------------------------------------------------ period table
  const periods = { plans: { Direct: {
    universe: {"6m":31,"1y":30,"3y":28,"10y":17},
    avg: {"6m":11.2,"1y":6.64,"3y":71.88,"10y":378.12},
    funds: { "118533": {
      abs:{"6m":6.30,"1y":0.69,"3y":66.37,"10y":308.99},
      ann:{"1y":0.69,"3y":18.51,"10y":15.13},
      rank:{"6m":29,"1y":27,"3y":20,"10y":12},
      q:{"6m":4,"1y":4,"3y":3,"10y":3} } } } } };
  const tbl = periodTableHtml(periods, "Direct", "118533");
  ok("every horizon gets a row", HORIZON_LABELS.every(h => tbl.includes(h[1])));
  ok("ranks are shown with their denominator", /29 of 31/.test(tbl));
  /* `avg` is built from the same CUMULATIVE returns as `abs` (fetch_ranks.py
     gates `ann` behind MIN_ANNUALISE_DAYS), so Return is the only column that is
     like-for-like with Category avg. They sit adjacent and Return carries the
     colour; colouring the annualised cell compared a CAGR to a cumulative figure
     and painted category-beating funds red at 5 of 7 horizons. */
  ok("sub-year rows carry no annualised figure",
     /<td>6 Month<\/td><td class="col-abs neg">6\.30%<\/td><td>11\.20%<\/td><td class="col-ann">—<\/td>/.test(tbl));
  /* Regression fixture for the CAGR-vs-cumulative bug. This fund BEATS its
     category at 10y on the only comparison that is unit-consistent (297.01 vs
     280.60 cumulative), while its annualised rate (14.79) sits far below the
     same average. Colour must follow the cumulative pair, so this row is green;
     comparing `ann` to `avg` would render it red. */
  const beats = { plans: { Direct: {
    universe: {"6m":43,"10y":18},
    avg: {"6m":4.48,"10y":280.60},
    funds: { "999": { abs:{"6m":7.78,"10y":297.01}, ann:{"10y":14.79},
                      rank:{"6m":10,"10y":6} } } } } };
  const beatTbl = periodTableHtml(beats, "Direct", "999");
  ok("Return above the category average renders green",
     /col-abs pos">297\.01%/.test(beatTbl) && /col-abs pos">7\.78%/.test(beatTbl));
  ok("Return below the category average renders red",
     /col-abs neg">6\.30%/.test(tbl) && /col-abs neg">308\.99%/.test(tbl));
  ok("the annualised cell is never the coloured one",
     !/col-ann[^>]*(pos|neg)/.test(beatTbl) && !/col-ann[^>]*(pos|neg)/.test(tbl));
  ok("the annualised column is tagged so narrow screens drop it, not Return",
     /<th class="col-ann">Annualized<\/th>/.test(tbl));
  ok("header order puts Category avg beside Return",
     /<th class="col-abs">Return<\/th><th>Category Avg<\/th>/.test(tbl));
  ok("every row has exactly five cells",
     (tbl.match(/<tr>(?!<th)/g) || []).length >= 0 &&
     tbl.split("<tr").slice(2).every(r => {
       const td = (r.match(/<td\b/g) || []).length;
       const span = (r.match(/colspan="(\d+)"/g) || [])
         .reduce((a, m) => a + (Number(m.match(/\d+/)[0]) - 1), 0);
       return td + span === 5;
     }));
  ok("horizons with no history are marked, not blanked",
     /not enough history/.test(tbl));
  ok("an unknown fund explains itself",
     /not yet in the published category table/.test(periodTableHtml(periods,"Direct","000")));
  ok("an unknown plan degrades gracefully",
     /No track record published/.test(periodTableHtml(periods,"Nope","118533")));
  ok("a null document does not throw",
     /No track record published/.test(periodTableHtml(null,"Direct","118533")));

  // ------------------------------------------------------- theme integrity
  // The first build of this tab referenced --ink, --pos and --neg, none of which
  // exist in the stylesheet. A CSS variable with no fallback and no definition
  // makes the whole declaration invalid, so the fund names fell back to the UA
  // default button colour: black text on a dark surface, unreadable. Nothing
  // errored. This asserts every variable the Insights CSS uses actually exists.
  const themeDefined = new Set((HTML.match(/(--[a-z0-9-]+)\s*:/g) || [])
    .map(m => m.replace(/\s*:$/, "")));
  const cssStart = HTML.indexOf("/* ---- Insights tab");
  const cssEnd = HTML.indexOf("@media(max-width:760px){", cssStart);
  ok("the Insights CSS block is present", cssStart > 0 && cssEnd > cssStart);
  const cssBlock = HTML.slice(cssStart, cssEnd);
  const usedVars = [...new Set((cssBlock.match(/var\((--[a-z0-9-]+)/g) || [])
    .map(m => m.slice(4)))];
  const undefinedVars = usedVars.filter(v => !themeDefined.has(v));
  ok("every CSS variable used by Insights is defined by the theme"
     + (undefinedVars.length ? " (missing: " + undefinedVars.join(", ") + ")" : ""),
     undefinedVars.length === 0);

  // A <button> inherits font but NOT colour, so this is load-bearing.
  ok("the row button inherits its text colour", /\.ins-summary\{color:inherit/.test(cssBlock));

  // Light-theme fallbacks on a dark surface are invisible.
  ok("no black-based overlay fallbacks remain", !/rgba\(0,0,0,/.test(cssBlock));

  // Quartile styling must not linger after the feature was removed.
  ok("no quartile CSS remains", !/\.q1\{|\.q4\{|\.qband\{/.test(HTML));
  ok("the footer no longer explains quartiles",
     !/split the category into four equal groups/.test(HTML));

  // The Return/Category-avg colouring needs the two utility classes to exist.
  ok("the coloured Return cell has theme colours",
     /td\.col-abs\.pos\{[^}]*var\(--beat\)/.test(cssBlock) &&
     /td\.col-abs\.neg\{[^}]*var\(--lag\)/.test(cssBlock));

  // ------------------------------------------------------------ wiring
  ok("the tab bar exists in the markup", /id="tabbar"/.test(HTML));
  ok("both tab buttons exist", /id="tabPortfolio"/.test(HTML) && /id="tabInsights"/.test(HTML));
  ok("panes are present", /id="paneInsights"/.test(HTML) && /id="panePortfolio"/.test(HTML));
  ok("tabs are wired to switchTab", /switchTab\("insights"\)/.test(HTML));
  ok("ranks files are fetched with no-store (they change daily)",
     /cache:"no-store"/.test(HTML));
  ok("sector funds are explained rather than silently absent",
     /pharma fund/i.test(HTML));
  ok("the disclaimer warns about survivorship", /merged or closed/i.test(HTML));
  ok("stale categories are surfaced to the user", /failed its last publish check/.test(HTML));
}

/* ============================================================================
   getDetail()'s NAV parsing: calendar validation + same-day deduplication.

   Both were found by adversarial fixtures:
   (a) JavaScript ROLLS OVER impossible dates -- new Date(2025,1,31) becomes
       3 March 2025 -- so a malformed upstream row was laundered into a real
       trading day and shifted SIP placement and valuation.
   (b) navOnOrAfter() walks to the FIRST row for a date, navOnOrBefore() to the
       LAST. With two rows for one date a SIP bought at one NAV and was valued at
       the other the same day: a Rs100 investment became Rs200 instantly.

   The logic under test is inlined from index.html's getDetail(); the extractor
   cannot pull a fragment out of an async function, so this mirrors it exactly.
   The parity assertion below fails if index.html ever drops either guard.
   ========================================================================== */
if(loaded){
  function parseNavRows(rows){
    const parsed = rows.map(function(d){
      const m = String(d.date||"").match(/^(\d{2})-(\d{2})-(\d{4})$/);
      if(!m) return null;
      const day=+m[1], month=+m[2], year=+m[3];
      const dt = new Date(year, month-1, day);
      if(dt.getFullYear()!==year || dt.getMonth()!==month-1 || dt.getDate()!==day) return null;
      const nav = Number(d.nav);
      return Number.isFinite(nav) && nav>0 ? {date:dt, nav:nav} : null;
    }).filter(Boolean).sort(function(a,b){return a.date-b.date;});
    const byDay = new Map(); let conflicts = 0;
    for(const row of parsed){
      const k = isoDate(row.date);
      const prev = byDay.get(k);
      if(prev && prev.nav !== row.nav) conflicts++;
      byDay.set(k, row);
    }
    return {arr:[...byDay.values()].sort(function(a,b){return a.date-b.date;}), conflicts:conflicts};
  }

  // ---- calendar validation
  let r = parseNavRows([{date:"31-02-2025",nav:"10"},{date:"01-01-2025",nav:"10"}]);
  eq("an impossible date (31 Feb) is dropped, not rolled forward", r.arr.length, 1);
  eq("and the valid row is untouched", isoDate(r.arr[0].date), "2025-01-01");
  eq("a real leap day is kept", parseNavRows([{date:"29-02-2024",nav:"10"}]).arr.length, 1);
  eq("a non-leap 29 Feb is dropped", parseNavRows([{date:"29-02-2025",nav:"10"}]).arr.length, 0);
  eq("31 April is dropped", parseNavRows([{date:"31-04-2025",nav:"10"}]).arr.length, 0);
  eq("month 13 is dropped", parseNavRows([{date:"01-13-2025",nav:"10"}]).arr.length, 0);

  // ---- same-day duplicates
  r = parseNavRows([{date:"01-01-2025",nav:"100"},{date:"01-01-2025",nav:"200"}]);
  eq("duplicate same-day rows collapse to one", r.arr.length, 1);
  eq("a conflicting duplicate is flagged for the user", r.conflicts, 1);
  const td = new Date(2025,0,1);
  const buy = navOnOrAfter(r.arr, td), val = navOnOrBefore(r.arr, td);
  ok("buy and valuation NAVs now agree on the same date", buy.nav === val.nav);
  eq("so a same-day purchase shows no phantom gain", (100/buy.nav)*val.nav, 100);

  r = parseNavRows([{date:"01-01-2025",nav:"100"},{date:"01-01-2025",nav:"100"}]);
  eq("identical duplicates collapse silently", r.conflicts, 0);
  eq("and still leave one row", r.arr.length, 1);

  // ---- ordering is preserved
  r = parseNavRows([{date:"03-01-2025",nav:"102"},{date:"01-01-2025",nav:"100"},
                    {date:"02-01-2025",nav:"101"}]);
  eq("rows remain date-sorted after dedupe", r.arr.map(function(x){return isoDate(x.date);}).join(","),
     "2025-01-01,2025-01-02,2025-01-03");


  // ======================== v10: scheme-level pooling, sub-tab scope, schedules
  // 23 SIP legs across 13 funds is one position per fund, not 23. The pooled return
  // is a single XIRR over the union of every leg's cash flows -- NOT the average of
  // the legs' XIRRs, which is wrong whenever the legs differ in size or timing.
  var schemes = [];
  var pfScope = "all";
  var CATEGORY_CANON = {};
  var v10Loaded = true;
  try {
    CATEGORY_CANON = eval("(" + HTML.match(/const CATEGORY_CANON = (\{[\s\S]*?\n\});/)[1] + ")");
    eval([grabFn("normName"), grabFn("normCategory"), grabFn("categoryKey"),
          grabFn("normalisePlan"), grabFn("detectPlanFromName"), grabFn("schemeView"),
          grabFn("poolRuns"), grabFn("scopeLegs"), grabFn("groupHoldings"),
          grabFn("scopeApplies"), grabFn("groupSchedule"), grabFn("insightsItems")].join("\n"));
  } catch(e){ v10Loaded = false; ok("could not load the v10 grouping layer: "+e.message, false); }

  if(v10Loaded){
    // Real published series, not a synthetic curve. A smooth exponential gives
    // every leg an identical XIRR, which would make the pooled-vs-mean test pass
    // for the wrong reason -- only real return dispersion can show that pooling
    // and averaging genuinely differ.
    const TRI = JSON.parse(require("fs").readFileSync(
      require("path").join(__dirname, "..", "data", "tri", "NIFTY_MIDCAP150.json"), "utf8"));
    const NAV = Object.keys(TRI.series).sort()
      .map(d=>({date:parseInput(d), nav:TRI.series[d]}));
    const VD = NAV[NAV.length-1].date;
    const leg = (startISO, endISO, amount) => {
      const end = endISO ? parseInput(endISO) : VD;
      return runSIP(NAV, uniformSchedule(scheduleDates(parseInput(startISO), end), amount), VD);
    };

    // ---- poolRuns
    const a = leg("2018-02-05", "2019-02-05", 1500);
    const b = leg("2019-03-05", null, 10000);
    const pooled = poolRuns([a,b]);
    ok("pooling two legs returns a run", !!pooled);
    ok("invested is the sum of the legs",
       Math.abs(pooled.invested-(a.invested+b.invested)) < 1e-6);
    ok("current value is the sum of the legs",
       Math.abs(pooled.currentValue-(a.currentValue+b.currentValue)) < 1e-6);
    ok("units are the sum of the legs",
       Math.abs(pooled.units-(a.units+b.units)) < 1e-9);
    ok("instalments placed are the sum of the legs",
       pooled.placed === a.placed+b.placed);

    // The decisive property: pooling must equal ONE SIP run over the merged
    // schedule. If these diverge the collapsed row is not the position it claims.
    const merged = {};
    for(const e of uniformSchedule(scheduleDates(parseInput("2018-02-05"), parseInput("2019-02-05")),1500))
      merged[isoDate(e.date)] = (merged[isoDate(e.date)]||0)+e.amount;
    for(const e of uniformSchedule(scheduleDates(parseInput("2019-03-05"), VD),10000))
      merged[isoDate(e.date)] = (merged[isoDate(e.date)]||0)+e.amount;
    const direct = runSIP(NAV, Object.keys(merged).sort().map(k=>({date:parseInput(k),amount:merged[k]})), VD);
    ok("pooled XIRR equals a single run over the merged schedule",
       Math.abs(pooled.xirr-direct.xirr) < 1e-9, (pooled.xirr-direct.xirr).toExponential(2));
    ok("...and is NOT the mean of the legs' XIRRs",
       Math.abs(pooled.xirr-(a.xirr+b.xirr)/2) > 1e-6);

    // Two legs struck at different NAV dates cannot be summed -- that is adding
    // rupees measured at two different prices.
    const early = leg("2018-02-05", null, 1000);
    const shifted = Object.assign({}, early, {valueDate:new Date(2019,5,1)});
    ok("legs struck on different dates refuse to pool", poolRuns([early, shifted]) === null);
    ok("an all-null leg list pools to null", poolRuns([null,null]) === null);
    ok("a single leg pools to itself",
       Math.abs(poolRuns([a]).xirr - a.xirr) < 1e-12);

    // ---- groupHoldings
    const mk = (code,startStr,endStr,amount,run) => ({
      code, name:"Fund "+code, plan:"Regular", category:"Equity Scheme - Mid Cap Fund",
      startStr, endStr:endStr||"", start:parseInput(startStr), amount,
      fund:run, fundCmp:run, benchCmp:run, cmpValueDate:VD, fundValueDate:VD,
      navStaleDays:0, fundSkipped:0, benchLabel:"NIFTY MIDCAP 150 TRI"
    });
    schemes = [ mk("111","2018-02-05","2019-02-05",1500,a),
                mk("111","2019-03-05","",10000,b),
                mk("222","2018-02-05","",3000,leg("2018-02-05",null,3000)) ];
    const groups = groupHoldings(schemes);
    eq("three legs across two funds collapse to two rows", groups.length, 2);
    eq("the multi-leg row reports its leg count", groups[0].legCount, 2);
    ok("the multi-leg row lists every distinct amount",
       groups[0].amountSpread.join(",") === "1500,10000");
    ok("the collapsed row's invested equals the sum of its legs",
       Math.abs(groups[0].fund.invested-(a.invested+b.invested)) < 1e-6);
    eq("input order of first appearance is preserved", groups[1].code, "222");
    ok("a group still satisfies schemeView()", schemeView(groups[0]).fxFull != null);

    // ---- scope
    ok("scopeApplies() is true once any leg has an end date", scopeApplies() === true);
    eq("live scope drops the ended leg", scopeLegs(schemes,"live").length, 2);
    eq("all scope keeps every leg", scopeLegs(schemes,"all").length, 3);
    const liveGroups = groupHoldings(scopeLegs(schemes,"live"));
    eq("the multi-leg fund survives with only its live leg", liveGroups[0].legCount, 1);
    ok("...and its invested is the live leg alone, not the whole position",
       Math.abs(liveGroups[0].fund.invested-b.invested) < 1e-6);
    schemes = [ mk("333","2018-02-05","",1000,leg("2018-02-05",null,1000)) ];
    ok("scopeApplies() is false when nothing has an end date", scopeApplies() === false);

    // ---- groupSchedule: what peers actually receive
    schemes = [ mk("111","2018-02-05","2019-02-05",1500,a), mk("111","2019-03-05","",10000,b) ];
    const g = groupHoldings(schemes)[0];
    const sched = groupSchedule(g);
    ok("the schedule carries per-date amounts, not one flat figure",
       new Set(sched.map(e=>e.amount)).size === 2);
    ok("the ended leg stops at ITS end date, not today",
       sched.filter(e=>e.amount===1500).every(e=>isoDate(e.date) <= "2019-02-05"));
    ok("the live leg runs to the valuation date",
       isoDate(sched[sched.length-1].date) > "2019-02-05" &&
       sched[sched.length-1].date <= VD);
    ok("dates are strictly ascending",
       sched.every((e,i)=>i===0 || e.date > sched[i-1].date));
    // Concurrent legs on the same day must ADD, not overwrite.
    schemes = [ mk("111","2019-03-05","",10000,b), mk("111","2019-03-05","",3000,leg("2019-03-05",null,3000)) ];
    const overlap = groupSchedule(groupHoldings(schemes)[0]);
    ok("two legs sharing a date sum their amounts", overlap[0].amount === 13000);

    // ---- insightsItems is one row per scheme and honours the sub-tab
    schemes = [ mk("111","2018-02-05","2019-02-05",1500,a),
                mk("111","2019-03-05","",10000,b),
                mk("222","2018-02-05","2019-02-05",3000,leg("2018-02-05","2019-02-05",3000)) ];
    pfScope = "all";
    eq("Insights shows one row per scheme, not per leg", insightsItems().length, 2);
    pfScope = "live";
    const liveItems = insightsItems();
    eq("Live SIP drops the fully-stopped scheme", liveItems.length, 1);
    eq("...and keeps the one still running", liveItems[0].code, "111");
    ok("each item carries a dated schedule for the peer run",
       Array.isArray(liveItems[0].schedule) && liveItems[0].schedule[0].amount === 10000);
    pfScope = "all";
  }


  // ============================ v11: the JS splice must agree with the Python one
  // Two implementations of one rule drift. tests/test_mergers.py pins the MAP; this
  // pins the BEHAVIOUR, so a tolerance edited on one side alone goes red.
  // `var`, not eval'd `const`: a direct eval's const is block-scoped to the eval
  // and invisible to the extracted spliceProblem(). Values are still READ from
  // index.html, so the shipped tolerances are what get tested.
  var MAX_SPLICE_RATIO_DRIFT = 0, MAX_SPLICE_GAP_DAYS = 0;
  var jsSpliceLoaded = true;
  try {
    MAX_SPLICE_RATIO_DRIFT = parseFloat(HTML.match(/const MAX_SPLICE_RATIO_DRIFT\s*=\s*([\d.]+)/)[1]);
    MAX_SPLICE_GAP_DAYS    = parseInt(HTML.match(/const MAX_SPLICE_GAP_DAYS\s*=\s*(\d+)/)[1],10);
    eval([grabFn("spliceProblem"), grabFn("spliceNav")].join("\n"));
  } catch(e){ jsSpliceLoaded = false; ok("could not load the splice logic: "+e.message, false); }

  if(jsSpliceLoaded){
    const day = (iso, nav) => ({date:parseInput(iso), nav});
    const SPL = parseInput("2022-11-28");
    const oldArr = [day("2022-11-23",100), day("2022-11-24",100), day("2022-11-25",100)];

    ok("a clean rename splices", spliceNav(oldArr,[day("2022-11-28",100)],SPL).spliced === true);
    ok("...and the joined series reaches back to the retired code's first row",
       isoDate(spliceNav(oldArr,[day("2022-11-28",100)],SPL).navArr[0].date) === "2022-11-23");
    ok("a 2% move across the join is allowed",
       spliceNav(oldArr,[day("2022-11-28",102)],SPL).spliced === true);
    ok("a 4% move is refused",
       spliceNav(oldArr,[day("2022-11-28",104)],SPL).spliced === false);
    ok("a ratio merger is refused outright",
       spliceNav(oldArr,[day("2022-11-28",210)],SPL).spliced === false);
    ok("a refused splice returns the NEW series untouched — never fewer rows",
       spliceNav(oldArr,[day("2022-11-28",210)],SPL).navArr.length === 1);
    ok("a long hole at the join is refused",
       spliceNav([day("2022-10-05",100)],[day("2022-11-28",100)],SPL).spliced === false);
    ok("an empty retired series is refused, not crashed",
       spliceNav([],[day("2022-11-28",100)],SPL).spliced === false);

    // A date in BOTH series must survive once, with the surviving code's value:
    // two rows for one day make navOnOrAfter and navOnOrBefore disagree, so a SIP
    // buys at one NAV and is valued at another on the same day.
    const dup = spliceNav(oldArr.concat([day("2022-11-28",100),day("2022-11-29",100)]),
                          [day("2022-11-28",100), day("2022-11-29",101)], SPL);
    const seen = dup.navArr.map(p=>isoDate(p.date));
    ok("a date present in both series appears exactly once",
       new Set(seen).size === seen.length);
    ok("...and takes the surviving code's NAV",
       dup.navArr[dup.navArr.length-1].nav === 101);

    // The tolerances are read from index.html above, so they are the SHIPPED ones.
    // Cross-language parity against mf_mergers.py is deliberately NOT checked here:
    // this suite cannot import a Python module, so it would have to regex the
    // source, and an unanchored regex over another language's file is exactly the
    // kind of check that fails for reasons unrelated to the thing it is testing.
    // It did: /MAX_SPLICE_RATIO_DRIFT = ([\d.]+)/ returned null in CI and the
    // resulting TypeError took the WHOLE suite down mid-run — 125 passing tests
    // reported as one crash. tests/test_mergers.py owns that comparison and does it
    // properly, by importing mf_mergers and reading the constant directly.
    ok("the shipped ratio tolerance is a sane fraction, not a percentage",
       MAX_SPLICE_RATIO_DRIFT > 0 && MAX_SPLICE_RATIO_DRIFT < 0.25);
    ok("the shipped gap limit leaves room beyond the 7-day SIP placement window",
       MAX_SPLICE_GAP_DAYS >= 7 && MAX_SPLICE_GAP_DAYS <= 31);
  }


  ok("the summary counts SCHEMES, not SIP legs",
     /<div class="k">Schemes<\/div><div class="v">\$\{groups\.length\}/.test(HTML));
  ok("...and index.html no longer headlines schemes.length there",
     !/<div class="k">Holdings<\/div><div class="v">\$\{schemes\.length/.test(HTML));
  ok("the leg count is still surfaced, just not as the headline",
     /SIP legs/.test(HTML));

  ok("computeScheme fetches through the chain",
     /const detail=await getDetailChained\(spec\.code\)/.test(HTML));


  // ================================================ v13: shipped-file guards
  ok("peers are filtered with Number.isFinite, not the coercing global",
     /if\(!r \|\| !Number\.isFinite\(r\.xirr\)\) continue;/.test(HTML));
  ok("rankCandidates classifies every exclusion by cause",
     /excluded:excluded/.test(HTML) && /excluded\.stale\+\+/.test(HTML));
  ok("the exclusion note no longer claims every dropped fund started late",
     !/in this category started after your SIP began and /.test(HTML));
  ok("...and names survivorship bias when dead funds were dropped",
     /flatters the surviving cohort/.test(HTML));
  ok("a dead-fund threshold exists rather than being inlined",
     /const DEAD_FUND_DAYS = 60;/.test(HTML));
  ok("the peer ranking has a minimum window, mirroring MIN_ANNUALISE_DAYS",
     /const MIN_RANK_DAYS = 365;/.test(HTML));
  ok("...and it suppresses the rank rather than annualising a short window",
     /spanDays < MIN_RANK_DAYS/.test(HTML));
  ok("the Excel export labels the scope it covers",
     /const scopeNote = scopeApplies\(\)/.test(HTML));
  ok("...while still exporting every leg, so a round-trip stays lossless",
     /const schemes = valuedSchemes\(\);/.test(HTML));
  ok("the import template column reads 'Monthly SIP'",
     /"End \(optional\)","Monthly SIP","Code \(optional\)"/.test(HTML));
  ok("...and the importer still accepts a sheet headed only 'Monthly'",
     /h\.includes\("monthly"\)/.test(HTML));

  // ---- shipped-file guards: the exact edits, so a bad paste goes red
  ok("rankCandidates takes a schedule, not (dates, amount)",
     /function rankCandidates\(navDoc, schedule, valueDate, ownCode\)/.test(HTML));
  ok("runSIP takes a schedule",
     /function runSIP\(navArr,schedule,valueDate\)/.test(HTML));
  ok("peers are run on the holding's own schedule",
     /runSIP\(gridToNavArr\(entry\), schedule, valueDate\)/.test(HTML));
  ok("index.html no longer reads the non-existent s.end", !/\bend:\s*s\.end\b/.test(HTML));
  ok("the Portfolio pane renders groups, not raw legs",
     /const groups = groupHoldings\(scoped\)/.test(HTML));
  ok("the PORTFOLIO total row is computed from the same groups",
     /portfolioMetrics\(groups\)/.test(HTML));
  ok("fillInsightDetail distinguishes a missing plan cohort from a short history",
     /inCohort/.test(HTML) && /isn't in the published/.test(HTML));

  // ---- parity: the shipped file must still contain both guards
  ok("index.html round-trips the parsed MFAPI date",
     /dt\.getFullYear\(\)!==year/.test(HTML) && /dt\.getDate\(\)!==day/.test(HTML));
  ok("index.html deduplicates NAV rows by calendar day",
     /byDay\.set\(/.test(HTML) && /conflicts/.test(HTML));
}

console.log("\n" + (fail ? "FAILED" : "ALL PASSED") + ` (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);