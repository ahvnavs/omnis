"""
load.py  —  Phase 1, Step 5
------------------------------
Loads all clean JSONL and mart JSONL files into a DuckDB analytical warehouse.

Strategy:
  1. Each JSONL → DuckDB TABLE (not view) so queries are fast and self-contained.
  2. Clean tables: prefix  clean_
  3. Mart tables:  prefix  mart_
  4. Salvage tables: prefix salvage_  (for audit / debugging)
  5. Also copies all four JSON telemetry reports into DuckDB as sys_ tables
     so the Phase 2 AI Agent can introspect the pipeline's own metadata.

Output:
  data/telemetry/05_warehouse/omnis_warehouse.duckdb
  data/telemetry/05_warehouse/05_load_manifest.json

The manifest records every table name, row count, and file source —
this is what the AI Agent uses to know what exists and what it means.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb


DB_FILENAME = "omnis_warehouse.duckdb"


def _load_jsonl_to_duckdb(con: duckdb.DuckDBPyConnection,
                           jsonl_path: Path,
                           table_name: str) -> int:
    """Read a JSONL file into a DuckDB table. Returns row count."""
    if not jsonl_path.exists() or jsonl_path.stat().st_size == 0:
        return 0
    # DuckDB can read JSONL natively with read_json_auto
    con.execute(f"""
        CREATE OR REPLACE TABLE "{table_name}" AS
        SELECT * FROM read_json_auto('{jsonl_path}', ignore_errors=true)
    """)
    count = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
    return count


def _load_json_report_to_duckdb(con: duckdb.DuckDBPyConnection,
                                 json_path: Path,
                                 table_name: str) -> bool:
    """Load a JSON report file as a single-row table for agent context."""
    if not json_path.exists():
        return False
    try:
        con.execute(f"""
            CREATE OR REPLACE TABLE "{table_name}" AS
            SELECT * FROM read_json_auto('{json_path}')
        """)
        return True
    except Exception:
        # Some reports have nested structure DuckDB can't auto-infer;
        # store as a raw text column instead
        try:
            content = json_path.read_text()
            escaped = content.replace("'", "''")
            con.execute(f"""
                CREATE OR REPLACE TABLE "{table_name}" AS
                SELECT '{escaped}'::VARCHAR AS raw_json
            """)
            return True
        except Exception:
            return False


def run(clean_dir: str, marts_dir: str, out_dir: str,
        telemetry_dirs: list[str] | None = None) -> dict:
    t0    = time.perf_counter()
    out_p = Path(out_dir)
    out_p.mkdir(parents=True, exist_ok=True)

    db_path = out_p / DB_FILENAME
    if db_path.exists():
        db_path.unlink()   # fresh build every run

    con      = duckdb.connect(str(db_path))
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path":      str(db_path.absolute()),
        "tables":       {},
    }

    print(f"  → [load] DuckDB at {db_path}")

    # ── 1. Clean tables ───────────────────────────────────────────────────────
    print("  → [load] clean tables …")
    clean_p = Path(clean_dir)
    for jsonl in sorted(clean_p.glob("clean_*.jsonl")):
        if "_salvage" in jsonl.name:
            continue
        table_name = jsonl.stem   # clean_db_orders, clean_web_products, etc.
        count = _load_jsonl_to_duckdb(con, jsonl, table_name)
        manifest["tables"][table_name] = {"source": str(jsonl), "rows": count, "type": "clean"}
        print(f"    {table_name}: {count:,}")

    # ── 2. Salvage tables (for audit) ─────────────────────────────────────────
    print("  → [load] salvage tables …")
    for jsonl in sorted(clean_p.glob("*_salvage.jsonl")):
        table_name = jsonl.stem   # clean_db_outlets_salvage, etc.
        count = _load_jsonl_to_duckdb(con, jsonl, table_name)
        manifest["tables"][table_name] = {"source": str(jsonl), "rows": count, "type": "salvage"}
        print(f"    {table_name}: {count:,}")

    # ── 3. Mart tables ────────────────────────────────────────────────────────
    print("  → [load] mart tables …")
    marts_p = Path(marts_dir)
    for jsonl in sorted(marts_p.glob("mart_*.jsonl")):
        table_name = jsonl.stem
        count = _load_jsonl_to_duckdb(con, jsonl, table_name)
        manifest["tables"][table_name] = {"source": str(jsonl), "rows": count, "type": "mart"}
        print(f"    {table_name}: {count:,}")

    # ── 4. Telemetry / system JSON reports → sys_ tables ─────────────────────
    print("  → [load] system telemetry tables …")
    sys_files = [
        (Path(clean_dir).parent / "01_metadata"  / "01_metadata.json",      "sys_01_metadata"),
        (Path(clean_dir).parent / "02_raw"        / "02_extract_report.json","sys_02_extract"),
        (Path(clean_dir)                           / "03_clean_report.json",  "sys_03_clean"),
        (Path(marts_dir)                           / "04_transform_report.json","sys_04_transform"),
    ]
    for json_path, table_name in sys_files:
        ok = _load_json_report_to_duckdb(con, json_path, table_name)
        manifest["tables"][table_name] = {
            "source": str(json_path), "type": "system", "loaded": ok
        }
        status = "OK" if ok else "MISSING"
        print(f"    {table_name}: {status}")

    # ── 5. Build schema_info helper view for the AI Agent ─────────────────────
    con.execute("""
        CREATE OR REPLACE VIEW schema_info AS
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'main'
        ORDER BY table_name, ordinal_position
    """)

    con.close()

    manifest["elapsed_s"] = round(time.perf_counter() - t0, 3)
    manifest["total_tables"] = len(manifest["tables"])

    manifest_path = out_p / "05_load_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"  → [load] {manifest['total_tables']} tables loaded in {manifest['elapsed_s']}s")
    print(f"  → [load] manifest → {manifest_path}")
    return manifest