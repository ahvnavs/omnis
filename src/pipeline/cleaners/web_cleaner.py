import json
from datetime import datetime, timezone
from pathlib import Path
import re
from src.pipeline.cleaners.common import IST, EXCLUDED_OUTLET_STATUSES, EXCLUDED_OUTLET_NAME_PATTERNS, CITY_ALIASES, KESTREL_SKU_MAP

def _is_test_outlet(name: str) -> bool:
    """Return True if the outlet name matches known test/migration patterns."""
    name_lower = name.strip().lower()
    # Prefix check: ZZ_ is a convention for excluded records
    if name_lower.startswith("zz_"):
        return True
    # Substring check for other known patterns
    if EXCLUDED_OUTLET_NAME_PATTERNS.search(name):
        return True
    return False

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
                    pass   # corrupt JSONL line — skip silently (counted in report)
    return rows

def _write_jsonl(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, default=str) + "\n")

def _parse_inr(text: str) -> float | None:
    """Extract a float rupee value from strings like '₹267.74' or 'Rs.288'."""
    if not text:
        return None
    m = re.search(r"(?:₹|Rs\.?)\s*([\d,]+(?:\.\d+)?)", str(text))
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None

def _utc_to_ist(ts: str) -> str:
    """Convert 'YYYY-MM-DDTHH:MM:SSZ' → IST ISO string. Returns original if unparseable."""
    if not ts:
        return ts
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(ts.rstrip("Z").strip(), fmt).replace(tzinfo=timezone.utc)
            return dt.astimezone(IST).isoformat()
        except ValueError:
            continue
    return ts

def _normalise_city(city: str) -> str:
    return CITY_ALIASES.get(city.strip().lower(), city.strip().title())

def _map_kestrel_sku(product_name: str) -> str | None:
    name_lower = product_name.lower()
    for fragment, sku in KESTREL_SKU_MAP.items():
        if fragment in name_lower:
            return sku
    return None

def _clean_web_listings(rows: list[dict]) -> tuple[list, list]:
    """Parse price floats and city normalisation from listing cards."""
    clean, salvage = [], []
    for row in rows:
        issues = []
        price = _parse_inr(row.get("price_raw", ""))
        if price is None:
            issues.append("price_raw unparseable")
        row["current_price_inr"] = price

        city = row.get("city", "")
        row["city_normalised"] = _normalise_city(city) if city else None

        sku = _map_kestrel_sku(row.get("product_name", ""))
        row["kestrel_sku_code"] = sku

        if issues:
            row["_issues"] = issues
            salvage.append(row)
        else:
            clean.append(row)

    return clean, salvage

def _clean_web_products(rows: list[dict]) -> tuple[list, list]:
    """
    Parse structured fields from product detail pages.
    muted_blocks[0]: 'Retailer · City · Pack · Category'
    muted_blocks[1]: 'MRP ₹NNN · In stock · rated X.X (NNN)'
    """
    clean, salvage = [], []
    for row in rows:
        issues = []
        muted  = row.get("muted_blocks", [])

        # current price
        price = _parse_inr(row.get("price_raw", ""))
        if price is None:
            issues.append("price_raw unparseable")
        row["current_price_inr"] = price

        # Retailer / city / pack from first muted block
        retailer = city = pack = category = None
        if muted:
            parts = [p.strip() for p in re.split(r"·|&middot;", muted[0])]
            if len(parts) >= 1: retailer  = parts[0].strip() or None
            if len(parts) >= 2: pack      = parts[1].strip() or None
            if len(parts) >= 3: category  = parts[2].strip() or None

        # city is on the detail page in the breadcrumb <p class="muted">Home / City / Category
        # It may also appear in the URL (listing page stores city separately)
        row["retailer"]           = retailer
        row["pack"]               = pack
        row["category"]           = category

        # MRP from second muted block
        mrp = None
        if len(muted) >= 2:
            mrp = _parse_inr(muted[1])
        # Fallback: search all muted blocks for 'MRP ₹NNN'
        if mrp is None:
            for blk in muted:
                if "mrp" in blk.lower() or "MRP" in blk:
                    mrp = _parse_inr(blk)
                    if mrp: break
        row["mrp_inr"] = mrp

        # Price history: parse each entry
        history = []
        for h in row.get("price_history", []):
            p = _parse_inr(h.get("price_raw", ""))
            history.append({
                "observed_on": h.get("observed_on"),
                "price_inr":   p,
            })
        row["price_history_clean"] = history

        # Kestrel SKU mapping
        row["kestrel_sku_code"] = _map_kestrel_sku(row.get("product_name", ""))

        if issues:
            row["_issues"] = issues
            salvage.append(row)
        else:
            clean.append(row)

    return clean, salvage

def _passthrough(rows: list[dict]) -> tuple[list, list]:
    """Tables that need no cleaning beyond what extract already provides."""
    return rows, []

