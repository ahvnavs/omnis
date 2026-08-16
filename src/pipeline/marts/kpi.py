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

def build_mart_q1_kpi(clean_dir: Path, out_dir: Path) -> int:
    """
    Filter financial/service rows to Q1 (April–June) and compute:
      - fill_rate_cases / fill_rate_eaches per region/warehouse/route
      - OTIF (on_time AND in_full) from deliveries joined to orders
    """
    fin    = _load_jsonl(out_dir.parent / "04_marts" / "mart_financial_service.jsonl")
    # Fallback if building in-sequence
    if not fin:
        fin = _load_jsonl(out_dir / "mart_financial_service.jsonl")

    deliveries = _load_jsonl(clean_dir / "clean_db_deliveries.jsonl")
    orders     = _load_jsonl(clean_dir / "clean_db_orders.jsonl")

    # map order_id → delivery (for delay_minutes)
    order_del: dict = {}
    for d in deliveries:
        oid = d.get("order_id")
        if oid is not None:
            order_del[oid] = d

    # map order_id → order (for order_date, status)
    order_map: dict = {}
    for o in orders:
        oid = o.get("order_id")
        if oid is not None:
            order_map[oid] = o

    # Aggregate Q1 rows by (region_id, warehouse_id, route_id, order_date_month, year)
    GroupKey = tuple   # (region_id, warehouse_id, route_id, year, month)
    agg: dict[GroupKey, dict] = defaultdict(lambda: {
        "ordered_cases":    0.0,
        "delivered_cases":  0.0,
        "ordered_eaches":   0.0,
        "delivered_eaches": 0.0,
        "total_orders":     0,
        "otif_orders":      0,
        "on_time_orders":   0,
        "in_full_orders":   0,
    })

    for row in fin:
        od = str(row.get("order_date", "") or "")
        if not od or len(od) < 7:
            continue
        try:
            year  = int(od[:4])
            month = int(od[5:7])
        except ValueError:
            continue
        if month not in Q1_MONTHS:
            continue

        key = (
            row.get("region_id"),
            row.get("warehouse_id"),
            row.get("route_id"),
            year,
            month,
        )
        bucket = agg[key]
        bucket["ordered_cases"]   += _safe_float(row.get("total_ordered_cases")) or 0.0
        bucket["delivered_cases"] += _safe_float(row.get("total_delivered_cases")) or 0.0
        bucket["ordered_eaches"]  += _safe_float(row.get("total_ordered_eaches")) or 0.0
        bucket["delivered_eaches"]+= _safe_float(row.get("total_delivered_eaches")) or 0.0
        bucket["total_orders"]    += 1

        oid = row.get("order_id")
        fr_cases = _safe_float(row.get("fill_rate_cases"))
        in_full  = fr_cases is not None and fr_cases >= 0.90

        del_rec  = order_del.get(oid, {})
        delay    = _safe_float(del_rec.get("delay_minutes"))
        on_time  = delay is not None and delay <= 0

        if in_full:  bucket["in_full_orders"] += 1
        if on_time:  bucket["on_time_orders"]  += 1
        if in_full and on_time:
            bucket["otif_orders"] += 1

    marts = []
    for (region_id, wh_id, route_id, year, month), b in agg.items():
        oc  = b["ordered_cases"]
        dc  = b["delivered_cases"]
        oe  = b["ordered_eaches"]
        de  = b["delivered_eaches"]
        tot = b["total_orders"]
        marts.append({
            "region_id":         region_id,
            "warehouse_id":      wh_id,
            "route_id":          route_id,
            "year":              year,
            "q1_month":          month,
            "total_orders":      tot,
            "ordered_cases":     round(oc, 4),
            "delivered_cases":   round(dc, 4),
            "ordered_eaches":    round(oe, 4),
            "delivered_eaches":  round(de, 4),
            "fill_rate_cases":   round(dc / oc, 6) if oc > 0 else None,
            "fill_rate_eaches":  round(de / oe, 6) if oe > 0 else None,
            "otif_orders":       b["otif_orders"],
            "on_time_orders":    b["on_time_orders"],
            "in_full_orders":    b["in_full_orders"],
            "otif_rate":         round(b["otif_orders"]    / tot, 6) if tot else None,
            "on_time_rate":      round(b["on_time_orders"] / tot, 6) if tot else None,
            "in_full_rate":      round(b["in_full_orders"] / tot, 6) if tot else None,
        })

    return _write_jsonl(out_dir / "mart_q1_kpi.jsonl", marts)

