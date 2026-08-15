import duckdb
import json
import time
from datetime import datetime, timezone
from pathlib import Path

def run(clean_dir: str, marts_dir: str, out_dir: str):
    t0 = time.time()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    print("  -> Loading Single Source of Truth to DuckDB...")
    
    db_file = out_path / "01_omnis_warehouse.duckdb"
    if db_file.exists(): db_file.unlink()
        
    con = duckdb.connect(str(db_file))
    manifest = {"timestamp": datetime.now(timezone.utc).isoformat(), "views": {}}

    def ingest(directory):
        for jsonl_file in Path(directory).glob("*.jsonl"):
            if jsonl_file.stat().st_size == 0: continue
            
            table_name = jsonl_file.stem
            parquet_file = out_path / f"{table_name}.parquet"
            
            try:
                con.execute(f"CREATE TABLE raw_{table_name} AS SELECT * FROM read_json_auto('{str(jsonl_file)}')")
                con.execute(f"COPY raw_{table_name} TO '{str(parquet_file)}' (FORMAT PARQUET)")
                con.execute(f"CREATE VIEW v_{table_name} AS SELECT * FROM read_parquet('{str(parquet_file)}')")
                con.execute(f"DROP TABLE raw_{table_name}")
                
                count = con.execute(f"SELECT count(*) FROM v_{table_name}").fetchone()[0]
                manifest["views"][f"v_{table_name}"] = {"rows": count}
                print(f"    Loaded view: v_{table_name} ({count} rows)")
            except Exception as e:
                print(f"    Failed to load {table_name}: {e}")

    ingest(clean_dir)
    ingest(marts_dir)
        
    con.close()
    
    # Save manifest strictly as 05_load_manifest.json
    with open(out_path / "05_load_manifest.json", "w") as f:
        json.dump(manifest, f, indent=4)
        
    print(f"  -> DuckDB Loaded and Secured in {round(time.time()-t0, 2)}s")

def load_telemetry_to_duckdb(db_path="data/telemetry/05_warehouse/01_omnis_warehouse.duckdb"):
    """
    Recursively scans data/telemetry for all JSON reports and loads them 
    into DuckDB as sys_ tables so the AI Agent has full system context.
    """
    if not Path(db_path).exists():
        print(f"Warning: Warehouse not found at {db_path} for telemetry load.")
        return

    conn = duckdb.connect(db_path)
    base_dir = Path("data/telemetry")
    
    loaded_count = 0
    try:
        for json_file in base_dir.glob("**/*.json"):
            if "05_warehouse" in json_file.parts and json_file.name != "05_load_manifest.json":
                continue
                
            table_name = f"sys_{json_file.stem}"
            try:
                conn.execute(f"""
                    CREATE OR REPLACE TABLE {table_name} AS 
                    SELECT * FROM read_json_auto('{json_file}')
                """)
                loaded_count += 1
            except Exception:
                try:
                    conn.execute(f"""
                        CREATE OR REPLACE TABLE {table_name} AS 
                        SELECT * FROM read_json('{json_file}', option='auto')
                    """)
                    loaded_count += 1
                except Exception as ex:
                    print(f"Could not load {json_file.name}: {ex}")
                    
        print(f"  -> Successfully injected {loaded_count} system telemetry tables into DuckDB.")
        
    except Exception as e:
        print(f"Warning: Could not load telemetry into DuckDB: {e}")
    finally:
        conn.close()