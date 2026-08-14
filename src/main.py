import os
import logging
import shutil
from pathlib import Path
from dotenv import load_dotenv
from etl import metadata, extract, clean, transform, load

logging.basicConfig(level=logging.INFO, format="%(asctime)s - omnis - %(message)s")
logger = logging.getLogger(__name__)

def setup_telemetry_environment():
    base_dir = Path("data/telemetry")
    base_dir.mkdir(parents=True, exist_ok=True)
    
    # Wipe old root-level JSON reports to prevent stale files like old load_manifest.json
    for old_file in base_dir.glob("*.json"):
        old_file.unlink()
        
    # Clean and recreate stage subdirectories
    for d in ["02_raw", "03_clean", "04_marts"]:
        dir_path = base_dir / d
        if dir_path.exists():
            shutil.rmtree(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        (dir_path / ".keep").touch(exist_ok=True)
        
    logger.info("Telemetry environment successfully purged and initialized.")

def main():
    logger.info("Initializing Omnis Polars ETL Pipeline...")
    load_dotenv()
    setup_telemetry_environment()
    
    db_path = "data/kestrel_ops.db"
    if not Path(db_path).exists():
        logger.error(f"Source database not found at {db_path}. Please supply the database.")
        return

    logger.info("Phase 1: Generating Metadata")
    metadata.run(db_path, out_path="data/telemetry/01_metadata.json")

    logger.info("Phase 2: Extracting to Raw JSONL")
    extract.run(db_path, metadata_path="data/telemetry/01_metadata.json", out_dir="data/telemetry/02_raw", report_path="data/telemetry/02_extract_report.json")

    logger.info("Phase 3: Cleaning & Sanitization")
    clean.run(raw_dir="data/telemetry/02_raw", clean_dir="data/telemetry/03_clean", report_path="data/telemetry/03_clean_report.json")

    logger.info("Phase 4: Transforming into Marts")
    transform.run(clean_dir="data/telemetry/03_clean", marts_dir="data/telemetry/04_marts", report_path="data/telemetry/04_transform_report.json")

    logger.info("Phase 5: Loading to DuckDB")
    load.run(marts_dir="data/telemetry/04_marts", manifest_path="data/telemetry/05_load_manifest.json", duckdb_path="data/warehouse.duckdb")

    logger.info("Omnis Pipeline execution complete. All stages and telemetry reports updated successfully.")

if __name__ == "__main__":
    main()