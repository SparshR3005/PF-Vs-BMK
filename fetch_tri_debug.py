#!/usr/bin/env python3
"""Diagnostic v2: catch the 302, reveal redirect target + hidden form tokens."""
import json
from datetime import date
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PAGE = "https://www.niftyindices.com/reports/historical-data"
ENDPOINT = "https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString"

JS_FETCH = r"""
async ([url, payload]) => {
  try {
    const r = await fetch(url, {
      method: "POST", credentials: "include", redirect: "manual",
      headers: {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
      },
      body: payload,
    });
    const text = await r.text();
    return { status: r.status, type: r.type, ct: r.headers.get("content-type") || "",
             loc: r.headers.get("location") || "", text: text };
  } catch (e) { return { status: -1, type: "err", ct: "", loc: "", text: String(e) }; }
}
"""

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
        page.wait_for_timeout(3000)

        print("TITLE:", page.title())
        print("COOKIES:", [c["name"] for c in context.cookies()])

        # Any hidden inputs / tokens on the page?
        tokens = page.evaluate("""() => {
            const out = {};
            document.querySelectorAll("input[type=hidden]").forEach(i => {
                out[i.name || i.id || "?"] = (i.value || "").slice(0, 40);
            });
            return out;
        }""")
        print("HIDDEN INPUTS:", json.dumps(tokens, indent=2))

        print("\n--- in-page fetch, redirect=manual ---")
        res = page.evaluate(JS_FETCH, [ENDPOINT, payload])
        print("status:", res.get("status"), "| type:", res.get("type"),
              "| ct:", res.get("ct"))
        print("location:", res.get("loc"))
        print("body[:300]:", repr((res.get("text") or "")[:300]))

        browser.close()

if __name__ == "__main__":
    main()
