# Audit items we are deliberately NOT fixing — and why

These come from the `Atlast_fixed_v3` external audit. Each was reviewed against the
actual code and the real deployment target (a single-file app on **GitHub Pages**,
edited through the GitHub web editor, used privately). They are being left as-is on
purpose. This file is the record of that decision so they don't get re-litigated
every audit cycle.

---

## #14 — CSP `frame-ancestors` is ineffective (and can't be fixed here)

**Finding:** The Content-Security-Policy is delivered via a `<meta>` tag, and
`frame-ancestors 'none'` is ignored in meta-delivered policies. The policy also
relies on `'unsafe-inline'`.

**Why we won't fix it:**
- The audit's own remedy — "deliver CSP as an HTTP response header" — **is not
  possible on GitHub Pages.** GitHub Pages serves static files and does not let
  you set custom response headers. So header-based CSP and working `frame-ancestors`
  are simply unavailable on this host.
- Removing `'unsafe-inline'` would require splitting all JS/CSS into separate
  same-origin files with nonces/hashes. That directly breaks the deliberate
  single-file, paste-the-whole-file workflow this project is built around.
- The realistic threat (clickjacking) is negligible here: the app is a personal,
  read-only calculator whose only "state" is the user's own `localStorage`. There
  is no authenticated action for a framing attacker to trick a click into.

**If the host ever changes** (e.g. moved behind Cloudflare/Netlify, which *can* set
headers), revisit: send a real `Content-Security-Policy` header including
`frame-ancestors 'none'`, then tighten `script-src`/`style-src`.

---

## #12 — Portfolio XIRR mixes terminal cash flows dated on different days

**Finding:** Each holding's terminal (current-value) inflow is dated at that
holding's own latest NAV date. Across holdings this can produce +/−/+ sign
patterns and, in theory, multiple XIRR roots.

**Why we won't fix it (for now):**
- The per-holding value-date behaviour is **intentional**. It was the fix for an
  earlier, worse bug: forcing every holding to a single common as-of date silently
  dropped the freshest installments and made the portfolio total stop equalling the
  sum of the visible rows. The current design guarantees `portfolio == Σ rows`,
  which is the property a user actually checks.
- The multiple-root risk is theoretical for SIP-shaped flows: the cash-flow stream
  is dominated by a long run of outflows followed by terminal inflows, so a sign
  change severe enough to create a second economically-meaningful root is not a
  case that arises with monthly SIPs into long-only equity funds. The solver also
  brackets and bisects from a fixed low guess, so it returns a stable root.
- The "correct" alternative (one common as-of date, carry last NAV forward, date
  all terminal flows on that date) is a legitimate methodology, but it trades the
  "totals equal the rows" property for textbook purity — the wrong trade for this
  tool's audience.

Documented as a known methodology choice, not a bug. Would only revisit if we add
proper time-weighted / Jensen's-alpha reporting.

---

## #13 — Date-only arithmetic depends on the browser's local timezone

**Finding:** Dates are built at local midnight and day-gaps are computed by
dividing milliseconds by 86,400,000, which is off by ±1 hour across a DST change.

**Why we won't fix it:**
- **India has no DST.** For the actual users (IST), the millisecond-subtraction
  approach never crosses a DST boundary, so the computed day counts are exact.
- The only way to hit the bug is to run the page in a DST-observing timezone *and*
  straddle the transition weekend *and* have a NAV/schedule date land exactly
  there — a vanishing edge case for an India-mutual-fund SIP tool.
- The audit itself grades the target-user impact as "limited."

The clean fix (represent date-only values as UTC epoch-day ordinals) is a nice
future hygiene item, but it touches every date path and earns nothing for the
intended users, so it is not worth the regression risk right now.

---

## #1 — Benchmark data (`data/tri/`) is absent from the delivered package

**Finding:** The ZIP contains no `data/tri/` files, and the front end degrades a
missing benchmark to "unavailable" rather than a hard error.

**Why we won't "fix" it as stated:**
- `data/tri/` is populated **by design** by the nightly GitHub Action
  (`fetch_tri.py`), which commits the JSON back to the repo. Shipping a static TRI
  snapshot inside the code ZIP would immediately be stale and would fight the
  actual data pipeline.
- The audit's suggested remedy — a **blocking** benchmark-data error at boot — is
  too aggressive: it would brick the whole app on the first deploy (before the
  Action has run once) and any time niftyindices has an off day, even though fund
  NAV, XIRR and everything else still work. A non-blocking banner is the right UX,
  and the app already has one (`checkTriFreshness()` warns when committed TRI data
  goes stale).
- The real, load-bearing concern hiding inside #1 — "will the Action actually keep
  `data/tri/` fresh?" — is addressed elsewhere: the `requirements.txt` + workflow
  fix (audit #3) removes the `setup-python` cache failure that could stop the job
  before it ran, and the continuity gate (audit #4) stops a bad fetch from
  corrupting good committed data.

**What we accept instead of the audit's fix:** keep the pipeline-populated model,
keep the non-blocking staleness banner, and treat niftyindices reachability from
GitHub's datacenter IPs as the known residual risk it has always been. If we ever
want a cold-start guarantee, the right move is to commit one validated TRI snapshot
directly to the repo (not the code ZIP) so a fresh clone has day-0 data — a repo
housekeeping step, not an app change.

---

## Partial item — #5 residual (spreadsheet parser)

The import path was hardened in this release (type/size/row-column limits, a
prototype-pollution tripwire, legacy `.xls` dropped). **Not done, deliberately
deferred:** swapping the *import* parser to official SheetJS CE ≥ 0.20.2.

Reason: `xlsx-js-style` (needed for the *styled export*) bundles an older SheetJS
core. The official current-CE build is no longer distributed on the CDNs this
project already trusts (jsDelivr) — it lives on `cdn.sheetjs.com` — so adopting it
means adding a new script origin to the CSP **and** pinning a fresh SRI hash, which
must be generated and verified in the real environment (it can't be done blind).
Until that's set up, import stays on the hardened existing parser. The mitigations
above materially shrink the exposure in the meantime, and for a workflow where you
import your *own* sheets the practical risk is low.
