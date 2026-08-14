import sqlite3
import polars as pl
import time
import json
from pathlib import Path

def run(db_path: str, metadata_path: str = "data/telemetry/01_metadata.json", out_dir: str = "data/telemetry/02_raw", report_path: str = "data/telemetry/02_extract_report.json"):
    print("Initiating Aware Live-Production Extraction Phase...")
    t0 = time.time()
    
    with open(metadata_path, "r") as f:
        metadata = json.load(f)
        
    expected_tables = metadata.get("Tables_Entities", {})
    uri = f"file:{Path(db_path).absolute()}?mode=ro"
    
    telemetry = {"module": "extraction_engine", "table_metrics": {}}

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(uri, uri=True, timeout=60.0)
    cursor = conn.cursor()
    cursor.execute("BEGIN DEFERRED TRANSACTION;")
    print("  -> Snapshot Isolation achieved. Streaming to JSONL...")

    for table_name, table_meta in expected_tables.items():
        expected_row_count = table_meta.get("Table_Row_Count", 0)
        
        try:
            cursor.execute(f'SELECT * FROM "{table_name}"')
            columns = [col[0] for col in cursor.description]
            data = cursor.fetchall()
            
            df = pl.DataFrame(data, schema=columns, infer_schema_length=None, strict=False, orient="row")
            
            output_file = out_path / f"{table_name}.jsonl"
            if output_file.exists():
                output_file.unlink()
            df.write_ndjson(output_file)
            
            actual_row_count = df.height
            telemetry["table_metrics"][table_name] = {
                "status": "success",
                "extracted_rows": actual_row_count,
                "drift_delta": actual_row_count - expected_row_count
            }
            print(f"  -> Extracted '{table_name}' to JSONL ({actual_row_count:,} rows)")

        except Exception as e:
            telemetry["table_metrics"][table_name] = {"status": "failed", "error": str(e)}
            print(f"  -> FAILED extracting '{table_name}': {e}")

    cursor.execute("COMMIT;")
    conn.close()
    
    telemetry["status"] = "completed"
    telemetry["execution_time"] = round(time.time() - t0, 3)
    
    rep_file = Path(report_path)
    if rep_file.exists():
        rep_file.unlink()

    with open(rep_file, "w") as f:
        json.dump(telemetry, f, indent=4)