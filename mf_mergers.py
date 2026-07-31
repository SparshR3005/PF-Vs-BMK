"""Scheme-continuation chains: one canonical definition, shared with index.html.

WHY THIS EXISTS
---------------
When an AMC merges or rebrands a scheme, AMFI sometimes keeps the surviving
scheme's code and sometimes allots a brand-new one. When it allots a new one, the
new code's NAV history begins on the merger date and everything before it lives
under the retired code. The utility then cannot place a single SIP instalment for
any period before the merger, and the holding is dropped at import with
"No valid SIP instalment could be placed".

Measured on the published grids, the L&T -> HSBC transition took the new-code
route across every scheme:

    151034/151036  HSBC Midcap Fund          first NAV 2022-11-28
    151076/151078  HSBC ELSS Tax saver Fund  first NAV 2022-11-28
    151110/151113  HSBC Value Fund           first NAV 2022-11-28
    151130/151133  HSBC Small Cap Fund       first NAV 2022-11-28

while the pre-existing HSBC schemes (101594, 102252, 104707, 146771, 148409 ...)
kept their codes and their full history. A uniform 2022-11-28 start across four
unrelated scheme pairs is the signature of new codes, not of four simultaneous
fund launches.

WHY A PLAIN CONCATENATION IS CORRECT HERE
-----------------------------------------
HSBC Midcap's first NAV is Rs 210.9607 (Regular) / Rs 231.7977 (Direct). A newly
launched scheme starts at its Rs 10 NFO price; opening in the 200s means the
series is the RENAMED CONTINUATION of the surviving scheme, carrying NAV straight
through. HSBC's own notice says renamed schemes had a name change only, with no
change in NAV or investment value -- it was the schemes merged INTO a survivor
that had NAV recomputed. L&T Mid Cap Fund was the survivor.

So units carry 1:1 and the two series are concatenated with NO ratio adjustment.

THAT IS AN ASSERTION ABOUT DATA, SO IT IS CHECKED, NOT TRUSTED
--------------------------------------------------------------
splice_problem() refuses the join unless the NAV either side of the splice is
continuous. If a chain is ever wrong -- a transferor mistaken for a survivor, a
mistyped code, an AMC that really did restate NAV -- the ratio will not be near
1.0 and the splice is REFUSED rather than silently welding a step-change into the
middle of someone's return history. A refused splice degrades to exactly today's
behaviour; a bad splice would invent performance that never happened.
"""

from datetime import date, datetime

# new (surviving) code -> the retired code its history continues from.
#
# `splice` is the first date the NEW code is authoritative. Rows on/after it come
# from the new code, rows before it from the old, so a day present in both can
# never be counted twice.
#
# Codes verified against mfapi's scheme list; note the retired schemes are spelt
# "L&T Mid Cap Fund" (with a space), which is why searching "L&T Midcap" finds
# only "L&T Large and Midcap Fund" -- a DIFFERENT scheme, deliberately not chained.
CHAIN = {
    "151034": {"from": "112496", "splice": "2022-11-28",
               "former": "L&T Mid Cap Fund - Regular Plan - Growth"},
    "151036": {"from": "119807", "splice": "2022-11-28",
               "former": "L&T Mid Cap Fund - Direct Plan - Growth"},
}

# A rename carries NAV through untouched, so the ratio across the join is 1.0 in
# principle. The tolerance absorbs the one or two trading days that separate the
# last old NAV from the first new one -- real market movement, not restatement.
# 3% is roughly a very bad single session for a midcap fund and far below any
# plausible merger ratio, which is typically tens of percent.
MAX_SPLICE_RATIO_DRIFT = 0.03

# The join must not open a hole larger than the SIP placement window (7 days), or
# an instalment scheduled in the gap silently vanishes. 10 allows a merger over a
# long weekend or a holiday cluster while still catching a real hole.
MAX_SPLICE_GAP_DAYS = 10


def _as_date(value):
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def splice_problem(old_points, new_points, splice, ratio_tol=MAX_SPLICE_RATIO_DRIFT,
                   gap_limit=MAX_SPLICE_GAP_DAYS):
    """Return an error string if these two series must not be joined, else ''.

    Both arguments are [(date, nav)] sorted ascending. Mirrors the shape and the
    spirit of fetch_tri.py's continuity_problem(): refuse the write, keep what is
    already correct, and say exactly why.
    """
    if not old_points:
        return "no rows from the retired code"
    if not new_points:
        return "no rows from the surviving code"

    splice_d = _as_date(splice)
    tail = [p for p in old_points if _as_date(p[0]) < splice_d]
    head = [p for p in new_points if _as_date(p[0]) >= splice_d]
    if not tail:
        return f"retired code has no rows before {splice_d}"
    if not head:
        return f"surviving code has no rows on/after {splice_d}"

    last_old_date, last_old_nav = _as_date(tail[-1][0]), float(tail[-1][1])
    first_new_date, first_new_nav = _as_date(head[0][0]), float(head[0][1])

    if last_old_nav <= 0 or first_new_nav <= 0:
        return "non-positive NAV at the join"

    gap = (first_new_date - last_old_date).days
    if gap > gap_limit:
        return (f"{gap}-day hole between {last_old_date} and {first_new_date} "
                f"(limit {gap_limit}) — an instalment scheduled in the gap would vanish")

    ratio = first_new_nav / last_old_nav
    if abs(ratio - 1.0) > ratio_tol:
        return (f"NAV jumps {(ratio - 1.0) * 100:+.2f}% across the join "
                f"({last_old_nav:.4f} on {last_old_date} -> {first_new_nav:.4f} on "
                f"{first_new_date}). A rename carries NAV through unchanged, so this "
                f"is either a ratio merger or the wrong retired code — refusing to "
                f"splice rather than invent a step-change in the return history")
    return ""


def splice_series(old_points, new_points, splice, **kw):
    """Return (points, problem). On any problem the NEW series is returned intact,
    so a refused splice degrades to today's behaviour instead of losing data."""
    problem = splice_problem(old_points, new_points, splice, **kw)
    if problem:
        return list(new_points), problem
    splice_d = _as_date(splice)
    joined = [p for p in old_points if _as_date(p[0]) < splice_d]
    joined += [p for p in new_points if _as_date(p[0]) >= splice_d]
    joined.sort(key=lambda p: _as_date(p[0]))
    return joined, ""


def chained_from(code):
    """The retired code whose history `code` continues, or None."""
    entry = CHAIN.get(str(code))
    return entry["from"] if entry else None