#!/usr/bin/env python3
"""One-shot diagnostic: what does Akamai actually give us from a GH runner?"""
import json
from datetime import date
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PAGE = "https://www.niftyindices.com/reports/historical-data"
ENDPOINT = "https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString"

JS_FETCH = r"""
async ([url, payload]) => {
  try {
    const r = await fetch(url, {
      method: "POST", credentials: "include",
      headers: {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
      },
      body: payload,
    });
    const text = await r.text();
    return { status: r.status, ct: r.headers.get("content-type") || "", text };
  } catch (e) { return { status: -1, ct: "", text: "FETCH_ERROR: " + String(e) }; }
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

        def on_resp(r):
            try:
                if r.status >= 400 or r.request.method == "POST":
                    print("  RESP %s %s %s" % (r.status, r.request.method, r.url[:110]))
            except Exception:
                pass
        page.on("response", on_resp)

        resp = page.goto(PAGE, wait_until="domcontentloaded", timeout=45000)
        print("NAV status:", resp.status if resp else None, "| final url:", page.url)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            print("(networkidle timed out)")

        for x, y in ((120, 160), (400, 300), (650, 480), (300, 620)):
            page.mouse.move(x, y); page.wait_for_timeout(150)
        page.mouse.wheel(0, 800)
        page.wait_for_timeout(4000)

        print("TITLE:", page.title())
        print("COOKIES present:")
        cookies = context.cookies()
        if not cookies:
            print("   (none)")
        for c in cookies:
            print("   %-14s domain=%s httpOnly=%s" % (c["name"], c.get("domain"), c.get("httpOnly")))

        print("Attempting in-page fetch regardless of cookies...")
        res = page.evaluate(JS_FETCH, [ENDPOINT, payload])
        print("FETCH status:", res.get("status"), "| ct:", res.get("ct"))
        print("FETCH body[:300]:", repr((res.get("text") or "")[:300]))

        browser.close()

if __name__ == "__main__":
    main()
