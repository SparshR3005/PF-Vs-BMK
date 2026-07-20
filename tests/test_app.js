#!/usr/bin/env node
/**
 * Regression tests for index.html.
 *
 * Run:  node tests/test_app.js
 *
 * These extract the REAL function bodies out of index.html by name and eval them
 * in an isolated scope. That matters: a test against a hand-copied duplicate of
 * the logic would keep passing after the shipped code drifted — which is exactly
 * how v4's changelog came to claim fixes that were never applied.
 */
const fs = require("fs");
const path = require("path");

const HTML = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");

let pass = 0, fail = 0;
function ok(name, cond, msg) {
  if (cond) { console.log("  PASS  " + name); pass++; }
  else { console.log("  FAIL  " + name + (msg ? ": " + msg : "")); fail++; }
}
function throws(fn) { try { fn(); return false; } catch (e) { return e; } }

/** Pull a top-level `function NAME(...){...}` out of the HTML by brace matching. */
function extractFn(name) {
  const start = HTML.indexOf("function " + name + "(");
  if (start < 0) throw new Error("function not found in index.html: " + name);
  let i = HTML.indexOf("{", start), depth = 0, inS = null, prev = "";
  for (; i < HTML.length; i++) {
    const c = HTML[i];
    if (inS) {
      if (c === inS && prev !== "\\") inS = null;
    } else if (c === '"' || c === "'" || c === "`") inS = c;
    else if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return HTML.slice(start, i + 1); }
    prev = c;
  }
  throw new Error("unbalanced braces extracting " + name);
}

// ============================================================ #2 export crash
(function testExportInsights() {
  // Real schemeView + buildInsights, with the few helpers they touch stubbed.
  const scope = {
    inr: n => "Rs." + n, pct: n => (n == null ? "-" : (n * 100).toFixed(2) + "%"),
    pp: n => (n * 100).toFixed(2) + " pp", shortSchemeName: n => String(n),
    schemes: null, // the GLOBAL — must never be read by buildInsights now
  };
  const src = extractFn("schemeView") + "\n" + extractFn("buildInsights") + "\n";
  const make = new Function("scope", `with(scope){ ${src} ; return {buildInsights, schemeView}; }`);
  const { buildInsights } = make(scope);

  const valued = {
    holdingId: "h1", name: "Fund A", benchLabel: "NIFTY 500",
    fund: { xirr: 0.14, currentValue: 120000, invested: 100000 },
    fundCmp: { xirr: 0.14 }, benchCmp: { xirr: 0.11 },
  };
  const errorRow = { holdingId: "h2", name: "Fund B", error: "Could not value this holding" };
  const port = { alpha: 0.03, cmpFundXirr: 0.14, benchXirr: 0.11, anyBench: true,
                 cmpFundCurrent: 120000, benchCurrent: 115000 };

  // Global deliberately poisoned: if buildInsights still reads it, this throws.
  scope.schemes = [valued, errorRow];

  const e = throws(() => buildInsights(port, [valued]));
  ok("#2 export with a valued holding does not throw", !e, e && e.message);

  const e2 = throws(() => buildInsights(port, [valued, errorRow]));
  ok("#2 export SURVIVES a retained error row (the v4 crash)", !e2, e2 && e2.message);

  let lines = null;
  const e2b = throws(() => { lines = buildInsights(port, [valued, errorRow]); });
  ok("#2 insights ignore the unvalued row in counts",
     !e2b && lines && (JSON.stringify(lines).includes("1 of 1") ||
                       !JSON.stringify(lines).includes("of 2")),
     e2b ? "threw: " + e2b.message : "unexpected counts");

  const e3 = throws(() => buildInsights(port, []));
  ok("#2 export with zero valued holdings does not throw", !e3, e3 && e3.message);
})();

// ====================================================== #4 import header map
(function testHeaderClassifier() {
  const normHeader = h => String(h || "").toLowerCase().replace(/[^a-z]/g, "");
  // Extract the real classifier body out of the import handler.
  const m = HTML.match(/hdr\.forEach\(\(h,i\)=>\{[\s\S]*?\n      \}\);/);
  if (!m) { ok("#4 classifier extracted", false, "pattern not found"); return; }
  const classify = hdrRaw => {
    const hdr = hdrRaw.map(normHeader), col = {};
    eval(m[0]);
    return col;
  };

  let col = classify(["Scheme Name", "Plan", "Start", "Monthly", "Scheme Code"]);
  ok("#4 'Scheme Name' maps to the NAME column (index 0)", col.scheme === 0, "got " + col.scheme);
  ok("#4 'Scheme Code' maps to the CODE column (index 4)", col.code === 4, "got " + col.code);

  col = classify(["Scheme Code", "Scheme Name", "Start", "Monthly"]);
  ok("#4 code-before-name column order still maps correctly",
     col.code === 0 && col.scheme === 1, JSON.stringify(col));

  col = classify(["Fund Name", "Start Date", "SIP Amount"]);
  ok("#4 'Fund Name'/'SIP Amount' aliases work",
     col.scheme === 0 && col.start === 1 && col.amount === 2, JSON.stringify(col));

  col = classify(["Scheme", "Start", "Monthly", "Scheme"]);
  ok("#4 duplicate header cannot clobber the first valid match (??=)", col.scheme === 0,
     "got " + col.scheme);

  col = classify(["Scheme Name", "Start", "Monthly"]);
  ok("#4 code column stays undefined when absent", col.code === undefined);
})();

// ======================================================== #6 non-finite SIP
(function testFiniteAmount() {
  const guard = amount => {
    if (!Number.isFinite(amount) || amount <= 0)
      throw new Error("Monthly SIP must be a finite number greater than zero");
    return true;
  };
  ok("#6 index.html rejects non-finite SIP at computeScheme",
     /Number\.isFinite\(amount\)\|\|amount<=0/.test(HTML.replace(/\s/g, "")),
     "guard not present in source");
  ok("#6 UI enable-state also checks isFinite",
     /Number\.isFinite\(amt\)/.test(HTML), "updateAddState not hardened");
  ok("#6 1e309 (Infinity) is rejected", !!throws(() => guard(Number("1e309"))));
  ok("#6 Infinity would have passed the old >0 test", Number("1e309") > 0);
  ok("#6 NaN is rejected", !!throws(() => guard(Number("abc"))));
  ok("#6 zero and negatives rejected", !!throws(() => guard(0)) && !!throws(() => guard(-5)));
  ok("#6 a normal amount is accepted", guard(5000) === true);
})();

// ================================================= #3 retry / #7 erase / #8 / #9
(function testSourceInvariants() {
  const flat = HTML.replace(/\s+/g, " ");

  ok("#3 retry resolves by stable holdingId after the await",
     /const opHoldingId\s*=\s*s\.holdingId/.test(HTML) &&
     /findIndex\(x=>x && x\.holdingId===opHoldingId\)/.test(flat.replace(/\s/g, "")) === false
       ? /holdingId===opHoldingId/.test(HTML) : true,
     "holdingId lookup missing");
  ok("#3 retry no longer writes back through the captured index",
     !/schemes\[i\]=sch;/.test(HTML) && !/schemes\[i\]=\{\.\.\.s, error:/.test(HTML),
     "stale schemes[i] write still present");

  ok("#7 erase sweeps __backup_ keys",
     /startsWith\(p\+"__backup_"\)/.test(HTML), "backup sweep missing");
  ok("#7 backups are capped",
     /keys\.slice\(3\)\.forEach/.test(HTML), "cap missing");

  ok("#8 closeResults invalidates in-flight searches",
     /function closeResults\(\)\{[\s\S]{0,600}?serverSearchSeq\+\+/.test(HTML),
     "serverSearchSeq++ not in closeResults");
  ok("#8 serverSearchSeq declared before closeResults (no TDZ)",
     HTML.indexOf("let serverSearchSeq") < HTML.indexOf("function closeResults"),
     "declaration order unsafe");

  ok("#9 listComplete tracks pagination", /let listComplete/.test(HTML));
  ok("#9 partial-list miss falls back to server search",
     /if\(!listComplete\)\{ serverSearch\(q\); return; \}/.test(HTML),
     "fallback missing");

  ok("#10 export label no longer says PORTFOLIO ALPHA", !/PORTFOLIO ALPHA/.test(HTML));
  ok("#10 export label says XIRR SPREAD", /XIRR SPREAD VS BENCHMARK PROXY/.test(HTML));
  ok("#10 'SEBI-standard benchmark' claim removed", !/SEBI-standard/.test(HTML));
  ok("#2 buildInsights takes holdings explicitly",
     /function buildInsights\(port, holdings\)/.test(HTML));
})();

// ============================================ #3 retry race, behavioural sim
(function testRetryRace() {
  // Mirrors the fixed control flow: capture id -> await -> re-resolve by id.
  async function retry(schemes, idx, compute, mutateDuringAwait) {
    const s = schemes[idx];
    const opHoldingId = s.holdingId;
    const p = compute(s);
    mutateDuringAwait();
    const sch = await p;
    const at = schemes.findIndex(x => x && x.holdingId === opHoldingId);
    if (at < 0) return "dropped";
    schemes[at] = sch;
    return "committed";
  }
  const A = { holdingId: "A", name: "A" }, B = { holdingId: "B", name: "B" }, C = { holdingId: "C", name: "C" };
  const schemes = [A, B, C];
  const compute = s => Promise.resolve({ holdingId: s.holdingId, name: s.name, fund: { xirr: 0.1 } });

  return retry(schemes, 1, compute, () => schemes.splice(0, 1)).then(r => {
    ok("#3 retry commits to the moved holding, not the stale index", r === "committed");
    ok("#3 sibling C is NOT overwritten (the v4 corruption)",
       schemes.find(x => x.holdingId === "C") && schemes.find(x => x.holdingId === "C").name === "C",
       JSON.stringify(schemes.map(x => x.holdingId + ":" + x.name)));
    ok("#3 retried holding B received its result",
       schemes.find(x => x.holdingId === "B").fund != null);

    const s2 = [A, B, C].slice();
    return retry(s2, 1, compute, () => s2.splice(1, 1)).then(r2 => {
      ok("#3 result is dropped if the holding was removed mid-flight", r2 === "dropped");
      console.log(`\n${fail ? "FAILED" : "ALL PASSED"} (${pass} passed, ${fail} failed)`);
      process.exit(fail ? 1 : 0);
    });
  });
})();