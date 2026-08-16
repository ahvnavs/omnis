import time
import json
from pathlib import Path
from src.pipeline.cleaners.db_cleaner import _clean_outlets, _clean_order_lines, _clean_orders, _clean_returns, _clean_deliveries, _clean_inventory_snapshots
from src.pipeline.cleaners.web_cleaner import _clean_web_listings, _clean_web_products
from src.pipeline.cleaners.api_cleaner import _clean_api_freight_invoices, _clean_api_shipment_events

def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0: return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try: rows.append(json.loads(line))
                except json.JSONDecodeError: pass
    return rows

def _write_jsonl(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records: fh.write(json.dumps(rec, default=str) + "\n")

def run(meta_dir: str, raw_dir: str, out_dir: str) -> dict:
    t0 = time.perf_counter()
    raw_p = Path(raw_dir)
    out_p = Path(out_dir)
    out_p.mkdir(parents=True, exist_ok=True)
    report = {"tables": {}}
    
    steps = [
        ("db_outlets", "clean_db_outlets.jsonl", _clean_outlets),
        ("db_orders", "clean_db_orders.jsonl", _clean_orders),
        ("db_order_lines", "clean_db_order_lines.jsonl", _clean_order_lines),
        ("db_returns_credit_notes", "clean_db_returns_credit_notes.jsonl", _clean_returns),
        ("db_deliveries", "clean_db_deliveries.jsonl", _clean_deliveries),
        ("db_inventory_snapshots", "clean_db_inventory_snapshots.jsonl", _clean_inventory_snapshots),
        ("web_listings", "clean_web_listings.jsonl", _clean_web_listings),
        ("web_products", "clean_web_products.jsonl", _clean_web_products),
        ("api_freight_invoices", "clean_api_freight_invoices.jsonl", _clean_api_freight_invoices),
        ("api_shipment_events", "clean_api_shipment_events.jsonl", _clean_api_shipment_events),
    ]

    for name, out_name, fn in steps:
        raw_file = raw_p / f"{name}.jsonl"
        rows = _load_jsonl(raw_file)
        if rows:
            clean, salvage = fn(rows)
            _write_jsonl(out_p / out_name, clean)
            if salvage:
                _write_jsonl(out_p / f"{out_name.replace('.jsonl','')}_salvage.jsonl", salvage)
            report["tables"][name] = {"clean": len(clean), "salvage": len(salvage)}

    # Direct passthrough tables
    for pt in ["db_products", "db_product_price_history", "db_warehouses", "db_routes", "db_regions", "db_salespeople", "db_promotions", "api_carriers", "api_fuel_surcharge"]:
        raw_file = raw_p / f"{pt}.jsonl"
        rows = _load_jsonl(raw_file)
        if rows:
            _write_jsonl(out_p / f"clean_{pt}.jsonl", rows)
            report["tables"][pt] = {"clean": len(rows), "salvage": 0}

    report["elapsed_s"] = round(time.perf_counter() - t0, 3)
    (out_p / "03_clean_report.json").write_text(json.dumps(report, indent=2))
    return report
