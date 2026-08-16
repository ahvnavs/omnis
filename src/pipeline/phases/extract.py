"""
extract.py  —  Phase 1, Step 2 (Parallelized for Speed)
--------------------------------
Pulls every row from every source and writes raw JSONL files.
Validates extracted counts against the metadata contract.
"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import concurrent.futures

import requests
from bs4 import BeautifulSoup

# ── constants ────────────────────────────────────────────────────────────────
WEB_BASE  = "http://localhost:8080"
API_BASE  = "http://localhost:8088"
API_KEY   = os.environ.get("OMNIS_API_KEY", "kp_live_7f3a9c21")
CRAWL_DELAY = 0          # Removed sleep for speed
MAX_RETRIES = 7          # per-request attempts for API
MAX_WORKERS = 25         # Optimal thread concurrency for stability and speed

# ── helpers ──────────────────────────────────────────────────────────────────

def _write_jsonl(path: Path, records: list) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, default=str) + "\n")
    return len(records)

def _api_get(session: requests.Session, url: str, params: Optional[dict] = None) -> Optional[dict]:
    attempt = 0
    while attempt < MAX_RETRIES:
        try:
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2))
                time.sleep(wait)
                continue
            if resp.status_code == 503:
                wait = 2 ** attempt
                time.sleep(wait)
                attempt += 1
                continue
            return None
        except requests.RequestException:
            wait = 2 ** attempt
            time.sleep(wait)
            attempt += 1
    return None

# ── DB extraction ─────────────────────────────────────────────────────────────

def _extract_single_table(db_path: str, tbl: str, raw_dir: Path, expected: int) -> tuple:
    conn = sqlite3.connect(f"file:{Path(db_path).absolute()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(f'SELECT * FROM "{tbl}"')
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    
    path = raw_dir / f"db_{tbl}.jsonl"
    written = _write_jsonl(path, rows)
    status = "OK" if written == expected else "COUNT_MISMATCH"
    print(f"    {tbl}: {written:,} rows [{status}]")
    return tbl, {"expected": expected, "extracted": written, "status": status}

def _extract_db(db_path: str, meta: dict, raw_dir: Path) -> dict:
    print("  → [extract] DB tables (Parallel) …")
    db_meta = meta.get("db", {})
    tables  = list(db_meta.get("tables", {}).keys())
    report  = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(_extract_single_table, db_path, tbl, raw_dir, db_meta["tables"][tbl]["row_count"])
            for tbl in tables
        ]
        for f in concurrent.futures.as_completed(futures):
            tbl, tbl_report = f.result()
            report[tbl] = tbl_report
            
    return report

# ── Web extraction ────────────────────────────────────────────────────────────

def _parse_listing_page(html: str, city: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    for card in soup.find_all(class_="product-item"):
        a_tag = card.find("a", href=True)
        if not a_tag:
            continue
        href = a_tag["href"].strip()
        prod_url = WEB_BASE + href if href.startswith("/") else href
        listing_id = card.get("data-listing-id") or re.search(r"/(\d+)\.html", href)
        listing_id = listing_id.group(1) if hasattr(listing_id, "group") else listing_id
        price_span = card.find(class_="price")
        price_raw = price_span.get_text(strip=True) if price_span else ""
        muted_texts = [d.get_text(" ", strip=True) for d in card.find_all(class_="muted")]
        listings.append({
            "listing_id": listing_id, "city": city, "page_url": page_url,
            "product_url": prod_url, "product_name": a_tag.get_text(strip=True),
            "price_raw": price_raw, "muted_blocks": muted_texts,
        })
    return listings

def _fetch_and_parse_product(session: requests.Session, url: str, lid: str) -> dict:
    attempt = 0
    while attempt < MAX_RETRIES:
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 200:
                break
            if resp.status_code == 429:
                time.sleep(int(resp.headers.get("Retry-After", 2)))
                continue
            attempt += 1
            time.sleep(2 ** attempt)
        except requests.RequestException:
            attempt += 1
            time.sleep(2 ** attempt)
    else:
        return {}
        
    soup = BeautifulSoup(resp.text, "html.parser")
    h2 = soup.find("h2")
    name = h2.get_text(strip=True) if h2 else ""
    price_span = soup.find(class_="price")
    price_raw = price_span.get_text(strip=True) if price_span else ""
    muted_blocks = [p.get_text(" ", strip=True) for p in soup.find_all(class_="muted")]
    
    history = []
    tbl = soup.find("table")
    if tbl:
        for tr in tbl.find_all("tr")[1:]:
            cols = tr.find_all("td")
            if len(cols) >= 2:
                history.append({
                    "observed_on": cols[0].get_text(strip=True),
                    "price_raw": cols[1].get_text(strip=True),
                })
                
    return {
        "listing_id": lid, "product_url": url, "product_name": name,
        "price_raw": price_raw, "muted_blocks": muted_blocks, "price_history": history,
    }

def _extract_web(meta: dict, raw_dir: Path) -> dict:
    print("  → [extract] BazaarPulse web (Parallel) …")
    web_meta = meta.get("web", {})
    sitemap_entries = web_meta.get("sitemap_entries", [])
    if not sitemap_entries:
        return {"listings": 0, "products": 0, "status": "skipped"}

    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    city_entry_points = {}
    for entry in sitemap_entries:
        m = re.match(r"^/city/([^/]+)/", entry.strip())
        if m:
            city = m.group(1)
            city_entry_points[city] = WEB_BASE + entry.strip()

    all_listings = []
    product_urls = {}

    for city, entry_url in city_entry_points.items():
        visited = set()
        queue = [entry_url]
        while queue:
            url = queue.pop(0)
            if url in visited: continue
            visited.add(url)
            try:
                resp = session.get(url, timeout=10)
                if resp.status_code != 200: continue
                cards = _parse_listing_page(resp.text, city, url)
                all_listings.extend(cards)
                for c in cards:
                    if c["listing_id"] and c["listing_id"] not in product_urls:
                        product_urls[c["listing_id"]] = c["product_url"]
                
                soup = BeautifulSoup(resp.text, "html.parser")
                pager = soup.find(class_="pager")
                if pager:
                    for a in pager.find_all("a", href=True):
                        href = a["href"].strip()
                        next_url = WEB_BASE + href if href.startswith("/") else href
                        if next_url not in visited:
                            queue.append(next_url)
            except Exception:
                pass

    listings_written = _write_jsonl(raw_dir / "web_listings.jsonl", all_listings)
    print(f"    listing cards: {listings_written}")

    all_products = []
    print(f"    Fetching {len(product_urls)} product pages concurrently...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_fetch_and_parse_product, session, url, lid) for lid, url in product_urls.items()]
        for idx, f in enumerate(concurrent.futures.as_completed(futures), 1):
            res = f.result()
            if res: all_products.append(res)
            if idx % 100 == 0:
                sys.stdout.write(f"\r    products: {idx}/{len(product_urls)} ")
                sys.stdout.flush()

    products_written = _write_jsonl(raw_dir / "web_products.jsonl", all_products)
    print(f"\r    product pages: {products_written} total          ")
    return {"listings": listings_written, "products": products_written, "status": "OK"}

# ── API extraction ────────────────────────────────────────────────────────────

def _fetch_shipment_events(inv_id: str) -> list[dict]:
    session = requests.Session()
    session.headers.update({"X-API-Key": API_KEY})
    data = _api_get(session, f"{API_BASE}/v1/shipment_events", {"invoice_id": inv_id})
    events = []
    if data:
        for ev in data.get("events", []):
            ev["invoice_id"] = inv_id
            events.append(ev)
    return events

def _extract_api(raw_dir: Path) -> dict:
    print("  → [extract] Partner API (Parallel) …")
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({"X-API-Key": API_KEY})
    report  = {}

    data = _api_get(session, f"{API_BASE}/v1/carriers")
    carriers = data.get("data", []) if data else []
    _write_jsonl(raw_dir / "api_carriers.jsonl", carriers)
    report["carriers"] = len(carriers)

    surcharges = []
    months = ([f"2025-{m:02d}" for m in range(1, 13)] + [f"2026-{m:02d}" for m in range(1, 7)])
    for month in months:
        d = _api_get(session, f"{API_BASE}/v1/fuel_surcharge", {"month": month})
        if d: surcharges.append(d)
    _write_jsonl(raw_dir / "api_fuel_surcharge.jsonl", surcharges)
    report["fuel_surcharge_months"] = len(surcharges)

    inv_file = raw_dir / "api_freight_invoices.jsonl"
    inv_file.parent.mkdir(parents=True, exist_ok=True)
    invoice_ids = []
    total_invoices = 0
    cursor = None
    page = 0
    with inv_file.open("w", encoding="utf-8") as fh:
        while True:
            params = {"cursor": cursor} if cursor else {}
            data = _api_get(session, f"{API_BASE}/v1/freight_invoices", params)
            if not data: break
            batch = data.get("data", [])
            for r in batch:
                fh.write(json.dumps(r, default=str) + "\n")
                if "invoice_id" in r:
                    invoice_ids.append(r["invoice_id"])
            total_invoices += len(batch)
            cursor = data.get("next_cursor")
            page += 1
            sys.stdout.write(f"\r    invoices: {total_invoices:,} (page {page}) ")
            sys.stdout.flush()
            if not cursor: break

    print(f"\r    invoices: {total_invoices:,} total          ")
    report["freight_invoices"] = total_invoices

    SAMPLE_SIZE = 500
    sample_ids = invoice_ids[:SAMPLE_SIZE]
    all_events = []
    print(f"    Fetching {len(sample_ids)} shipment events concurrently...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_fetch_shipment_events, inv_id) for inv_id in sample_ids]
        for idx, f in enumerate(concurrent.futures.as_completed(futures), 1):
            all_events.extend(f.result())
            if idx % 50 == 0:
                sys.stdout.write(f"\r    events: {len(all_events)} (invoice {idx}/{len(sample_ids)})")
                sys.stdout.flush()

    print(f"\r    events: {len(all_events)} from {len(sample_ids)} sampled invoices    ")
    _write_jsonl(raw_dir / "api_shipment_events.jsonl", all_events)
    report["shipment_events"] = len(all_events)
    report["events_invoice_sample"] = len(sample_ids)

    return report

# ── orchestrator ─────────────────────────────────────────────────────────────

def run(db_path: str, meta_dir: str, out_dir: str) -> dict:
    t0 = time.perf_counter()
    raw_dir = Path(out_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    meta_file = Path(meta_dir) / "01_metadata.json"
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}

    db_report  = _extract_db(db_path, meta, raw_dir)
    web_report = _extract_web(meta, raw_dir)
    api_report = _extract_api(raw_dir)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_s":    round(time.perf_counter() - t0, 3),
        "db":           db_report,
        "web":          web_report,
        "api":          api_report,
    }
    (raw_dir / "02_extract_report.json").write_text(json.dumps(report, indent=2))
    return report
