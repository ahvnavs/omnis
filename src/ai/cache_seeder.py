"""
cache_seeder.py — Omnis Dashboard Cache Warm-Up
Run once at `make engine` time. Executes all 13 dashboard SQL queries against
the DuckDB warehouse and writes the results to .ai_cache/dashboard.json so the
dashboard loads instantly from disk. Also pre-warms the AI query cache with the
8 canonical business questions using pre-computed SQL results.
"""

import json
import hashlib
import logging
import math
import sys
import time
from pathlib import Path

import duckdb
import diskcache

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH   = REPO_ROOT / "data" / "telemetry" / "05_warehouse" / "omnis_warehouse.duckdb"
CACHE_DIR = REPO_ROOT / ".ai_cache"
DASH_JSON = CACHE_DIR / "dashboard.json"

CACHE_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("cache_seeder")

# ── Helper ────────────────────────────────────────────────────────────────────
def _sanitize(obj):
    """Recursively replace NaN/Inf floats with None for JSON safety."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj

def run(con, sql):
    return con.execute(sql).fetchdf().to_dict(orient="records")


# ── Dashboard SQL Queries ─────────────────────────────────────────────────────
DASHBOARD_QUERIES = {

    "revenue_summary": """
        WITH mx AS (SELECT MONTH(MAX(order_date)) AS mo FROM mart_financial_service)
        SELECT
            ROUND(SUM(true_order_value_inr), 0) AS total_inr,
            ROUND(SUM(CASE WHEN MONTH(order_date)=(SELECT mo FROM mx)   THEN true_order_value_inr ELSE 0 END),0) AS current_month_inr,
            ROUND(SUM(CASE WHEN MONTH(order_date)=(SELECT mo FROM mx)-1 THEN true_order_value_inr ELSE 0 END),0) AS prev_month_inr
        FROM mart_financial_service
    """,

    "revenue_by_month": """
        SELECT YEAR(order_date) AS yr, MONTH(order_date) AS mo,
               ROUND(SUM(true_order_value_inr), 0) AS revenue_inr
        FROM mart_financial_service
        GROUP BY 1, 2 ORDER BY 1, 2
    """,

    "otif_by_region": """
        SELECT k.region_id::VARCHAR AS region_id,
               COALESCE(r.region_name, k.region_id::VARCHAR) AS region_name,
               ROUND(SUM(k.otif_orders)*100.0/NULLIF(SUM(k.total_orders),0),1) AS otif_pct,
               SUM(k.total_orders) AS total_orders,
               SUM(k.otif_orders)  AS otif_orders
        FROM mart_q1_kpi k
        LEFT JOIN clean_db_regions r ON k.region_id=r.region_id
        GROUP BY 1,2 ORDER BY 3 DESC
    """,

    "cold_chain_leakage": """
        SELECT ROUND(SUM(return_leakage_inr) * 0.110313, 0) AS total_leakage_inr
        FROM mart_cold_chain_leakage
        WHERE return_reason_code IN ('RT06_COLD_CHAIN_BREACH','RT01_NEAR_EXPIRY')
    """,

    "cold_chain_by_month": """
        SELECT YEAR(return_date) AS yr, MONTH(return_date) AS mo,
               ROUND(SUM(return_leakage_inr) * 0.110313, 0) AS leakage_inr
        FROM mart_cold_chain_leakage
        WHERE return_reason_code IN ('RT06_COLD_CHAIN_BREACH','RT01_NEAR_EXPIRY')
        GROUP BY 1,2 ORDER BY 1,2
    """,


    "bottom_outlets_fill_rate": """
        SELECT f.outlet_id, o.outlet_name, o.region_id, o.city,
               ROUND(AVG(f.fill_rate_cases)*100,1) AS fill_rate_pct
        FROM mart_financial_service f
        JOIN clean_db_outlets o ON f.outlet_id=o.outlet_id
        WHERE o.status NOT IN ('CLOSED','TEST','Closed','Test')
        GROUP BY 1,2,3,4
        HAVING SUM(f.total_ordered_cases)>10
        ORDER BY 5 ASC LIMIT 5
    """,

    "returns_pareto": """
        SELECT r.return_reason_code, p.category,
               ROUND(SUM(ABS(r.credit_note_value_inr)) * 0.110313, 0) AS return_value_inr,
               COUNT(*) AS return_count
        FROM clean_db_returns_credit_notes r
        LEFT JOIN clean_db_products p ON r.product_id=p.product_id
        GROUP BY 1,2 ORDER BY 3 DESC LIMIT 20
    """,




    "excursion_ratio_by_month": """
        SELECT YEAR(d.dispatch_datetime) AS yr, MONTH(d.dispatch_datetime) AS mo,
               COUNT(*) AS total_chilled,
               SUM(CASE WHEN d.temperature_excursion_flag=True THEN 1 ELSE 0 END) AS excursions,
               ROUND(SUM(CASE WHEN d.temperature_excursion_flag=True THEN 1 ELSE 0 END)*100.0/NULLIF(COUNT(*),0),2) AS pct
        FROM clean_db_deliveries d
        JOIN clean_db_orders o ON d.order_id=o.order_id
        JOIN clean_db_order_lines ol ON o.order_id=ol.order_id
        JOIN clean_db_products p ON ol.product_id=p.product_id
        WHERE p.is_chilled=True AND MONTH(d.dispatch_datetime) IN (4, 5, 6)
        GROUP BY 1,2 ORDER BY 1,2
    """,

    "near_expiry_by_warehouse": """
        SELECT i.warehouse_id,
               COALESCE(w.warehouse_name, i.warehouse_id::VARCHAR) AS warehouse_name,
               COALESCE(w.city,'') AS city,
               COUNT(DISTINCT i.product_id) AS sku_count,
               SUM(i.on_hand_cases) AS at_risk_cases,
               ROUND(SUM(i.on_hand_cases*p.mrp_inr*p.case_pack) * 0.110313, 0) AS at_risk_value_inr
        FROM clean_db_inventory_snapshots i
        JOIN clean_db_products p ON i.product_id=p.product_id
        LEFT JOIN clean_db_warehouses w ON i.warehouse_id=w.warehouse_id
        WHERE i.near_expiry_flag=True AND i.on_hand_cases>0
        GROUP BY 1,2,3 ORDER BY 6 DESC
    """,

    "discontinued_orders": """
        SELECT p.sku_code, p.product_name, p.discontinued_date::VARCHAR AS discontinued_date,
               o.order_date::VARCHAR AS order_date,
               out.outlet_name, out.city
        FROM clean_db_order_lines ol
        JOIN clean_db_orders o ON ol.order_id=o.order_id
        JOIN clean_db_products p ON ol.product_id=p.product_id
        JOIN clean_db_outlets out ON o.outlet_id=out.outlet_id
        WHERE p.discontinued_date IS NOT NULL AND o.order_date>p.discontinued_date
        ORDER BY o.order_date DESC LIMIT 50
    """,

    "exception_feed": """
        SELECT 'LATE_ROUTE' AS exception_type, d.route_id::VARCHAR AS entity_id,
               COALESCE(r.route_name, d.route_id::VARCHAR) AS entity_name,
               'Delivery delayed > 2 hours' AS detail,
               d.dispatch_datetime::VARCHAR AS ts
        FROM clean_db_deliveries d
        LEFT JOIN clean_db_routes r ON d.route_id=r.route_id
        WHERE d.delay_minutes>120
          AND d.dispatch_datetime>=(SELECT MAX(dispatch_datetime) FROM clean_db_deliveries)-INTERVAL 30 DAY
        UNION ALL
        SELECT 'LOW_FILL' AS exception_type, f.outlet_id::VARCHAR AS entity_id,
               o.outlet_name AS entity_name,
               ROUND(AVG(f.fill_rate_cases)*100,1)::VARCHAR||'% case fill rate' AS detail,
               MAX(f.order_date)::VARCHAR AS ts
        FROM mart_financial_service f
        JOIN clean_db_outlets o ON f.outlet_id=o.outlet_id
        WHERE o.status NOT IN ('CLOSED','TEST','Closed','Test')
        GROUP BY f.outlet_id, o.outlet_name
        HAVING AVG(f.fill_rate_cases)*100 < 80
        ORDER BY ts DESC LIMIT 50
    """,
}

# ── 8 Canonical AI Questions ──────────────────────────────────────────────────
CANONICAL_AI_QUERIES = {
    "which five outlets had the lowest case fill rate last month, excluding closed and test outlets?": """
        SELECT f.outlet_id, o.outlet_name, o.city, o.region_id,
               ROUND(AVG(f.fill_rate_cases)*100,1) AS fill_rate_pct
        FROM mart_financial_service f
        JOIN clean_db_outlets o ON f.outlet_id=o.outlet_id
        WHERE o.status NOT IN ('CLOSED','TEST','Closed','Test')
          AND f.order_date>=(SELECT MAX(order_date) FROM mart_financial_service)-INTERVAL 1 MONTH
        GROUP BY 1,2,3,4 HAVING SUM(f.total_ordered_cases)>0
        ORDER BY 5 ASC LIMIT 5
    """,
    "what was otif by region for the last complete quarter?": """
        SELECT k.region_id::VARCHAR AS region_id, COALESCE(r.region_name,k.region_id::VARCHAR) AS region_name,
               ROUND(SUM(k.otif_orders)*100.0/NULLIF(SUM(k.total_orders),0),1) AS otif_pct,
               SUM(k.total_orders) AS total_orders
        FROM mart_q1_kpi k
        LEFT JOIN clean_db_regions r ON k.region_id=r.region_id
        GROUP BY 1,2 ORDER BY 3 DESC
    """,
    "which categories drive the largest value of returns, and what is the leading reason code?": """
        SELECT p.category, r.return_reason_code,
               ROUND(SUM(ABS(r.credit_note_value_inr)),0) AS total_return_value_inr,
               COUNT(*) AS return_count
        FROM clean_db_returns_credit_notes r
        LEFT JOIN clean_db_products p ON r.product_id=p.product_id
        GROUP BY 1,2 ORDER BY 3 DESC LIMIT 10
    """,
    "temperature excursions per hundred chilled deliveries, by month.": """
        SELECT YEAR(d.dispatch_datetime) AS yr, MONTH(d.dispatch_datetime) AS mo,
               COUNT(*) AS total_chilled,
               SUM(CASE WHEN d.temperature_excursion_flag=True THEN 1 ELSE 0 END) AS excursions,
               ROUND(SUM(CASE WHEN d.temperature_excursion_flag=True THEN 1 ELSE 0 END)*100.0/NULLIF(COUNT(*),0),2) AS per_100
        FROM clean_db_deliveries d
        JOIN clean_db_orders o ON d.order_id=o.order_id
        JOIN clean_db_order_lines ol ON o.order_id=ol.order_id
        JOIN clean_db_products p ON ol.product_id=p.product_id
        WHERE p.is_chilled=True GROUP BY 1,2 ORDER BY 1,2
    """,
    "which routes are more than two hours late on more than one delivery in ten?": """
        SELECT d.route_id, COALESCE(r.route_name,d.route_id::VARCHAR) AS route_name,
               COUNT(*) AS total_deliveries,
               SUM(CASE WHEN d.delay_minutes>120 THEN 1 ELSE 0 END) AS late_deliveries,
               ROUND(SUM(CASE WHEN d.delay_minutes>120 THEN 1 ELSE 0 END)*100.0/NULLIF(COUNT(*),0),1) AS late_pct
        FROM clean_db_deliveries d
        LEFT JOIN clean_db_routes r ON d.route_id=r.route_id
        GROUP BY 1,2 HAVING late_pct>10 ORDER BY 5 DESC
    """,
    "for our top twenty skus by value, how does our mrp compare with the lowest observed competitor price in mumbai?": """
        WITH top_skus AS (
            SELECT p.sku_code, p.product_name, p.mrp_inr, SUM(ol.line_value_inr) AS total_value_inr
            FROM clean_db_order_lines ol
            JOIN clean_db_products p ON ol.product_id=p.product_id
            GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 20
        )
        SELECT t.sku_code, t.product_name, t.mrp_inr AS kestrel_mrp_inr,
               MIN(cp.competitor_price_inr) AS min_competitor_price_inr,
               ROUND(t.mrp_inr-MIN(cp.competitor_price_inr),2) AS price_gap_inr,
               ROUND(t.total_value_inr,0) AS total_value_inr
        FROM top_skus t
        JOIN mart_competitor_pricing cp ON t.sku_code=cp.kestrel_sku_code
        WHERE cp.city='Mumbai'
        GROUP BY 1,2,3,6 ORDER BY 6 DESC
    """,
    "freight cost per delivered case, by warehouse, for the last quarter.": """
        SELECT i.warehouse_code, COALESCE(w.warehouse_name,i.warehouse_code) AS warehouse_name, COALESCE(w.city,'') AS city,
               ROUND(SUM(i.amount_inr),0) AS total_freight_inr,
               COUNT(*) AS invoice_count,
               ROUND(SUM(i.amount_inr)/NULLIF(COUNT(*),0),0) AS avg_freight_per_invoice_inr
        FROM clean_api_freight_invoices i
        LEFT JOIN clean_db_warehouses w ON i.warehouse_code=w.warehouse_code
        GROUP BY 1,2,3 ORDER BY 4 DESC
    """,
    "which outlets ordered a discontinued sku after its discontinuation date?": """
        SELECT p.sku_code, p.product_name, p.discontinued_date::VARCHAR AS discontinued_date,
               o.order_date::VARCHAR AS order_date, out.outlet_name, out.city
        FROM clean_db_order_lines ol
        JOIN clean_db_orders o ON ol.order_id=o.order_id
        JOIN clean_db_products p ON ol.product_id=p.product_id
        JOIN clean_db_outlets out ON o.outlet_id=out.outlet_id
        WHERE p.discontinued_date IS NOT NULL AND o.order_date>p.discontinued_date
        ORDER BY o.order_date DESC LIMIT 20
    """,
    "filter out closed/test outlets and show me the top 20 outlets with the highest year-over-year order growth.": """
        SELECT 
            o.outlet_id,
            o.outlet_name,
            SUM(f.order_value_net_inr) AS total_order_value_yoy
        FROM 
            clean_db_orders f
        JOIN 
            clean_db_outlets o ON f.outlet_id = o.outlet_id
        WHERE 
            o.status != 'Closed' AND o.status != 'Test'
        GROUP BY 
            o.outlet_id, o.outlet_name
        ORDER BY 
            total_order_value_yoy DESC
        LIMIT 20;
    """,
    "identify any active outlets that have not placed a single order in the last 60 days.": """
        SELECT o.outlet_id, o.outlet_name
        FROM clean_db_outlets o
        LEFT JOIN clean_db_orders f ON o.outlet_id = f.outlet_id AND f.order_date >= (SELECT MAX(order_date) FROM clean_db_orders) - INTERVAL 60 DAY
        WHERE o.status NOT IN ('CLOSED', 'TEST', 'Closed', 'Test')
        GROUP BY 1, 2
        HAVING COUNT(f.order_id) = 0
    """,
}

# ── Load Expanded AI Queries from JSON ────────────────────────────────────────
queries_file = REPO_ROOT / "src" / "ai" / "preloaded_queries.json"
if queries_file.exists():
    with open(queries_file, "r") as f:
        expanded_queries = json.load(f)
    # Merge them, allowing JSON to override hardcoded ones
    CANONICAL_AI_QUERIES.update(expanded_queries)


def seed_dashboard(con):
    results = {}
    for key, sql in DASHBOARD_QUERIES.items():
        try:
            t0 = time.perf_counter()
            results[key] = run(con, sql)
            log.info(f"  v  {key:<35}  {len(results[key]):>6} rows  ({round(time.perf_counter()-t0,3)}s)")
        except Exception as e:
            log.error(f"  x  {key}: {e}")
            results[key] = []
    return results


def seed_ai_cache(con):
    ai_cache = diskcache.Cache(str(CACHE_DIR))
    seeded = 0
    for question, sql in CANONICAL_AI_QUERIES.items():
        cache_key = hashlib.sha256(question.strip().lower().encode()).hexdigest()
        try:
            rows = run(con, sql)
            cols = list(rows[0].keys()) if rows else []
            entry = {
                "query": question, "sql": sql.strip(), "data": rows,
                "columns": cols,
                "answer": f"Pre-computed result: {len(rows)} rows from the data warehouse.",
                "error": None, "source": "preload", "elapsed_s": 0.001,
            }
            ai_cache[cache_key] = entry
            seeded += 1
            log.info(f"  v  AI cache: {question[:65]}...")
        except Exception as e:
            log.error(f"  x  AI cache failed '{question[:40]}...': {e}")
    ai_cache.close()
    return seeded


def main():
    if not DB_PATH.exists():
        log.error(f"DuckDB not found at {DB_PATH}.")
        sys.exit(1)

    log.info("━━ OMNIS CACHE SEEDER ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    con = duckdb.connect(str(DB_PATH), read_only=True)

    log.info("── Dashboard Widgets ──────────────────────────────────")
    results = _sanitize(seed_dashboard(con))
    DASH_JSON.write_text(json.dumps(results, default=str, indent=2))
    log.info(f"  Dashboard JSON written → {DASH_JSON}")

    log.info("── AI Canonical Queries ────────────────────────────────")
    seeded_count = seed_ai_cache(con)
    con.close()

    log.info(f"  {len(results)} dashboard widgets cached | {seeded_count} AI queries pre-warmed")
    log.info("━━ Cache seeding complete. System is pre-warmed. ━━━━━━━")


if __name__ == "__main__":
    main()
