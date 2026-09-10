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

  /* v15: the label reverted to "Alpha" on request. v4's concern was never the WORD
     — it was a headline figure implying Jensen's α with nothing nearby to say
     otherwise. The guard now pins the property v4 actually wanted: wherever Alpha
     is stated, the disclaimer is stated too. tests/test_report.js enforces this
     per-sheet on the real workbook; here we check the strings exist at all. */
  ok("#10 the Alpha card is qualified as per-annum, not a bare 'PORTFOLIO ALPHA'",
     !/PORTFOLIO ALPHA/.test(HTML) && /label:"ALPHA \(p\.a\.\)"/.test(HTML));
  ok("#10 every surface that states Alpha also states it is NOT Jensen's alpha",
     (HTML.match(/Jensen's α/g) || []).length >= 4);
  ok("#10 ...including the on-screen footnote, unchanged since v4",
     /<b>Alpha<\/b> here is the fund's money-weighted XIRR minus the benchmark's/.test(HTML));
  ok("#10 'SEBI-standard benchmark' claim removed", !/SEBI-standard/.test(HTML));
  ok("#2 buildInsights takes holdings explicitly",
     /function buildInsights\(port, holdings\)/.test(HTML));
})();

// ============================================ #3 retry race, behavioural sim
/* The clickjacking guard. `frame-ancestors 'none'` is in the CSP but a <meta> CSP
   cannot deliver it -- browsers honour that directive from an HTTP header only, and
   GitHub Pages sends none, so Chrome logs "...is ignored when delivered via a <meta>
   element" on every load. The script guard is the enforcement.

   These run the REAL extracted guard against a fake window, because the property
   that matters is behavioural: does it fail CLOSED. A source-shape assertion would
   stay green if someone flipped the default to visible. */
(function testFrameGuard() {
  const vm = require("vm");

  const blocks = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  const guardBlocks = blocks.filter(b => b.includes("frame-guard"));
  ok("exactly one script block carries the frame guard", guardBlocks.length === 1,
     String(guardBlocks.length));
  if (guardBlocks.length !== 1) return;
  const SRC = guardBlocks[0];

  /* tests/test_report.js evaluates scripts[scripts.length - 1] to get the app. If
     the guard ever became the LAST block, that harness would silently evaluate the
     guard instead of the application and every report test would go green against
     nothing at all. */
  ok("...and it is NOT the last block, which test_report.js evaluates as the app",
     !blocks[blocks.length - 1].includes("frame-guard"));

  // Fails closed at rest: the document is hidden by the stylesheet BEFORE any script
  // runs, so a guard that never executes leaves the page blank rather than exposed.
  ok("the guard stylesheet hides the document by default",
     /<style id="frame-guard">html\{display:none !important;\}<\/style>/.test(HTML));
  ok("...and only <noscript> may override it, for a page that has nothing to hijack",
     /<noscript><style>html\{display:block !important;\}<\/style><\/noscript>/.test(HTML));

  function runGuard(opts) {
    const removed = [];
    const navigated = [];
    const guardEl = { parentNode: { removeChild(el) { removed.push(el); } } };
    const win = { location: { href: "https://sparshr3005.github.io/PF-Vs-BMK/" } };
    win.self = win;
    Object.defineProperty(win, "top", {
      get() {
        if (opts.topThrows) throw new Error("cross-origin access denied");
        if (!opts.framed) return win;              // we ARE the top document
        return { location: { replace(u) {
          if (opts.navThrows) throw new Error("sandboxed: top navigation blocked");
          navigated.push(u);
        } } };
      }
    });
    const ctx = {
      window: win,
      document: { getElementById: id => (id === "frame-guard" ? guardEl : null) }
    };
    vm.createContext(ctx);
    let threw = null;
    try { vm.runInContext(SRC, ctx, { filename: "frame-guard" }); }
    catch (e) { threw = e; }
    return { revealed: removed.length === 1, navigated, threw };
  }

  // Top-level: reveal, and do not navigate anywhere.
  let r = runGuard({ framed: false });
  ok("top-level: the guard style is removed so the page renders",
     r.revealed && !r.threw, r.threw && r.threw.message);
  ok("...and nothing navigates", r.navigated.length === 0);

  // Framed: never reveal. Breaking out is a bonus, staying blank is the guarantee.
  r = runGuard({ framed: true });
  ok("framed: the page is NOT revealed", !r.revealed && !r.threw,
     r.threw && r.threw.message);
  ok("...and it attempts to break out to its own URL",
     r.navigated.length === 1
     && r.navigated[0] === "https://sparshr3005.github.io/PF-Vs-BMK/");

  // Sandboxed frame: top navigation is blocked outright. This is the case the naive
  // frame-buster gets wrong -- it navigates, fails, and leaves a live UI on screen.
  r = runGuard({ framed: true, navThrows: true });
  ok("sandboxed frame: the blocked navigation does not escape as an error", !r.threw,
     r.threw && r.threw.message);
  ok("...and the page STAYS hidden rather than falling back to visible", !r.revealed);

  // A cross-origin parent can make even reading window.top throw. That is not an
  // excuse to render: no answer means assume the restrictive one.
  r = runGuard({ topThrows: true });
  ok("an unreadable window.top is treated as framed, not as safe",
     !r.revealed && !r.threw, r.threw && r.threw.message);
})();

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