"""
test_warehouse.py  —  Phase 1 Sanity Check
--------------------------------------------
Verifies the DuckDB warehouse is intact and all known data quality rules
were applied correctly. Run with: make test
"""
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH   = REPO_ROOT / "data" / "telemetry" / "05_warehouse" / "omnis_warehouse.duckdb"

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  {status}  {label}{suffix}")
    return condition


def main():
    print("\n" + "═" * 60)
    print("  OMNIS WAREHOUSE VERIFICATION")
    print("═" * 60 + "\n")

    if not DB_PATH.exists():
        print(f"  {FAIL}  Warehouse not found at {DB_PATH}")
        print("       Run 'make run' first.\n")
        sys.exit(1)

    con      = duckdb.connect(str(DB_PATH), read_only=True)
    passed   = 0
    failed   = 0

    def run_check(label, condition, detail=""):
        nonlocal passed, failed
        ok = check(label, condition, detail)
        if ok:
            passed += 1
        else:
            failed += 1
        return ok

    # ── 1. Core tables present ────────────────────────────────────────────────
    print("[1] Core tables & row counts")
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()}

    required = [
        "clean_db_orders", "clean_db_order_lines", "clean_db_outlets",
        "clean_db_deliveries", "clean_db_returns_credit_notes",
        "clean_db_products", "clean_db_inventory_snapshots",
        "mart_financial_service", "mart_q1_kpi",
        "mart_cold_chain_leakage", "mart_freight_cost_per_case",
        "mart_competitor_pricing",
    ]
    for tbl in required:
        exists = tbl in tables
        if exists:
            count = con.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            run_check(f"{tbl}", count > 0, f"{count:,} rows")
        else:
            run_check(f"{tbl}", False, "TABLE MISSING")

    # ── 2. KP-2377: Test/migration outlets excluded ───────────────────────────
    print("\n[2] KP-2377  Test outlets excluded from clean set")
    if "clean_db_outlets" in tables:
        n = con.execute("""
            SELECT COUNT(*) FROM clean_db_outlets
            WHERE lower(outlet_name) LIKE '%test%'
               OR lower(outlet_name) LIKE '%migration%'
               OR lower(outlet_name) LIKE '%dummy%'
               OR lower(outlet_name) LIKE 'zz_%'
               OR status = 'DELETED'
               OR is_deleted = 1
        """).fetchone()[0]
        run_check("No test/deleted outlets in clean set", n == 0, f"{n} found")

        # Salvage should have some
        if "clean_db_outlets_salvage" in tables:
            ns = con.execute("SELECT COUNT(*) FROM clean_db_outlets_salvage").fetchone()[0]
            run_check("Salvage table has excluded outlets", ns > 0, f"{ns} salvaged")

    # ── 3. KP-2402: Return quantities all positive ────────────────────────────
    print("\n[3] KP-2402  Return quantities normalised (all positive)")
    if "clean_db_returns_credit_notes" in tables:
        neg = con.execute("""
            SELECT COUNT(*) FROM clean_db_returns_credit_notes WHERE return_qty < 0
        """).fetchone()[0]
        run_check("No negative return_qty in clean set", neg == 0, f"{neg} negative")
        tot = con.execute("SELECT COUNT(*) FROM clean_db_returns_credit_notes").fetchone()[0]
        run_check("Return records loaded", tot > 0, f"{tot:,} rows")

    # ── 4. KP-2340: UOM normalisation ────────────────────────────────────────
    print("\n[4] KP-2340  UOM columns normalised (ordered_qty_cases + ordered_qty_eaches)")
    if "clean_db_order_lines" in tables:
        has_cases  = con.execute("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_name='clean_db_order_lines'
              AND column_name='ordered_qty_cases'
        """).fetchone()[0]
        has_eaches = con.execute("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_name='clean_db_order_lines'
              AND column_name='ordered_qty_eaches'
        """).fetchone()[0]
        run_check("ordered_qty_cases column present",  has_cases  > 0)
        run_check("ordered_qty_eaches column present", has_eaches > 0)

        # Spot-check: CASE rows should have non-null cases, EACH rows non-null eaches
        case_nulls = con.execute("""
            SELECT COUNT(*) FROM clean_db_order_lines
            WHERE qty_uom='CASE' AND ordered_qty_cases IS NULL
        """).fetchone()[0]
        run_check("CASE rows have ordered_qty_cases", case_nulls == 0,
                  f"{case_nulls} CASE rows missing cases value")

    # ── 5. KP-2301: True order value computed ────────────────────────────────
    print("\n[5] KP-2301  True order value in financial mart")
    if "mart_financial_service" in tables:
        n = con.execute("""
            SELECT COUNT(*) FROM mart_financial_service WHERE true_order_value_inr > 0
        """).fetchone()[0]
        run_check("mart_financial_service has positive true values", n > 0, f"{n:,} rows")

        # Sample: check fill rates are between 0 and some sensible bound
        bad_fr = con.execute("""
            SELECT COUNT(*) FROM mart_financial_service
            WHERE fill_rate_cases IS NOT NULL
              AND (fill_rate_cases < 0 OR fill_rate_cases > 5)
        """).fetchone()[0]
        run_check("fill_rate_cases within 0–5 range", bad_fr == 0, f"{bad_fr} outliers")

    # ── 6. Q1 mart has data ───────────────────────────────────────────────────
    print("\n[6] Q1 KPI mart (April–June)")
    if "mart_q1_kpi" in tables:
        n = con.execute("SELECT COUNT(*) FROM mart_q1_kpi").fetchone()[0]
        run_check("mart_q1_kpi has rows", n > 0, f"{n:,}")
        months = {r[0] for r in con.execute("SELECT DISTINCT q1_month FROM mart_q1_kpi").fetchall()}
        run_check("Q1 months are 4,5,6 only", months.issubset({4, 5, 6}),
                  f"months found: {sorted(months)}")

    # ── 7. Cold chain leakage ─────────────────────────────────────────────────
    print("\n[7] Cold chain leakage mart")
    if "mart_cold_chain_leakage" in tables:
        n = con.execute("SELECT COUNT(*) FROM mart_cold_chain_leakage").fetchone()[0]
        run_check("mart_cold_chain_leakage has rows", n > 0, f"{n:,}")
        leakage_inr = con.execute("""
            SELECT SUM(return_leakage_inr) FROM mart_cold_chain_leakage
            WHERE return_leakage_inr IS NOT NULL
        """).fetchone()[0]
        run_check("Total leakage INR computable", leakage_inr is not None and leakage_inr > 0,
                  f"₹{leakage_inr:,.0f}" if leakage_inr else "NULL")

    # ── 8. Freight mart ───────────────────────────────────────────────────────
    print("\n[8] Freight cost mart")
    if "mart_freight_cost_per_case" in tables:
        n = con.execute("SELECT COUNT(*) FROM mart_freight_cost_per_case").fetchone()[0]
        run_check("mart_freight_cost_per_case has rows", n > 0, f"{n:,}")

    # ── 9. Competitor pricing mart ────────────────────────────────────────────
    print("\n[9] Competitor pricing mart")
    if "mart_competitor_pricing" in tables:
        n = con.execute("SELECT COUNT(*) FROM mart_competitor_pricing").fetchone()[0]
        run_check("mart_competitor_pricing has rows", n > 0, f"{n:,}")
        mapped = con.execute("""
            SELECT COUNT(*) FROM mart_competitor_pricing
            WHERE kestrel_sku_code IS NOT NULL
        """).fetchone()[0]
        run_check("SKU-mapped listings exist", mapped > 0, f"{mapped} Kestrel-mapped rows")

    # ── 10. System / telemetry tables ─────────────────────────────────────────
    print("\n[10] System telemetry tables for AI Agent")
    for sys_tbl in ["sys_01_metadata", "sys_02_extract", "sys_03_clean", "sys_04_transform"]:
        run_check(f"{sys_tbl} present", sys_tbl in tables)

    # ── Summary ───────────────────────────────────────────────────────────────
    con.close()
    total = passed + failed
    print(f"\n{'═'*60}")
    print(f"  {passed}/{total} checks passed   {FAIL if failed else PASS}  {failed} failed")
    print(f"{'═'*60}\n")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()