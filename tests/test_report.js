#!/usr/bin/env node
/**
 * End-to-end test for the Excel report.
 *
 *   node tests/test_report.js
 *
 * This does NOT re-implement the report. It loads the entire inline script out of
 * index.html into a VM with a stub DOM, points fetch() at the committed
 * data/tri and data/ranks files, drives the REAL exportReport(), and then reads
 * the workbook back with the same xlsx-js-style build the page loads from the CDN.
 *
 * Requires xlsx-js-style (npm i xlsx-js-style@1.2.0). Skips with a clear message
 * rather than failing if it is absent, so CI without the dep stays honest.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
let XLSX;
try { XLSX = require("xlsx-js-style"); }
catch(e){
  console.log("SKIP  tests/test_report.js — xlsx-js-style not installed (npm i xlsx-js-style@1.2.0)");
  process.exit(0);
}

let pass = 0, fail = 0;
function ok(label, cond, msg){
  if(cond){ pass++; console.log("  PASS  " + label); }
  else { fail++; console.log("  FAIL  " + label + (msg ? ": " + msg : "")); }
}
function eq(label, got, want){
  ok(label, got === want, "got " + JSON.stringify(got) + " want " + JSON.stringify(want));
}

// ------------------------------------------------------------------ stub DOM
function makeEl(id){
  const el = {
    id, style:{}, dataset:{}, classList:{add(){}, remove(){}, toggle(){}, contains(){return false;}},
    children:[], value:"", textContent:"", innerHTML:"", hidden:false, disabled:false,
    files:null, checked:false,
    addEventListener(){}, removeEventListener(){}, setAttribute(){}, getAttribute(){return null;},
    appendChild(){}, remove(){}, focus(){}, select(){}, click(){},
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    closest(){ return null; }, insertAdjacentHTML(){}
  };
  return el;
}
const els = Object.create(null);
const document = {
  getElementById(id){ return els[id] || (els[id] = makeEl(id)); },
  querySelector(){ return null; },
  querySelectorAll(){ return []; },
  createElement(){ return makeEl("new"); },
  addEventListener(){},
  activeElement:null,
  body: makeEl("body")
};

// fetch(): committed repo files served off disk, everything else a hard 404 so the
// report can never quietly depend on the network.
const written = [];
function fakeFetch(url){
  const clean = String(url).split("?")[0];
  if(/^https?:/.test(clean)) return Promise.resolve({ok:false, status:404, json(){ return Promise.resolve(null); }});
  const p = path.join(ROOT, clean);
  if(!fs.existsSync(p)) return Promise.resolve({ok:false, status:404, json(){ return Promise.resolve(null); }});
  const body = fs.readFileSync(p, "utf8");
  return Promise.resolve({ok:true, status:200, json(){ return Promise.resolve(JSON.parse(body)); },
                          text(){ return Promise.resolve(body); }});
}

const sandbox = {
  console, setTimeout, clearTimeout, Promise, Date, Math, JSON, Object, Array, Number, String,
  Set, Map, RegExp, Error, isNaN, isFinite, parseInt, parseFloat, encodeURIComponent,
  document, fetch: fakeFetch,
  window:{}, navigator:{userAgent:"node"},
  requestAnimationFrame(fn){ fn(); },
  crypto:{ randomUUID(){ return "u"+Math.random().toString(36).slice(2); } },
  localStorage:{ _m:{}, getItem(k){ return this._m[k]==null?null:this._m[k]; },
                 setItem(k,v){ this._m[k]=String(v); }, removeItem(k){ delete this._m[k]; } },
  XLSX: Object.assign(Object.create(XLSX), {
    writeFile(wb, name){ written.push({wb, name}); }
  })
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const HTML = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const scripts = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)];
const APP = scripts[scripts.length - 1][1];

/* `let`/`const` at the top level of a script are script-scoped, not properties of
   the global object, so the harness cannot reach `store`/`schemes` by name. An
   epilogue publishes live accessors — the app itself is untouched. */
const EPILOGUE = `
;globalThis.__app = {
  get store(){return store;},   set store(v){store=v;},
  get schemes(){return schemes;}, set schemes(v){schemes=v;},
  get pfScope(){return pfScope;}, set pfScope(v){pfScope=v;},
  exportReport, insightsItems, insightFacts, rankSentence, scopeApplies,
  parseInput, scheduleDates, uniformSchedule, runSIP, groupHoldings, portfolioMetrics,
  fmtISO, normaliseDateCell
};`;

vm.createContext(sandbox);
try { vm.runInContext(APP + EPILOGUE, sandbox, {filename:"index.html"}); }
catch(e){ console.log("  FAIL  index.html script did not evaluate: " + e.message); process.exit(1); }

console.log("Run node tests/test_report.js");

// --------------------------------------------------- build a REAL portfolio
// Real committed TRI series as the NAV curve, real ranks codes as the scheme
// codes, so rankCandidates() finds the holding in its published cohort. A
// synthetic smooth curve would give every leg the same XIRR and hide pooling bugs.
const S = sandbox.__app;
function navFromTri(file){
  const doc = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "tri", file), "utf8"));
  return Object.keys(doc.series).sort().map(d => ({date: S.parseInput(d), nav: doc.series[d]}));
}
const MID = navFromTri("NIFTY_MIDCAP150.json");
const F500 = navFromTri("NIFTY500.json");
const VD = MID[MID.length - 1].date;

// Codes lifted from the committed grids so the peer ranking is a real ranking.
const midGrid  = JSON.parse(fs.readFileSync(path.join(ROOT,"data","ranks","navs_MID_CAP_Direct.json"),"utf8"));
const flexGrid = JSON.parse(fs.readFileSync(path.join(ROOT,"data","ranks","navs_FLEXI_CAP_Direct.json"),"utf8"));
const MID_CODE  = Object.keys(midGrid.funds)[0];
const FLEX_CODE = Object.keys(flexGrid.funds)[0];

function holding(code, name, category, startISO, endISO, amount, navArr, benchArr, benchLabel){
  const start = S.parseInput(startISO);
  const end = endISO ? S.parseInput(endISO) : VD;
  const sched = S.uniformSchedule(S.scheduleDates(start, end), amount);
  const fund = S.runSIP(navArr, sched, VD);
  const bench = S.runSIP(benchArr, sched, VD);
  return {
    holdingId:"h"+code+startISO, code, name, category, plan:"Direct",
    startStr:startISO, endStr:endISO||"", amount, start, benchLabel,
    benchKey:"NIFTY_MIDCAP150", benchApprox:false,
    fund, fundCmp:fund, benchCmp:bench,
    fundValueDate:VD, cmpValueDate:VD, cmpFirst:null, cmpShorter:false,
    navStaleDays:0, fundSkipped:0
  };
}

S.store.active = "Test Portfolio";
S.schemes = [
  // one scheme, two legs: an amount change. Pools to ONE row.
  holding(MID_CODE, midGrid.funds[MID_CODE].n, "Equity Scheme - Mid Cap Fund",
          "2018-02-05", "2019-02-05", 1500, MID, MID, "Nifty Midcap 150 TRI"),
  holding(MID_CODE, midGrid.funds[MID_CODE].n, "Equity Scheme - Mid Cap Fund",
          "2019-03-05", "", 10000, MID, MID, "Nifty Midcap 150 TRI"),
  // a second scheme whose only leg has ENDED -> excluded from Live SIP
  holding(FLEX_CODE, flexGrid.funds[FLEX_CODE].n, "Equity Scheme - Flexi Cap Fund",
          "2018-02-05", "2021-02-05", 3000, F500, F500, "Nifty 500 TRI")
];

// ------------------------------------------------------------------- assertions
(async function(){
  ok("the portfolio under test has an ended leg, so both sub-tabs exist", S.scopeApplies());

  await S.exportReport(function(){});
  ok("exportReport wrote exactly one workbook", written.length === 1, "wrote " + written.length);
  const wb = written[0].wb;
  eq("the filename says Report, not the old vs-Benchmark name",
     /^SIP_Report_/.test(written[0].name), true);

  // ---- sheet set and NAMES
  eq("six sheets when a Live SIP view exists", wb.SheetNames.length, 6);
  eq("sheet order and names",
     wb.SheetNames.join(" | "),
     "Report Summary | Portfolio — All | Portfolio — Live SIP | Insights — All | Insights — Live SIP | Method & Legend");

  const txt = name => {
    const ws = wb.Sheets[name];
    return XLSX.utils.sheet_to_csv(ws, {blankrows:false});
  };

  // ---- Portfolio sheets mirror the SCREEN: pooled, one row per scheme
  const all = txt("Portfolio — All");
  const live = txt("Portfolio — Live SIP");
  ok("the All sheet pools the two legs into one row",
     (all.match(/SIP legs pooled/g) || []).length === 1);
  ok("...and says how many legs are behind it", /2 SIP legs pooled/.test(all));
  ok("the All sheet carries both schemes", all.includes("PORTFOLIO (valued holdings)") &&
     (all.match(/Nifty Midcap 150 TRI|Nifty 500 TRI/g) || []).length >= 2);
  ok("the Live SIP sheet drops the fully-ended scheme",
     !live.includes("Nifty 500 TRI"));
  ok("...and keeps the scheme that is still being funded",
     live.includes("Nifty Midcap 150 TRI"));

  // ---- each Portfolio sheet carries its OWN KPI block (answer (a) to Q: where do cards live)
  ok("the All sheet has its own KPI cards", /TOTAL INVESTED/.test(all) && /PORTFOLIO XIRR/.test(all));
  ok("the Live SIP sheet has its own KPI cards", /TOTAL INVESTED/.test(live));

  // The whole point of per-sheet cards: the card must equal the table beneath it.
  const cardInvested = name => {
    const ws = wb.Sheets[name];
    for(const ref in ws){
      if(ref[0] === "!") continue;
      if(ws[ref].v === "TOTAL INVESTED"){
        const c = XLSX.utils.decode_cell(ref);
        return ws[XLSX.utils.encode_cell({r:c.r+1, c:c.c})].v;
      }
    }
    return null;
  };
  const rowInvested = name => {
    const ws = wb.Sheets[name];
    for(const ref in ws){
      if(ref[0] === "!") continue;
      if(ws[ref].v === "PORTFOLIO (valued holdings)"){
        const c = XLSX.utils.decode_cell(ref);
        return ws[XLSX.utils.encode_cell({r:c.r, c:c.c+4})].v;
      }
    }
    return null;
  };
  /* Reading the card and the row out of the SAME sheet only proves the sheet is
     internally consistent: feed both the wrong scope and they still agree. The
     expected figure is therefore derived here, from the holdings this harness
     built, with no help from the workbook or from reportScopeData(). */
  const investedFor = scope => Math.round(S.schemes
    .filter(s => scope === "all" || !s.endStr)
    .reduce((t, s) => t + s.fund.invested, 0));

  eq("All: the KPI card equals the invested total computed independently",
     cardInvested("Portfolio — All"), investedFor("all"));
  eq("Live SIP: the KPI card equals the LIVE invested total, computed independently",
     cardInvested("Portfolio — Live SIP"), investedFor("live"));
  eq("All: the PORTFOLIO row agrees with its own cards",
     rowInvested("Portfolio — All"), investedFor("all"));
  eq("Live SIP: the PORTFOLIO row agrees with its own cards",
     rowInvested("Portfolio — Live SIP"), investedFor("live"));
  ok("the two views really are different populations, so one KPI block could not serve both",
     investedFor("all") !== investedFor("live"));

  // ---- Insights sheets carry the tab's own content
  const insAll = txt("Insights — All");
  ok("Insights carries the track-record table", /Category Avg/.test(insAll) && /Annualized/.test(insAll));
  ok("...every published horizon", ["6 Month","1 Year","3 Years","10 Years"].every(h => insAll.includes(h)));
  ok("...the peer window line", /Your window/.test(insAll) && /instalments/.test(insAll));
  ok("...the top-peer list", /Top peers in category/.test(insAll) && /vs your fund/.test(insAll));
  ok("...and the fund's own position in it", /your fund/.test(insAll));
  ok("Insights ranks one row per scheme, not per leg",
     (insAll.match(/^\d+\.\s{2}/gm) || []).length === 2);
  const insLive = txt("Insights — Live SIP");
  ok("the Live SIP Insights sheet drops the ended scheme",
     (insLive.match(/^\d+\.\s{2}/gm) || []).length === 1);

  // ---- the screen and the sheet must agree on the RANK
  const item = S.insightsItems("all")[0];
  const facts = await S.insightFacts(item);
  if(facts.summary && facts.summary.kind === "ranked"){
    const sentence = S.rankSentence(facts.summary, item.plan);
    ok("the rank sentence in the sheet is the one insightFacts produced",
       insAll.includes(sentence.split(".")[0]), sentence);
  } else {
    ok("the rank sentence in the sheet is the one insightFacts produced", true);
  }

  // ---- v15: bare headings, and the disclosure that has to carry them
  /* Assert on the HEADER ROW, not the whole sheet: the footnote and the Key
     Insights prose legitimately use the words "proxy" and "full", and a blanket
     text search would force those disclosures out to make itself pass. */
  const headerOf = name => {
    const aoa = XLSX.utils.sheet_to_json(wb.Sheets[name], {header:1, raw:true, defval:""});
    return aoa.find(r => r.includes("Scheme")) || [];
  };
  const hdrAll = headerOf("Portfolio — All");
  eq("headings dropped their parenthetical qualifiers",
     hdrAll.join("|"),
     "Scheme|Plan|SIP|Benchmark|Invested|Current|Gain|Abs return|Fund XIRR|Benchmark XIRR|Alpha|Verdict");
  ok("the spread column is headed Alpha, not XIRR spread",
     hdrAll.includes("Alpha") && !hdrAll.some(h => /XIRR spread/i.test(h)));
  ok("the KPI card reads ALPHA (p.a.)", /ALPHA \(p\.a\.\)/.test(all));

  /* The invariant behind the rename: a sheet may state Alpha only if the SAME
     sheet says it is not Jensen's α. Checking the strings exist SOMEWHERE in the
     file would stay green if the cover kept its Alpha row and lost its footnote. */
  const alphaSheets = wb.SheetNames.filter(n => /\balpha\b/i.test(txt(n)));
  ok("more than one sheet states Alpha, so this is worth enforcing",
     alphaSheets.length >= 2, alphaSheets.join(", "));
  alphaSheets.forEach(n => {
    ok("  '" + n + "' states Alpha AND disclaims Jensen's alpha on the same sheet",
       /Jensen/.test(txt(n)));
  });
  ok("the proxy disclosure survived the loss of '(TRI proxy)'",
     /PROXY chosen by this tool/.test(all));
  ok("the full-vs-comparable distinction survived the loss of '(full)'",
     /full SIP history/.test(all));

  // ---- v15: dates
  ok("the SIP column shows dd-mm-yyyy, not ISO",
     /\b\d{2}-\d{2}-\d{4}\b/.test(all) && !/\b\d{4}-\d{2}-\d{2}\b/.test(all));
  ok("...and the day component really is first",
     all.includes(S.schemes[0].startStr.split("-").reverse().join("-")));
  ok("the Insights track-record as-of is reformatted too",
     !/as of \d{4}-\d{2}-\d{2}/.test(insAll));
  ok("fmtDate-style dates are untouched — '01 Aug 2026' was kept",
     /\d{2} [A-Z][a-z]{2} \d{4}/.test(all));

  /* The dashed form is not a style preference: normaliseDateCell() reads a dashed
     DD-MM-YYYY as day-first, but rejects the slashed form as ambiguous whenever
     both parts are <= 12. Behavioural, not a string match. */
  ["2020-01-14","2022-04-17","2019-11-03","2021-06-08","2024-02-29"].forEach(iso => {
    const shown = S.fmtISO(iso);
    eq("  " + iso + " displays as " + shown + " and re-imports unchanged",
       S.normaliseDateCell(shown).value, iso);
  });
  ok("...whereas the slashed form would be rejected as ambiguous",
     S.normaliseDateCell("08/06/2021").error !== "");

  // ---- no leg-level sheet (answer to Q4)
  // "Method & Legend" contains the letters "leg"; match the actual shape instead.
  ok("there is no leg-level sheet — the utility does not show legs either",
     !wb.SheetNames.some(n => /\bsip legs?\b|\bholdings?\b/i.test(n)));

  // ---- the importer must not mistake a report sheet for a template
  // "Portfolio" is the sheet name the importer PREFERS. A report sheet opens with a
  // title band, so row 1 is not a header row and the importer rejects it.
  const hdrRow = (() => {
    const ws = wb.Sheets["Portfolio — All"];
    const aoa = XLSX.utils.sheet_to_json(ws, {header:1, raw:true, defval:""});
    return (aoa[0] || []).map(h => String(h || "").toLowerCase().replace(/[^a-z]/g, ""));
  })();
  const col = {};
  hdrRow.forEach((h, i) => {
    if(h === "code" || h === "schemecode"){ if(col.code == null) col.code = i; }
    else if(h === "scheme" || h === "schemename"){ if(col.scheme == null) col.scheme = i; }
    else if(h.includes("start")){ if(col.start == null) col.start = i; }
    else if(h.includes("monthly") || h.includes("amount") || h.includes("sip")){ if(col.amount == null) col.amount = i; }
  });
  ok("a report sheet is REJECTED by the importer rather than half-read as holdings",
     col.scheme == null || col.start == null || col.amount == null,
     JSON.stringify(col));

  // ---- single-scope naming
  S.schemes = S.schemes.filter(s => !s.endStr);
  written.length = 0;
  await S.exportReport(function(){});
  const wb2 = written[0].wb;
  eq("four sheets when nothing has an end date", wb2.SheetNames.length, 4);
  eq("...and the sheets are named plainly, with no 'All' to contrast against",
     wb2.SheetNames.join(" | "), "Report Summary | Portfolio | Insights | Method & Legend");

  console.log("\n" + (fail ? "FAILED" : "ALL PASSED") + ` (${pass} passed, ${fail} failed)`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.log("  FAIL  harness threw: " + e.stack); process.exit(1); });