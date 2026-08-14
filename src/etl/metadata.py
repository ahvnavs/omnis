import sqlite3
import time
import json
from pathlib import Path

def run(db_path: str, out_path: str = "data/telemetry/01_metadata.json"):
    print("Initiating Aware DB Introspection...")
    t0 = time.time()
    db_file = Path(db_path)
    
    uri = f"file:{db_file.absolute()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND type='table';")
    tables = [row[0] for row in cursor.fetchall()]

    tables_detailed = {}
    for table_name in tables:
        cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        row_count = cursor.fetchone()[0]
        
        cursor.execute(f'PRAGMA table_info("{table_name}");')
        col_info = cursor.fetchall()
        
        tables_detailed[table_name] = {
            "Table_Row_Count": row_count,
            "Table_Column_Count": len(col_info),
            "Columns": {col[1]: {"Data_Type": col[2]} for col in col_info}
        }

    conn.close()

    report = {
        "Telemetry_Metadata": {"module": "metadata_scanner", "execution_time_seconds": round(time.time() - t0, 3)},
        "System_Architecture": {"Database_Name": db_file.name},
        "Tables_Entities": tables_detailed
    }

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists():
        out_file.unlink()

    with open(out_file, "w") as f:
        json.dump(report, f, indent=4)
    print(f"  -> Metadata exported to {out_path}")