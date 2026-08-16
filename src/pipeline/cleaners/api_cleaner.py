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

def _clean_api_freight_invoices(rows: list[dict]) -> tuple[list, list]:
    """
    Convert amount (paise → INR). Convert created_at_utc → IST.
    Validate required fields.
    """
    clean, salvage = [], []
    for row in rows:
        issues = []

        # paise → INR
        amt = row.get("amount")
        if amt is not None:
            try:
                row["amount_inr"] = float(amt) / 100.0
            except (TypeError, ValueError):
                issues.append("amount not numeric")
                row["amount_inr"] = None
        else:
            issues.append("amount missing")
            row["amount_inr"] = None

        # detention_charge paise → INR
        det = row.get("detention_charge")
        if det is not None:
            try:
                row["detention_charge_inr"] = float(det) / 100.0
            except (TypeError, ValueError):
                row["detention_charge_inr"] = None

        # UTC → IST
        row["created_at_ist"] = _utc_to_ist(str(row.get("created_at_utc", "")))

        if issues:
            row["_issues"] = issues
            salvage.append(row)
        else:
            clean.append(row)

    return clean, salvage

def _clean_api_shipment_events(rows: list[dict]) -> tuple[list, list]:
    """Convert timestamp_utc → IST."""
    for row in rows:
        row["timestamp_ist"] = _utc_to_ist(str(row.get("timestamp_utc", "")))
    return rows, []

def _passthrough(rows: list[dict]) -> tuple[list, list]:
    """Tables that need no cleaning beyond what extract already provides."""
    return rows, []

