import sqlite3
import polars as pl
import time
import json
import requests
import sys
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

def run(db_path: str, meta_dir: str, out_dir: str):
    t0 = time.time()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    report = {"module": "extraction_engine", "validations": {}}

    with open(Path(meta_dir) / "01_system_metadata.json", "r") as f:
        meta = json.load(f)

    # --- 1. EXTRACT DB (Bulletproof Mode for Dirty SQLite Data) ---
    print("  -> Extracting Database Tables...")
    conn = sqlite3.connect(f"file:{Path(db_path).absolute()}?mode=ro", uri=True)
    
    for table, expected in meta["sources"].get("sqlite_db", {}).items():
        cursor = conn.cursor()
        cursor.execute(f'SELECT * FROM "{table}"')
        columns = [col[0] for col in cursor.description]
        data = cursor.fetchall()
        
        df = pl.DataFrame(data, schema=columns, infer_schema_length=None, strict=False, orient="row")
        df.write_ndjson(out_path / f"db_{table}.jsonl")
        
        report["validations"][table] = {"expected": expected["expected_rows"], "extracted": df.height}
    conn.close()

    # --- 2. EXTRACT WEB (Fast Parent-Node Scraping) ---
    print("  -> Extracting BazaarPulse Site...")
    base_url = "http://localhost:8080"
    
    for endp in ["/robots.txt", "/methodology.html"]:
        try:
            res = requests.get(base_url + endp, timeout=5)
            with open(out_path / f"web_{endp.strip('/').replace('.','_')}.txt", "w") as f:
                f.write(res.text)
        except Exception: pass

    cities = ["mumbai", "delhi", "bengaluru", "chennai"]
    scraped_products = []
    
    for city in cities:
        page = 1
        while True:
            path = f"/city/{city}/page/{page}.html" if city in ["mumbai", "delhi"] else (f"/city/{city}/index.html" if page == 1 else f"/city/{city}/index_p{page}.html")
            time.sleep(1) # Strict robots.txt obedience
            try:
                resp = requests.get(urljoin(base_url, path), timeout=5)
                if resp.status_code != 200: break
                
                soup = BeautifulSoup(resp.text, 'html.parser')
                found = 0
                for a in soup.find_all('a', href=True):
                    if '/product/' in a['href']:
                        parent_text = a.parent.get_text(separator=' | ', strip=True)
                        scraped_products.append({
                            "raw_text_block": parent_text,
                            "source_url": urljoin(base_url, a['href']),
                            "city": city
                        })
                        found += 1
                
                if found == 0: break
                page += 1
            except Exception: break

    pl.DataFrame(scraped_products).write_ndjson(out_path / "web_competitor_prices.jsonl")
    report["validations"]["web"] = {"extracted": len(scraped_products)}
    print(f"    Captured {len(scraped_products)} competitor listings safely.")

    # --- 3. EXTRACT API (Resilient Threading) ---
    print("  -> Extracting Partner API...")
    session = requests.Session()
    session.headers.update({"X-API-Key": "kp_live_7f3a9c21"})
    
    def fetch_api(url, params=None):
        attempt = 0
        while attempt < 5:
            try:
                r = session.get(url, params=params, timeout=10)
                if r.status_code == 200: return r.json()
                elif r.status_code == 429: time.sleep(int(r.headers.get("Retry-After", 1)))
                elif r.status_code == 503: time.sleep(2 ** attempt); attempt += 1
                else: break
            except Exception: attempt += 1
        return None

    carriers = fetch_api(f"{base_url.replace('8080','8088')}/v1/carriers")
    if carriers: pl.DataFrame(carriers.get("data", [])).write_ndjson(out_path / "api_carriers.jsonl")

    surcharges = []
    for m in range(1, 7):
        sur = fetch_api(f"{base_url.replace('8080','8088')}/v1/fuel_surcharge", {"month": f"2026-{m:02d}"})
        if sur: surcharges.append(sur)
    if surcharges:
        pl.DataFrame(surcharges).write_ndjson(out_path / "api_fuel_surcharge.jsonl")

    invoices, invoice_ids, cursor = [], [], None
    while True:
        data = fetch_api(f"{base_url.replace('8080','8088')}/v1/freight_invoices", {"cursor": cursor} if cursor else {})
        if not data: break
        batch = data.get("data", [])
        invoices.extend(batch)
        invoice_ids.extend([i["invoice_id"] for i in batch if "invoice_id" in i])
        cursor = data.get("next_cursor")
        if not cursor: break
        sys.stdout.write(f"\r    Fetched {len(invoices)} invoices...")
        sys.stdout.flush()
        
    pl.DataFrame(invoices).write_ndjson(out_path / "api_freight_invoices.jsonl")
    
    events = []
    sample_ids = invoice_ids[:1000]
    print(f"\n    Fetching shipment events for {len(sample_ids)} records...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_api, f"{base_url.replace('8080','8088')}/v1/shipment_events", {"invoice_id": i}) for i in sample_ids]
        for f in as_completed(futures):
            res = f.result()
            if res: events.extend(res if isinstance(res, list) else [res])
            
    pl.DataFrame(events).write_ndjson(out_path / "api_shipment_events.jsonl")
    report["validations"]["api"] = {"invoices": len(invoices), "events": len(events)}

    with open(out_path / "02_extract_report.json", "w") as f:
        json.dump(report, f, indent=4)
    print(f"  -> Extraction complete in {round(time.time()-t0, 2)}s")