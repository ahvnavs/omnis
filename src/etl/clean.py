import polars as pl
import time
import json
from pathlib import Path

PII_COLUMNS = ["contact_name", "contact_phone", "contact_email", "driver_name"]

def run(raw_dir: str = "data/telemetry/02_raw", clean_dir: str = "data/telemetry/03_clean", report_path: str = "data/telemetry/03_clean_report.json"):
    print("Initiating Sanitization Phase from JSONL...")
    t0 = time.time()
    
    clean_path = Path(clean_dir)
    clean_path.mkdir(parents=True, exist_ok=True)

    telemetry = {"module": "sanitizer_engine", "total_rows_dropped_system_wide": 0, "table_metrics": {}}

    for jsonl_file in Path(raw_dir).glob("*.jsonl"):
        table_name = jsonl_file.stem
        
        try:
            df = pl.read_ndjson(jsonl_file, infer_schema_length=None)
            initial_rows = df.height
            applied_fixes = []

            # MASK PII
            for pii_col in PII_COLUMNS:
                if pii_col in df.columns:
                    df = df.with_columns(pl.lit("***MASKED***").alias(pii_col))
                    if "Masked PII" not in applied_fixes: 
                        applied_fixes.append("Masked PII")

            # OUTLETS
            if table_name == "outlets":
                if "is_deleted" in df.columns and "status" in df.columns:
                    df = df.filter((pl.col("is_deleted") == 0) & (pl.col("status").cast(pl.String).str.to_uppercase() != "CLOSED"))
                if "outlet_name" in df.columns:
                    df = df.filter(~pl.col("outlet_name").cast(pl.String).str.to_lowercase().str.contains("test|migration"))
                if "outlet_code" in df.columns and "onboarded_date" in df.columns:
                    df = df.sort("onboarded_date", descending=True).unique(subset=["outlet_code"], keep="first")
                applied_fixes.append("KP-2377/2211: Exclusions & Deduplication")

            # RETURNS
            elif table_name == "returns_credit_notes":
                if "return_qty" in df.columns:
                    df = df.with_columns(pl.col("return_qty").abs().alias("return_qty"))
                if "approval_date" in df.columns:
                    df = df.drop("approval_date")
                applied_fixes.append("KP-2402: Absolute Returns & dropped defects")

            # DELIVERIES
            elif table_name == "deliveries":
                if "fuel_cost_inr" in df.columns:
                    df = df.drop("fuel_cost_inr")
                applied_fixes.append("Dropped driver fuel_cost")

            # TEXT STANDARDIZATION
            if "city" in df.columns:
                df = df.with_columns(pl.col("city").cast(pl.String).str.strip_chars().str.to_titlecase().alias("city"))
                applied_fixes.append("KP-2288: City Standardization")

            # SAVE
            out_file = clean_path / f"{table_name}.jsonl"
            if out_file.exists():
                out_file.unlink()
            df.write_ndjson(out_file)

            final_rows = df.height
            rows_dropped = initial_rows - final_rows
            telemetry["total_rows_dropped_system_wide"] += rows_dropped
            telemetry["table_metrics"][table_name] = {
                "status": "success", "initial_rows": initial_rows, "final_rows": final_rows,
                "rows_dropped": rows_dropped, "applied_fixes": applied_fixes
            }
            print(f"  -> Cleaned '{table_name}': Dropped {rows_dropped:,} rows.")

        except Exception as e:
            telemetry["table_metrics"][table_name] = {"status": "failed", "error": str(e)}
            print(f"  -> FAILED cleaning '{table_name}': {e}")

    telemetry["execution_time"] = round(time.time() - t0, 3)
    rep_file = Path(report_path)
    if rep_file.exists():
        rep_file.unlink()

    with open(rep_file, "w") as f:
        json.dump(telemetry, f, indent=4)