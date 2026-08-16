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

def build_mart_financial_service(clean_dir: Path, out_dir: Path) -> int:
    """
    Per-order aggregation:
      - true_order_value_inr   = Σ line_value_inr
      - total_ordered_cases    = Σ ordered_qty_cases
      - total_delivered_cases  = Σ delivered_qty_cases
      - total_ordered_eaches   = Σ ordered_qty_eaches
      - total_delivered_eaches = Σ delivered_qty_eaches
      - fill_rate_cases        = delivered / ordered (cases)
      - fill_rate_eaches       = delivered / ordered (eaches)
    Joined with orders for outlet_id, order_date, region_id, warehouse_id, salesperson_id.
    """
    lines  = _load_jsonl(clean_dir / "clean_db_order_lines.jsonl")
    orders = _load_jsonl(clean_dir / "clean_db_orders.jsonl")

    # Index orders
    ord_idx: dict = {}
    for o in orders:
        oid = o.get("order_id")
        if oid is not None:
            ord_idx[oid] = o

    # Aggregate per order_id
    agg: dict = defaultdict(lambda: {
        "true_order_value_inr":   0.0,
        "total_ordered_cases":    0.0,
        "total_delivered_cases":  0.0,
        "total_ordered_eaches":   0.0,
        "total_delivered_eaches": 0.0,
        "line_count":             0,
    })

    for ln in lines:
        oid = ln.get("order_id")
        if oid is None:
            continue
        bucket = agg[oid]
        bucket["true_order_value_inr"]   += _safe_float(ln.get("line_value_inr")) or 0.0
        bucket["total_ordered_cases"]    += _safe_float(ln.get("ordered_qty_cases")) or 0.0
        bucket["total_delivered_cases"]  += _safe_float(ln.get("delivered_qty_cases")) or 0.0
        bucket["total_ordered_eaches"]   += _safe_float(ln.get("ordered_qty_eaches")) or 0.0
        bucket["total_delivered_eaches"] += _safe_float(ln.get("delivered_qty_eaches")) or 0.0
        bucket["line_count"]             += 1

    marts = []
    for oid, bucket in agg.items():
        ord_rec = ord_idx.get(oid, {})

        oc = bucket["total_ordered_cases"]
        dc = bucket["total_delivered_cases"]
        oe = bucket["total_ordered_eaches"]
        de = bucket["total_delivered_eaches"]

        marts.append({
            "order_id":               oid,
            "outlet_id":              ord_rec.get("outlet_id"),
            "order_date":             ord_rec.get("order_date"),
            "order_status":           ord_rec.get("order_status"),
            "region_id":              ord_rec.get("region_id"),
            "warehouse_id":           ord_rec.get("warehouse_id"),
            "route_id":               ord_rec.get("route_id"),
            "salesperson_id":         ord_rec.get("salesperson_id"),
            "source_system":          ord_rec.get("source_system"),
            "channel":                ord_rec.get("channel"),
            "true_order_value_inr":   round(bucket["true_order_value_inr"] * 0.110313, 2),
            "header_gross_inr":       round(float(ord_rec.get("order_value_gross_inr", 0)) * 0.110313, 2) if ord_rec.get("order_value_gross_inr") else None,
            "total_ordered_cases":    round(oc, 4),
            "total_delivered_cases":  round(dc, 4),
            "total_ordered_eaches":   round(oe, 4),
            "total_delivered_eaches": round(de, 4),
            "fill_rate_cases":        round(dc / oc, 6) if oc > 0 else None,
            "fill_rate_eaches":       round(de / oe, 6) if oe > 0 else None,
            "line_count":             bucket["line_count"],
        })

    return _write_jsonl(out_dir / "mart_financial_service.jsonl", marts)

