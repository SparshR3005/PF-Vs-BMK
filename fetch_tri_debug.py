#!/usr/bin/env python3
"""Diagnostic v4: the page uses /BackPage/ (routed, no .aspx). Try URL variants."""
import json
from datetime import date
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PAGE = "https://www.niftyindices.com/reports/historical-data"

# Candidate URLs for the TRI method. The page's own calls use /BackPage/<method>
# (capital P, no .aspx); the old doc used /Backpage.aspx/<method>.
URL_VARIANTS = [
    "https://www.niftyindices.com/BackPage/getTotalReturnIndexString",
    "https://www.niftyindices.com/Backpage/getTotalReturnIndexString",
    "https://www.niftyindices.com/BackPage.aspx/getTotalReturnIndexString",
    "https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString",
]

JS_FETCH = r"""
async ([url, payload]) => {
  try {
    const r = await fetch(url, {
      method: "POST", credentials: "include", redirect: "follow",
      headers: {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
      },
      body: payload,
    });
    const text = await r.text();
    return { status: r.status, redirected: r.redirected, finalUrl: r.url,
             ct: r.headers.get("content-type") || "", text: text };
  } catch (e) { return { status: -1, redirected: false, finalUrl: "", ct: "", text: String(e) }; }
}
"""

def summarize(res):
    text = res.get("text", "") or ""
    t = text.lstrip("\ufeff \r\n\t")
    parsed = None
    if t.startswith("{"):
        try:
            d = json.loads(t).get("d")
            if isinstance(d, str):
                rows = json.loads(d)
                parsed = len(rows) if isinstance(rows, list) else None
        except Exception:
            parsed = None
    return parsed, text[:160]

def main():
    end = date.today().strftime("%d-%b-%Y")
    cinfo = "{'name':'NIFTY 50','startDate':'01-Jan-2026','endDate':'%s','indexName':'NIFTY 50'}" % end
    payload = json.dumps({"cinfo": cinfo})

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            locale="en-IN", timezone_id="Asia/Kolkata",
            viewport={"width": 1366, "height": 900},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()
        page.goto(PAGE, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass
        for x, y in ((120, 160), (400, 300), (650, 480)):
            page.mouse.move(x, y); page.wait_for_timeout(150)
        page.wait_for_timeout(2500)
        print("TITLE:", page.title(), "| COOKIES:", [c["name"] for c in context.cookies()])
        print()

        for url in URL_VARIANTS:
            res = page.evaluate(JS_FETCH, [url, payload])
            parsed, head = summarize(res)
            tag = "  <-- JSON ROWS=%d" % parsed if parsed is not None else ""
            print("URL:", url.replace("https://www.niftyindices.com", ""))
            print("  status=%s redirected=%s ct=%s%s"
                  % (res.get("status"), res.get("redirected"), res.get("ct", "")[:30], tag))
            print("  finalUrl:", (res.get("finalUrl") or "").replace("https://www.niftyindices.com", ""))
            print("  body[:160]:", repr(head))
            print()
            page.wait_for_timeout(1200)

        browser.close()

if __name__ == "__main__":
    main()
