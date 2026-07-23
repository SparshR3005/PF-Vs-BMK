#!/usr/bin/env python3
"""
mf_universe.py -- the single definition of "which mfapi schemes can this tool use".

This logic exists in index.html too (loadSchemeList / CATEGORY_CANON), because the
client filters the scheme picker and the nightly job filters the ranking universe.
Two copies of one rule is a bug waiting to happen: add a SEBI category to the
client, forget the Python, and funds silently vanish from rankings with nothing
erroring. tests/test_probe_ranks.py parses index.html and fails on any drift.

Both probe_ranks.py and fetch_ranks.py import from here, so there is exactly one
Python copy. The dependency points at this module, never the other way round --
the probe is disposable, this is not.

Stdlib only, deliberately: nothing here should add to requirements.txt.
"""

import json
import re
import time
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.mfapi.in"
UA = "PF-Vs-BMK/1.0 (+https://github.com/SparshR3005/PF-Vs-BMK)"

NON_EQUITY_NAME_TOKENS = [
    "liquid", "overnight", "gilt", "money market", "ultra short", "low duration",
    "short duration", "medium duration", "long duration", "banking and psu",
    "corporate bond", "credit risk", "debt", "duration", "floater", "dynamic bond", "bond",
    "hybrid", "balanced", "arbitrage", "equity savings", "multi asset", "asset allocation",
    "gold", "silver", "commodit", "fund of fund", "fof", "overseas", "international", "global",
    "index fund", "exchange traded", "etf", "retirement", "children", "pension",
]

# Income-OPTION tokens: these identify a dividend/IDCW *payout plan* of a scheme,
# which must never enter the universe (its NAV is reduced by every payout, so an
# XIRR against a TRI is meaningless).
#
# "dividend" is deliberately ABSENT from this list. It used to be here, and it
# silently deleted an entire SEBI equity category: "Equity Scheme - Dividend
# Yield Fund" is a growth-option EQUITY fund whose NAME contains "dividend".
# CATEGORY_CANON maps it to DIV_YIELD and treats it as rankable, but no
# DIV_YIELD file was ever published and those funds never appeared in the
# picker -- with nothing erroring, because the fund was dropped at ingest.
#
# The distinction we actually want is "dividend" NOT followed by "yield", so the
# payout plan is still excluded while the fund category survives. Kept as a
# predicate (not a bare token) because a substring test cannot express it.
INCOME_TOKENS = ["idcw", "payout", "reinvest", "bonus"]

# "dividend" only counts as an income-option marker when it is NOT part of the
# category phrase "dividend yield". Word-bounded so "dividends" in a longer
# marketing string can't slip past.
DIVIDEND_PLAN_RE = re.compile(r"\bdividend\b(?!\s+yield)")


def name_looks_income_option(n):
    """True when the scheme NAME marks an income/payout plan rather than growth.

    Mirrors index.html's loadSchemeList() exactly. Any change here must be made
    there too -- tests/test_probe_ranks.py fails on drift.
    """
    if any(t in n for t in INCOME_TOKENS):
        return True
    return bool(DIVIDEND_PLAN_RE.search(n))

CATEGORY_CANON = {
    "equity scheme - large cap fund":         "LARGE_CAP",
    "equity scheme - large & mid cap fund":   "LARGE_MID",
    "equity scheme - large and mid cap fund": "LARGE_MID",
    "equity scheme - mid cap fund":           "MID_CAP",
    "equity scheme - small cap fund":         "SMALL_CAP",
    "equity scheme - multi cap fund":         "MULTI_CAP",
    "equity scheme - flexi cap fund":         "FLEXI_CAP",
    "equity scheme - focused fund":           "FOCUSED",
    "equity scheme - value fund":             "VALUE",
    "equity scheme - contra fund":            "CONTRA",
    "equity scheme - dividend yield fund":    "DIV_YIELD",
    "equity scheme - sectoral/ thematic":     "SECTORAL",
    "equity scheme - sectoral/thematic":      "SECTORAL",
    # MFAPI also serves a plural variant on live schemes; without it those funds
    # are rejected by the app entirely. Must mirror index.html exactly.
    "equity schemes - thematic fund":         "SECTORAL",
    "equity scheme - thematic fund":          "SECTORAL",
    "equity scheme - elss":                   "ELSS",
    "elss":                                   "ELSS",
}

UNRANKABLE_KEYS = {"SECTORAL"}

def norm_name(s):
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def norm_category(c):
    return re.sub(r"\s+", " ", str(c or "").lower()).strip()


def category_key(category):
    """Canonical SEBI key, or None when absent/junk/non-equity. Fails closed."""
    return CATEGORY_CANON.get(norm_category(category))


def name_looks_non_equity(n):
    return any(t in n for t in NON_EQUITY_NAME_TOKENS)


def classify_plan(n):
    """Direct when the name says so; otherwise Regular (mirrors the client's
    else-branch, which treats a name mentioning neither as Regular)."""
    if "direct" in n:
        return "Direct"
    return "Regular"


def get_json(url, timeout, attempts=3):
    """GET with retry/backoff. Returns (payload, elapsed_s, nbytes, status)."""
    last = None
    for i in range(attempts):
        started = time.monotonic()
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urlopen(req, timeout=timeout) as res:
                raw = res.read()
                elapsed = time.monotonic() - started
                # mfapi has been observed serving JSON under a text/html content
                # type, so never gate parsing on the content-type header.
                return json.loads(raw.decode("utf-8", "replace")), elapsed, len(raw), res.status
        except HTTPError as e:
            last = e
            # 429/5xx are worth backing off on; 404 is final.
            if e.code == 404:
                return None, time.monotonic() - started, 0, 404
            if e.code == 429:
                time.sleep(2.0 * (i + 1))
                continue
        except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
            last = e
        time.sleep(0.6 * (i + 1))
    return None, 0.0, 0, getattr(last, "code", 0)


def unwrap_list(raw):
    """Tolerate a shape change exactly as the client does: today /mf returns a
    bare array, but guard against a future paginated wrapper so the whole list
    doesn't silently vanish."""
    if isinstance(raw, list):
        return raw, True
    if isinstance(raw, dict):
        for k in ("data", "schemes"):
            if isinstance(raw.get(k), list):
                paginated = (
                    raw.get("nextPage") is not None
                    or raw.get("next") is not None
                    or raw.get("hasMore") is True
                    or (raw.get("page") is not None
                        and raw.get("totalPages") is not None
                        and raw["page"] < raw["totalPages"])
                )
                return raw[k], not paginated
    return None, False


def parse_dmy(v):
    """mfapi serves dates as DD-MM-YYYY (confirmed against index.html's parser)."""
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", str(v or ""))
    if not m:
        return None
    d, mo, y = (int(x) for x in m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None