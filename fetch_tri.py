import json
import os
import time
import requests

# 2010-01-01 to today
START = "01-Jan-2010"
END = time.strftime("%d-%b-%Y")

# index -> output filename. The left string is what the NSE TRI endpoint expects.
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

HEADERS = {
    "Connection": "keep-alive",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.77 Safari/537.36",
    "Content-Type": "application/json; charset=UTF-8",
    "Origin": "https://niftyindices.com",
    "Referer": "https://niftyindices.com/reports/historical-data",
    "Accept-Language": "en-US,en;q=0.9",
}

TRI_URL = "https://niftyindices.com/Backpage.aspx/getTotalReturnIndexString"


def fetch_one(session, index_name):
    body = {
        "cinfo": "{'name':'" + index_name + "','startDate':'" + START +
                 "','endDate':'" + END + "','indexName':'" + index_name + "'}"
    }
    r = session.post(TRI_URL, headers=HEADERS, json=body, timeout=60)
    r.raise_for_status()
    outer = r.json()
    rows = json.loads(outer["d"])
    return rows


def normalise(rows):
    # rows look like {"Date":"01 Jan 2010","TotalReturnsIndex":"..."} (key names vary)
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
    session = requests.Session()
    # warm up a session cookie
    try:
        session.get("https://niftyindices.com", headers=HEADERS, timeout=30)
    except Exception:
        pass

    ok, fail = 0, 0
    for index_name, filename in INDICES.items():
        try:
            rows = fetch_one(session, index_name)
            clean = normalise(rows)
            if not clean:
                print(f"FAIL  {index_name}: 0 usable rows (name may be wrong)")
                fail += 1
                continue
            with open(os.path.join("data", filename), "w") as f:
                json.dump({"index": index_name, "data": clean}, f)
            print(f"OK    {index_name}: {len(clean)} rows -> data/{filename}")
            ok += 1
        except Exception as e:
            print(f"FAIL  {index_name}: {e}")
            fail += 1
        time.sleep(2)  # be gentle with NSE

    print(f"\nDone. {ok} succeeded, {fail} failed.")
    # don't hard-fail the whole run if a few indices fail
    if ok == 0:
        raise SystemExit("No indices fetched — something is wrong.")


if __name__ == "__main__":
    main()
