import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

Q1_MONTHS = {4, 5, 6}

def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows

def _write_jsonl(path: Path, records: list) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, default=str) + "\n")
    return len(records)

def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _order_month(order_date: str) -> int | None:
    """Return month number from 'YYYY-MM-DD' string, or None."""
    try:
        return int(str(order_date)[5:7])
    except (TypeError, ValueError, IndexError):
        return None

def build_mart_competitor_pricing(clean_dir: Path, out_dir: Path) -> int:
    """
    Compare BazaarPulse prices against Kestrel MRP.
    Only rows where kestrel_sku_code is not null (matched via SKU map in clean step).
    Adds price_vs_mrp_delta_pct = (competitor_price - kestrel_mrp) / kestrel_mrp * 100
    """
    products  = _load_jsonl(clean_dir / "clean_db_products.jsonl")
    web_prods = _load_jsonl(clean_dir / "clean_web_products.jsonl")

    # Index products by sku_code
    prod_by_sku: dict = {}
    for p in products:
        sku = p.get("sku_code")
        if sku:
            prod_by_sku[sku] = p

    marts = []
    for wp in web_prods:
        sku  = wp.get("kestrel_sku_code")
        if not sku:
            continue

        prod = prod_by_sku.get(sku, {})
        comp_price = _safe_float(wp.get("current_price_inr"))
        mrp        = _safe_float(wp.get("mrp_inr")) or _safe_float(prod.get("mrp_inr"))
        kestrel_mrp = _safe_float(prod.get("mrp_inr"))

        delta_pct = None
        if comp_price is not None and kestrel_mrp and kestrel_mrp > 0:
            delta_pct = round((comp_price - kestrel_mrp) / kestrel_mrp * 100, 2)

        # Extract city from muted_blocks if available
        muted = wp.get("muted_blocks", [])
        city  = None
        if muted:
            # breadcrumb: "Home / City / Category"
            for blk in muted:
                m = __import__("re").search(r"Home\s*/\s*([^/]+)\s*/", blk)
                if m:
                    city = m.group(1).strip()
                    break

        marts.append({
            "listing_id":            wp.get("listing_id"),
            "product_url":           wp.get("product_url"),
            "competitor_product_name": wp.get("product_name"),
            "kestrel_sku_code":      sku,
            "kestrel_product_name":  prod.get("product_name"),
            "category":              wp.get("category") or prod.get("category"),
            "retailer":              wp.get("retailer"),
            "city":                  city,
            "pack":                  wp.get("pack"),
            "competitor_price_inr":  comp_price,
            "observed_mrp_inr":      mrp,
            "kestrel_mrp_inr":       kestrel_mrp,
            "kestrel_list_price_inr": prod.get("list_price_inr"),
            "price_vs_mrp_delta_pct": delta_pct,
            "is_below_mrp":          (comp_price < kestrel_mrp) if (comp_price is not None and kestrel_mrp) else None,
        })

    return _write_jsonl(out_dir / "mart_competitor_pricing.jsonl", marts)

