#!/usr/bin/env python3
"""Diagnostic v3: drive the real form; capture the page's own TRI request/response."""
import json
from datetime import date
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PAGE = "https://www.niftyindices.com/reports/historical-data"
ENDPOINT_HINT = "getTotalReturnIndexString"

def main():
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

        captured = {}

        def on_response(r):
            if ENDPOINT_HINT in r.url:
                try:
                    body = r.text()
                except Exception as e:
                    body = "READ_ERR: %s" % e
                captured["status"] = r.status
                captured["ct"] = r.headers.get("content-type", "")
                captured["body"] = body[:400]
                print("  >>> CAPTURED %s POST status=%s ct=%s"
                      % (r.url[:80], r.status, r.headers.get("content-type", "")))
        page.on("response", on_response)

        page.goto(PAGE, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass
        page.wait_for_timeout(2500)
        print("TITLE:", page.title())

        # The TRI panel is the third historical-data section ("Historical Total Return Index Data").
        # Its submit button id is submit_totalindexhistorical per the endpoint notes.
        # First, list candidate buttons/inputs so we can see what's actually on the page.
        controls = page.evaluate("""() => {
            const pick = el => ({
                tag: el.tagName, id: el.id || "", name: el.name || "",
                type: el.type || "", val: (el.value || el.textContent || "").trim().slice(0, 30)
            });
            const els = [...document.querySelectorAll("button, input[type=button], input[type=submit], a")];
            return els.filter(e => /total|tri|submit|index|historical/i.test(
                (e.id||"") + (e.name||"") + (e.value||"") + (e.textContent||"")
            )).slice(0, 25).map(pick);
        }""")
        print("CANDIDATE CONTROLS:")
        for c in controls:
            print("   ", json.dumps(c))

        # Try the documented submit id directly.
        clicked = False
        for sel in ["#submit_totalindexhistorical",
                    "input#submit_totalindexhistorical",
                    "button#submit_totalindexhistorical"]:
            el = page.query_selector(sel)
            if el:
                print("Found submit via", sel, "-> clicking (dispatch)")
                page.eval_on_selector(sel, "el => el.click()")
                clicked = True
                break
        if not clicked:
            print("Did NOT find #submit_totalindexhistorical; see candidates above.")

        page.wait_for_timeout(6000)  # let the page's AJAX complete
        print("\nRESULT:", json.dumps(captured, indent=2)[:600] if captured
              else "(no getTotalReturnIndexString response captured)")

        browser.close()

if __name__ == "__main__":
    main()
