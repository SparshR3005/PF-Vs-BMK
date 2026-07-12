import json
import os
import time
import requests

# 2010-01-01 to today
START = "01-Jan-2010"
END = time.strftime("%d-%b-%Y")

INDICES = {
    "NIFTY 100":                  "nifty100_tri.json",
    "NIFTY LARGEMIDCAP 250":      "largemidcap250_tri.json",
    "NIFTY MIDCAP 150":           "midcap150_tri.json",
    "NIFTY SMALLCAP 250":         "smallcap250_tri.json",
    "NIFTY 500":                  "nifty500_tri.json",
    "NIFTY500 MULTICAP 50:25:25": "multicap_502525_tri.json",
    "NIFTY AUTO":                 "auto_tri.json",
    "NIFTY BANK":                 "bank_tri.json",
    "NIFTY FINANCIAL SERVICES":   "finserv_tri.json",
    "NIFTY FMCG":                 "fmcg_tri.json",
    "NIFTY IT":                   "it_tri.json",
    "NIFTY PHARMA":               "pharma_tri.json",
    "NIFTY INDIA CONSUMPTION":    "consumption_tri.json",
    "NIFTY INFRASTRUCTURE":       "infra_tri.json",
    "NIFTY ENERGY":               "energy_tri.json",
}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

POST_HEADERS = {
    "Connection": "keep-alive",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json; charset=UTF-8",
    "Origin": "https://niftyindices.com",
    "Referer": "https://niftyindices.com/reports/historical-data",
    "Accept-Language": "en-US,en;q=0.9",
}

TRI_URL = "https://niftyindices.com/Backpage.aspx/getTotalReturnIndexString"


def make_session():
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    # Visit pages to collect the cookies the endpoint checks for.
    for url in [
        "https://niftyindices.com",
        "https://niftyindices.com/reports/historical-data",
    ]:
        try:
            s.get(url, timeout=30)
        except Exception as e:
            print(f"  warm-up warning for {url}: {e}")
        time.sleep(1)
    return s


def fetch_one(session, index_name):
    body = {
        "cinfo": "{'name':'" + index_name + "','startDate':'" + START +
                 "','endDate':'" + END + "','indexName':'" + index_name + "'}"
    }
    r = session.post(TRI_URL, headers=POST_HEADERS, json=body, timeout=60)
    r.raise_for_status()
    text = r.text
    # If it's not JSON, surface what came back so we can diagnose.
    try:
        outer = r.json()
    except ValueError:
        snippet = text[:200].replace("\n", " ")
        raise ValueError(f"non-JSON response (first 200 chars): {snippet!r}")
    rows = json.loads(outer["d"])
    return rows


def normalise(rows):
    out = []
    for row in rows:
        date_val = None
        tri_val = None
        for k, v in row.items():
            kl = k.lower().replace(" ", "")
            if kl in ("date", "historicaldate", "indexdate"):
                date_val = v
            if "totalreturn" in kl or kl in ("closingindexvalue", "close"):
                tri_val = v
        if date_val and tri_val:
            try:
                out.append({"date": date_val, "tri": float(str(tri_val).replace(",", ""))})
            except ValueError:
                pass
    return out


def main():
    os.makedirs("data", exist_ok=True)
    session = make_session()

    ok, fail = 0, 0
    for index_name, filename in INDICES.items():
        try:
            rows = fetch_one(session, index_name)
            clean = normalise(rows)
            if not clean:
                print(f"FAIL  {index_name}: 0 usable rows (parsed but empty)")
                fail += 1
                continue
            with open(os.path.join("data", filename), "w") as f:
                json.dump({"index": index_name, "data": clean}, f)
            print(f"OK    {index_name}: {len(clean)} rows -> data/{filename}")
            ok += 1
        except Exception as e:
            print(f"FAIL  {index_name}: {e}")
            fail += 1
        time.sleep(2)

    print(f"\nDone. {ok} succeeded, {fail} failed.")
    if ok == 0:
        raise SystemExit("No indices fetched — see errors above.")


if __name__ == "__main__":
    main()
