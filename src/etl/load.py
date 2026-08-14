import duckdb
import json
from datetime import datetime, timezone
from pathlib import Path

def run(marts_dir: str = "data/telemetry/04_marts", manifest_path: str = "data/telemetry/05_load_manifest.json", duckdb_path: str = "data/warehouse.duckdb"):
    print("Initiating Load to DuckDB Analytics Warehouse...")
    
    marts_path = Path(marts_dir)
    man_path = Path(manifest_path)
    
    # Remove old database if exists
    if Path(duckdb_path).exists():
        Path(duckdb_path).unlink()
        
    con = duckdb.connect(duckdb_path)
    
    load_manifest = {
        "load_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "marts": {}
    }

    for jsonl_file in marts_path.glob("*.jsonl"):
        mart_name = jsonl_file.stem
        parquet_file = marts_path / f"{mart_name}.parquet"
        if parquet_file.exists():
            parquet_file.unlink()
        
        # Load JSONL directly into a staging table, export to Parquet
        con.execute(f"CREATE TABLE raw_{mart_name} AS SELECT * FROM read_json_auto('{str(jsonl_file)}')")
        con.execute(f"COPY raw_{mart_name} TO '{str(parquet_file)}' (FORMAT PARQUET)")
        
        # Create immutable read-only view pointing to the Parquet file
        con.execute(f"CREATE VIEW v_{mart_name} AS SELECT * FROM read_parquet('{str(parquet_file)}')")
        
        count = con.execute(f"SELECT count(*) FROM v_{mart_name}").fetchone()[0]
        
        # Drop the staging table so only the view remains
        con.execute(f"DROP TABLE raw_{mart_name}")
        
        load_manifest["marts"][mart_name] = {
            "row_count": count,
            "status": "READY",
            "view_name": f"v_{mart_name}",
            "parquet_file": str(parquet_file.name)
        }
        print(f"  -> Loaded Mart '{mart_name}' securely into view 'v_{mart_name}' (Rows: {count})")
        
    con.close()
    
    if man_path.exists():
        man_path.unlink()

    with open(man_path, "w") as f:
        json.dump(load_manifest, f, indent=4)