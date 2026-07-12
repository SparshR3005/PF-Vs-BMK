import json
import os
import time
import httpx

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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

POST_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json; charset=UTF-8",
    "Origin": "https://niftyindices.com",
    "Referer": "https://niftyindices.com/reports/historical-data",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

TRI_URL = "https://niftyindices.com/Backpage.aspx/getTotalReturnIndexString"


def make_client():
    # HTTP/2 via httpx is the technique that gets past NSE's datacenter block.
    client = httpx.Client(http2=True, headers=BROWSER_HEADERS, timeout=60.0, follow_redirects=True)
    for url in [
        "https://www.niftyindices.com",
        "https://niftyindices.com/reports/historical-data",
    ]:
        try:
            r = client.get(url)
            print(f"  warm-up {url} -> HTTP {r.status_code} (http2={r.http_version}), cookies: {list(client.cookies.keys())}")
        except Exception as e:
            print(f"  warm-up warning {url}: {e}")
        time.sleep(1.5)
    return client


def fetch_one(client, index_name, attempts=4):
    body = {
        "cinfo": "{'name':'" + index_name + "','startDate':'" + START +
                 "','endDate':'" + END + "','indexName':'" + index_name + "'}"
    }
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            r = client.post(TRI_URL, headers=POST_HEADERS, json=body)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                time.sleep(3 * attempt)
                continue
            try:
                outer = r.json()
            except Exception:
                snippet = r.text[:200].replace("\n", " ").replace("\r", " ")
                last_err = f"non-JSON (try {attempt}): {snippet!r}"
                time.sleep(3 * attempt)
                continue
            rows = json.loads(outer["d"])
            return rows
        except Exception as e:
            last_err = str(e)
            time.sleep(3 * attempt)
    raise ValueError(last_err)


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
    client = make_client()

    ok, fail = 0, 0
    for index_name, filename in INDICES.items():
        try:
            rows = fetch_one(client, index_name)
            clean = normalise(rows)
            if not clean:
                print(f"FAIL  {index_name}: parsed but 0 usable rows")
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

    client.close()
    print(f"\nDone. {ok} succeeded, {fail} failed.")
    if ok == 0:
        raise SystemExit("No indices fetched — see errors above.")


if __name__ == "__main__":
    main()
