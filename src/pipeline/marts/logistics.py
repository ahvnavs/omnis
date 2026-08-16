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

def build_mart_cold_chain_leakage(clean_dir: Path, out_dir: Path) -> int:
    """
    Per-return line with cold-chain context:
      - return_leakage_inr = ABS(return_qty) * unit_price_inr
      - temperature_excursion_flag from delivery
      - near_expiry_flag from inventory_snapshots (if batch_id matches)
    Only includes cold-chain-relevant reason codes: RT01, RT06, RT02, RT04.
    """
    returns    = _load_jsonl(clean_dir / "clean_db_returns_credit_notes.jsonl")
    lines      = _load_jsonl(clean_dir / "clean_db_order_lines.jsonl")
    deliveries = _load_jsonl(clean_dir / "clean_db_deliveries.jsonl")
    orders     = _load_jsonl(clean_dir / "clean_db_orders.jsonl")
    snapshots  = _load_jsonl(clean_dir / "clean_db_inventory_snapshots.jsonl")
    products   = _load_jsonl(clean_dir / "clean_db_products.jsonl")

    # Index order_line → unit_price, batch_id, product_id
    line_idx: dict = {}
    for ln in lines:
        lid = ln.get("order_line_id")
        if lid is not None:
            line_idx[lid] = ln

    # Index order → delivery
    order_del: dict = {}
    for d in deliveries:
        oid = d.get("order_id")
        if oid is not None:
            order_del[oid] = d

    # Index order_id → order
    order_map: dict = {}
    for o in orders:
        oid = o.get("order_id")
        if oid is not None:
            order_map[oid] = o

    # Index batch_id → near_expiry_flag (most recent snapshot wins)
    batch_near_expiry: dict = {}
    for snap in snapshots:
        bid  = snap.get("batch_id")
        nef  = snap.get("near_expiry_flag")
        date = snap.get("snapshot_date", "")
        if bid and nef is not None:
            existing = batch_near_expiry.get(bid, ("", None))
            if str(date) > str(existing[0]):
                batch_near_expiry[bid] = (date, nef)

    # Index product_id → product
    prod_idx: dict = {}
    for p in products:
        pid = p.get("product_id")
        if pid is not None:
            prod_idx[pid] = p

    COLD_CHAIN_CODES = {"RT01_NEAR_EXPIRY", "RT02_DAMAGE_TRANSIT",
                        "RT04_QUALITY", "RT06_COLD_CHAIN_BREACH"}

    marts = []
    for ret in returns:
        reason = str(ret.get("return_reason_code", "") or "")
        if reason not in COLD_CHAIN_CODES:
            continue

        lid    = ret.get("order_line_id")
        line   = line_idx.get(lid, {})
        unit_p = _safe_float(line.get("unit_price_inr"))
        qty    = _safe_float(ret.get("return_qty"))   # already ABS'd in clean step
        leakage_inr = (qty * unit_p) if qty is not None and unit_p is not None else None

        oid  = ret.get("order_id")
        ord_rec = order_map.get(oid, {})
        del_rec = order_del.get(oid, {})

        batch_id = line.get("batch_id") or ret.get("batch_id")
        _, near_expiry = batch_near_expiry.get(str(batch_id), ("", None)) if batch_id else ("", None)

        pid  = ret.get("product_id") or line.get("product_id")
        prod = prod_idx.get(pid, {})

        marts.append({
            "return_id":                ret.get("return_id"),
            "credit_note_number":       ret.get("credit_note_number"),
            "return_date":              ret.get("return_date"),
            "return_reason_code":       reason,
            "disposition":              ret.get("disposition"),
            "order_id":                 oid,
            "order_line_id":            lid,
            "outlet_id":                ret.get("outlet_id"),
            "product_id":               pid,
            "sku_code":                 prod.get("sku_code"),
            "product_name":             prod.get("product_name"),
            "category":                 prod.get("category"),
            "is_chilled":               prod.get("is_chilled"),
            "return_qty":               qty,
            "qty_uom":                  ret.get("qty_uom"),
            "unit_price_inr":           unit_p,
            "return_leakage_inr":       round(leakage_inr, 2) if leakage_inr else None,
            "credit_note_value_inr":    ret.get("credit_note_value_inr"),
            "temperature_excursion_flag": del_rec.get("temperature_excursion_flag"),
            "max_temp_celsius":         del_rec.get("max_temp_celsius"),
            "near_expiry_flag":         near_expiry,
            "warehouse_id":             del_rec.get("warehouse_id"),
            "region_id":                ord_rec.get("region_id"),
            "route_id":                 del_rec.get("route_id"),
        })

    return _write_jsonl(out_dir / "mart_cold_chain_leakage.jsonl", marts)

def build_mart_freight_cost_per_case(clean_dir: Path, out_dir: Path) -> int:
    """
    True freight cost per delivered case.
    Joins API freight invoices (amount_inr) → deliveries (via route_code / warehouse_code).
    The deliveries table does NOT have invoice_id, so we match on route_code + invoice_date ≈ actual_arrival date.
    Where the join is impossible, we aggregate by carrier / warehouse / route from the invoice side.
    """
    invoices   = _load_jsonl(clean_dir / "clean_api_freight_invoices.jsonl")
    deliveries = _load_jsonl(clean_dir / "clean_db_deliveries.jsonl")
    orders_fin = _load_jsonl(out_dir / "mart_financial_service.jsonl")

    # Total delivered cases per order_id from financial mart
    del_cases_by_order: dict = {}
    for row in orders_fin:
        oid = row.get("order_id")
        dc  = _safe_float(row.get("total_delivered_cases"))
        if oid is not None and dc is not None:
            del_cases_by_order[oid] = dc

    # Total delivered cases per delivery
    del_cases_by_delivery: dict = {}
    for d in deliveries:
        did  = d.get("delivery_id")
        oid  = d.get("order_id")
        dc   = del_cases_by_order.get(oid, 0.0)
        if did is not None:
            del_cases_by_delivery[did] = dc

    # Aggregate invoices by (carrier_id, warehouse_code, route_code)
    GroupKey = tuple
    agg: dict[GroupKey, dict] = defaultdict(lambda: {
        "total_amount_inr":    0.0,
        "invoice_count":       0,
        "paid_amount_inr":     0.0,
        "disputed_amount_inr": 0.0,
    })

    for inv in invoices:
        key = (
            inv.get("carrier_id"),
            inv.get("carrier_name"),
            inv.get("warehouse_code"),
            inv.get("route_code"),
        )
        amt    = _safe_float(inv.get("amount_inr")) or 0.0
        status = str(inv.get("status", "") or "")
        agg[key]["total_amount_inr"]    += amt
        agg[key]["invoice_count"]       += 1
        if status == "PAID":
            agg[key]["paid_amount_inr"] += amt
        elif status == "DISPUTED":
            agg[key]["disputed_amount_inr"] += amt

    # Aggregate delivered_cases per (warehouse_code, route_code) from deliveries
    del_cases_by_route: dict[tuple, float] = defaultdict(float)
    route_wh_map: dict = {}
    for d in deliveries:
        wh_id = d.get("warehouse_id")
        rt_id = d.get("route_id")
        oid   = d.get("order_id")
        dc    = del_cases_by_order.get(oid, 0.0)
        del_cases_by_route[(wh_id, rt_id)] += dc

    marts = []
    for (carrier_id, carrier_name, wh_code, route_code), b in agg.items():
        total_amt = b["total_amount_inr"]
        count     = b["invoice_count"]
        # We can't perfectly join warehouse_code (API) to warehouse_id (DB) without a lookup
        # So we report at the invoice-level grain and let the AI agent join on warehouse_code
        marts.append({
            "carrier_id":             carrier_id,
            "carrier_name":           carrier_name,
            "warehouse_code":         wh_code,
            "route_code":             route_code,
            "invoice_count":          count,
            "total_freight_inr":      round(total_amt, 2),
            "paid_freight_inr":       round(b["paid_amount_inr"], 2),
            "disputed_freight_inr":   round(b["disputed_amount_inr"], 2),
            "avg_freight_per_invoice_inr": round(total_amt / count, 2) if count else None,
            # NOTE: freight_cost_per_case requires delivery-level case counts which
            # need a warehouse_code→warehouse_id lookup (not in API response).
            # Recorded as null here; the AI agent can compute it with a JOIN.
            "delivered_cases":        None,
            "freight_cost_per_case_inr": None,
        })

    return _write_jsonl(out_dir / "mart_freight_cost_per_case.jsonl", marts)

