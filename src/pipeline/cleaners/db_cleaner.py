import json
from datetime import datetime
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

def _clean_outlets(rows: list[dict]) -> tuple[list, list]:
    """
    KP-2211 dedup + KP-2377 test exclusion + KP-2288 city normalisation.
    Returns (clean, salvage).
    """
    clean, salvage = [], []
    seen_codes: dict[str, dict] = {}   # outlet_code → best row (latest updated_at)

    for row in rows:
        issues = []
        name   = str(row.get("outlet_name", "") or "")
        status = str(row.get("status", "") or "")
        code   = str(row.get("outlet_code", "") or "")

        if status in EXCLUDED_OUTLET_STATUSES:
            issues.append(f"status={status} excluded")
        if _is_test_outlet(name):
            issues.append("test/migration outlet name pattern")
        if row.get("is_deleted") == 1:
            issues.append("is_deleted=1")


        if issues:
            row["_issues"] = issues
            salvage.append(row)
            continue

        # City normalisation (KP-2288)
        if row.get("city"):
            row["city"] = _normalise_city(str(row["city"]))

        # Dedup (KP-2211): keep row with latest updated_at for each outlet_code
        if code in seen_codes:
            existing_ts = seen_codes[code].get("updated_at", "")
            current_ts  = row.get("updated_at", "")
            if str(current_ts) > str(existing_ts):
                seen_codes[code] = row
        else:
            seen_codes[code] = row

    clean = list(seen_codes.values())
    return clean, salvage

def _clean_order_lines(rows: list[dict]) -> tuple[list, list]:
    """
    KP-2340: normalise qty_uom → add ordered_qty_cases + ordered_qty_eaches.
    Both source UOMs are CASE and EACH (confirmed from DB inspection).
    case_pack_at_order is the conversion factor.
    """
    clean, salvage = [], []
    for row in rows:
        issues = []
        uom     = str(row.get("qty_uom", "") or "").upper().strip()
        qty     = row.get("ordered_qty")
        del_qty = row.get("delivered_qty")
        alloc   = row.get("allocated_qty")
        cpp     = row.get("case_pack_at_order")

        # line_value_inr must exist for financials (KP-2301)
        if row.get("line_value_inr") is None:
            issues.append("line_value_inr is NULL")

        try:
            qty     = float(qty)     if qty     is not None else None
            del_qty = float(del_qty) if del_qty is not None else None
            alloc   = float(alloc)   if alloc   is not None else None
            cpp     = float(cpp)     if cpp     is not None else None
        except (TypeError, ValueError) as e:
            issues.append(f"qty cast error: {e}")
            row["_issues"] = issues
            salvage.append(row)
            continue

        if uom == "CASE":
            row["ordered_qty_cases"]  = qty
            row["ordered_qty_eaches"] = (qty * cpp) if cpp and qty is not None else None
            row["delivered_qty_cases"] = del_qty
            row["delivered_qty_eaches"] = (del_qty * cpp) if cpp and del_qty is not None else None
        elif uom == "EACH":
            row["ordered_qty_eaches"]  = qty
            row["ordered_qty_cases"]   = (qty / cpp) if cpp and qty else None
            row["delivered_qty_eaches"] = del_qty
            row["delivered_qty_cases"]  = (del_qty / cpp) if cpp and del_qty is not None else None
        else:
            issues.append(f"unknown qty_uom={uom!r}")
            row["ordered_qty_cases"]   = None
            row["ordered_qty_eaches"]  = qty
            row["delivered_qty_cases"] = None
            row["delivered_qty_eaches"] = del_qty

        if issues:
            row["_issues"] = issues

        # Route to salvage only if line_value_inr is NULL (still keep the rest)
        if "line_value_inr is NULL" in issues:
            salvage.append(row)
        else:
            clean.append(row)

    return clean, salvage

def _clean_orders(rows: list[dict]) -> tuple[list, list]:
    """
    KP-2301: order_value_gross_inr may not match Σ line_value_inr.
    We add a _header_value_flag; the true value is computed in transform.py.
    Also normalise created_at to ISO where possible.
    """
    clean, salvage = [], []
    for row in rows:
        issues = []
        # Mark orders where gross value looks suspicious (non-positive)
        gross = row.get("order_value_gross_inr")
        if gross is not None:
            try:
                if float(gross) <= 0:
                    issues.append("order_value_gross_inr <= 0")
            except (TypeError, ValueError):
                issues.append("order_value_gross_inr not numeric")

        if issues:
            row["_issues"] = issues
            salvage.append(row)
        else:
            clean.append(row)

    return clean, salvage

def _clean_returns(rows: list[dict]) -> tuple[list, list]:
    """
    KP-2402: return_qty sign inconsistent across feeds. Apply ABS().
    Record whether the original was negative in _was_negative.
    """
    clean, salvage = [], []
    for row in rows:
        qty = row.get("return_qty")
        if qty is not None:
            try:
                fqty = float(qty)
                row["_was_negative"] = fqty < 0
                row["return_qty"]    = abs(fqty)
            except (TypeError, ValueError):
                row["_issues"] = ["return_qty not numeric"]
                salvage.append(row)
                continue
        clean.append(row)
    return clean, salvage

def _clean_deliveries(rows: list[dict]) -> tuple[list, list]:
    """
    Deliveries have two telematics vendors with different actual_arrival formats.
    We try to normalise to YYYY-MM-DDTHH:MM:SS; flag unparseable values.
    returned_cases is cast to int.
    """
    clean, salvage = [], []
    FORMATS = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S",
    ]
    for row in rows:
        issues = []
        aa = row.get("actual_arrival")
        if aa and str(aa).strip():
            parsed = None
            for fmt in FORMATS:
                try:
                    parsed = datetime.strptime(str(aa).strip(), fmt)
                    break
                except ValueError:
                    continue
            if parsed:
                row["actual_arrival_normalised"] = parsed.isoformat()
            else:
                issues.append(f"actual_arrival unparseable: {aa!r}")
                row["actual_arrival_normalised"] = None

        # Cast returned_cases to int
        rc = row.get("returned_cases")
        if rc is not None:
            try:
                row["returned_cases"] = int(float(rc))
            except (TypeError, ValueError):
                issues.append("returned_cases not numeric")

        if issues:
            row["_issues"] = issues
        clean.append(row)   # keep ALL delivery rows — just flag issues

    return clean, salvage

def _clean_inventory_snapshots(rows: list[dict]) -> tuple[list, list]:
    """Add near_expiry_flag (days_of_cover < 7)."""
    for row in rows:
        doc = row.get("days_of_cover")
        try:
            row["near_expiry_flag"] = 1 if doc is not None and float(doc) < 7 else 0
        except (TypeError, ValueError):
            row["near_expiry_flag"] = None
    return rows, []

def _passthrough(rows: list[dict]) -> tuple[list, list]:
    """Tables that need no cleaning beyond what extract already provides."""
    return rows, []

