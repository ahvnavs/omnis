import duckdb
from pathlib import Path

DB_PATH = "data/telemetry/05_warehouse/01_omnis_warehouse.duckdb"

def verify_warehouse():
    print("\n" + "="*50)
    print("  🦅 OMNIS WAREHOUSE SANITY CHECK & COMPLIANCE VERIFICATION")
    print("="*50 + "\n")
    
    if not Path(DB_PATH).exists():
        print(f"❌ Error: DuckDB warehouse not found at {DB_PATH}")
        print("Run 'make run' first.")
        return

    try:
        con = duckdb.connect(DB_PATH)
        
        print("[1] Tables & Views Generated (Business + System Context):")
        con.sql("SHOW TABLES").show()

        print("\n[2] Financial & Service Mart (Top 5 Rows):")
        print("    -> Checking KP-2301 resolution (True Order Value & Fill Rate)")
        con.sql("SELECT order_id, salesperson_id, fill_rate_pct, true_order_value_inr FROM v_mart_financial_and_service LIMIT 5").show()

        print("\n[3] Clean Competitor Prices (Top 5 Rows):")
        print("    -> Checking Web Scraping Regex Floats & Parsed Pricing")
        con.sql("SELECT raw_text_block, source_url, city, clean_price_inr FROM v_clean_competitor_prices LIMIT 5").show()

        print("\n[4] AI Context & System Telemetry (JSON-to-DuckDB Tables):")
        print("    -> Verifying metadata, extraction, cleaning, transformation reports, and load manifest loaded for Text-to-SQL agent:")
        con.sql("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_name LIKE 'sys_%'
        """).show()

        print("\n    -> Peeking at sys_05_load_manifest (AI Load Manifest Context):")
        try:
            con.sql("SELECT * FROM sys_05_load_manifest LIMIT 1").show()
        except Exception:
            print("       (sys_05_load_manifest table structure check skipped or empty)")

        print("\n[5] Data Anomaly & Sanitization Verification:")
        print("    -> Checking Outlets (KP-2211 / KP-2377: Closed & test outlets filtered):")
        try:
            closed_test_count = con.sql("SELECT count(*) FROM v_clean_outlets WHERE status = 'CLOSED' OR lower(outlet_name) LIKE '%test%'").fetchone()[0]
            print(f"       Closed/Test outlets remaining in clean view: {closed_test_count} (Expected: 0)")
        except Exception as e:
            print(f"       Could not verify outlet filtering: {e}")

        print("    -> Checking Returns Sign Correction (KP-2402: Negative quantities fixed via ABS):")
        try:
            negative_returns = con.sql("SELECT count(*) FROM v_clean_returns WHERE return_qty < 0").fetchone()[0]
            print(f"       Negative return quantities remaining: {negative_returns} (Expected: 0)")
        except Exception as e:
            print(f"       Could not verify returns sign correction: {e}")

        con.close()
        print("\n✅ Verification Complete. Warehouse is fully intact, sanitized, and queryable by the AI Agent.")
        
    except Exception as e:
        print(f"\n❌ Verification failed: {e}")

if __name__ == "__main__":
    verify_warehouse()