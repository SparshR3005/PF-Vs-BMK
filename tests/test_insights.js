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
const HORIZON_LABELS = [["6m","6 Month"],["1y","1 Year"],["2y","2 Years"],["3y","3 Years"],
                        ["5y","5 Years"],["7y","7 Years"],["10y","10 Years"]];
const QUARTILE_WORD = {1:"Top quartile",2:"2nd quartile",3:"3rd quartile",4:"Bottom quartile"};
// qBadge() reads this; the real strings are asserted against index.html below.
const QUARTILE_HELP = {1:"a",2:"b",3:"c",4:"d"};
const NAME_STOPWORDS = new Set(["direct","regular","plan","growth","option","opt",
                                "payout","reinvestment","idcw","dividend"]);
const NAME_ACRONYMS = new Set(["SBI","HSBC","ICICI","HDFC","UTI","LIC","IDFC","DSP","PGIM",
  "BNP","JM","IIFL","ITI","NJ","BOI","PPFAS","AMC","ELSS","LT","TATA","IDBI","BOB",
  "MF","TRUSTMF","WOC","NAV","TRI","SIP","XIRR","FOF","IDCW","ONE",
  "US","UK","ESG","REIT","INVIT","PSU","FMCG","IT","NIFTY","BSE","NSE","CRISIL"]);

const NEEDED = ["titleCaseWord","titleCaseName","normaliseFundName","gridToNavArr",
                "rankCandidates","qBadge","signedPP","periodTableHtml","topListHtml"];
let loaded = true;
try { eval(NEEDED.map(grabFn).join("\n")); }
catch(e){ loaded = false; console.log("  FAIL  index.html is missing Insights machinery: " + e.message); fail++; }

// runSIP/scheduleDates come from index.html too -- rankCandidates calls them.
try { eval([grabFn("navOnOrAfter"), grabFn("navOnOrBefore"), grabFn("addMonths"),
            grabFn("daysBetween"), grabFn("isoDate"), grabFn("parseInput"),
            grabFn("normaliseFlows"), grabFn("xirr"), grabFn("scheduleDates"),
            grabFn("valuationAt"), grabFn("runSIP")].join("\n")); }
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
  const res = rankCandidates({funds}, dates, 5000, VALUE, "F3");
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
  const res2 = rankCandidates({funds:withLate}, dates, 5000, VALUE, "F3");
  ok("a mid-window launch is excluded, not ranked first",
     res2.top.every(r => String(r.code) !== "LATE"));
  eq("and the eligible universe is unchanged", res2.universe, 14);

  eq("an empty category ranks nothing", rankCandidates({funds:{}}, dates, 5000, VALUE, "X").universe, 0);

  // A malformed document must be distinguishable from a genuinely empty cohort.
  // Both give universe 0, so without the flag a shape bug looks like a real result
  // -- which is exactly how two assertions in this file once passed vacuously.
  ok("a malformed document is flagged, not silently empty",
     rankCandidates({F0:{n:"x",t0:"2016-01-04",d:[0],v:[1]}}, dates, 5000, VALUE, "F0").malformed === true);
  ok("a genuinely empty cohort is NOT flagged malformed",
     rankCandidates({funds:{}}, dates, 5000, VALUE, "X").malformed !== true);
  ok("null document is flagged", rankCandidates(null, dates, 5000, VALUE, "X").malformed === true);

  // ------------------------------------------------------------ suppression
  const three = {};
  for(let i=0;i<3;i++){ const [c,e]=synth("T"+i,"Tiny "+i,0.08+0.01*i,560); three[c]=e; }
  const small = rankCandidates({funds:three}, dates, 5000, VALUE, "T0");
  ok("a 3-fund cohort is below the top-five threshold", small.universe < MIN_TOP_UNIVERSE);
  ok("and the UI says so instead of listing a top five",
     /would be noise/.test(topListHtml(small, small.ownXirr)));
  ok("a large cohort does get a list", /<ol class="top5">/.test(topListHtml(res, res.ownXirr)));

  // ------------------------------------------------------------ formatting
  eq("positive spreads carry a plus", signedPP(4.2), "+4.20 pp");
  ok("negative spreads use a real minus sign", signedPP(-4.2) === "\u22124.20 pp");
  eq("null spread renders as a dash", signedPP(null), "—");
  eq("no badge when the quartile is suppressed", qBadge(null), "");
  ok("quartile 1 reads as top", /Top quartile/.test(qBadge(1)));
  ok("quartile 4 reads as bottom", /Bottom quartile/.test(qBadge(4)));

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
  ok("sub-year rows carry no annualised figure",
     /<td>6 Month<\/td><td class="col-abs">6\.30%<\/td><td>—<\/td>/.test(tbl));
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

  // Quartile chips must be legible, i.e. use the theme's own accent colours.
  ok("quartile chips use theme colours",
     /\.q1\{[^}]*var\(--beat\)/.test(cssBlock) && /\.q4\{[^}]*var\(--lag\)/.test(cssBlock));

  // The label alone did not convey the meaning; it needs to explain itself.
  ok("quartile chips carry an explanatory tooltip", /QUARTILE_HELP/.test(HTML));
  ok("all four quartiles have help text",
     [1,2,3,4].every(q => new RegExp(q + ':"[^"]{10,}"').test(HTML)));
  ok("the footer explains what a quartile is", /split the category into four equal groups/.test(HTML));

  // ------------------------------------------------------------ wiring
  ok("the tab bar exists in the markup", /id="tabbar"/.test(HTML));
  ok("both tab buttons exist", /id="tabPortfolio"/.test(HTML) && /id="tabInsights"/.test(HTML));
  ok("panes are present", /id="paneInsights"/.test(HTML) && /id="panePortfolio"/.test(HTML));
  ok("tabs are wired to switchTab", /switchTab\("insights"\)/.test(HTML));
  ok("ranks files are fetched with no-store (they change daily)",
     /cache:"no-store"/.test(HTML));
  ok("sector funds are explained rather than silently absent",
     /pharma fund/i.test(HTML));
  ok("the disclaimer states ranking ignores risk", /ignores risk/i.test(HTML));
  ok("the disclaimer warns about survivorship", /merged or closed/i.test(HTML));
  ok("stale categories are surfaced to the user", /failed its last publish check/.test(HTML));
}

console.log("\n" + (fail ? "FAILED" : "ALL PASSED") + ` (${pass} passed, ${fail} failed)`);
process.exit(fail ? 1 : 0);
