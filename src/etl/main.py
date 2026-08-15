import os
import sys
import logging
import shutil
from pathlib import Path
from dotenv import load_dotenv
from load import load_telemetry_to_duckdb

sys.path.append(str(Path(__file__).parent))

import metadata, extract, clean, transform, load

logging.basicConfig(level=logging.INFO, format="%(asctime)s - omnis - %(message)s")
logger = logging.getLogger(__name__)

def setup_telemetry_environment():
    base_dir = Path("data/telemetry")
    if base_dir.exists():
        shutil.rmtree(base_dir) # Nuke and pave for a perfect run
        
    directories = ["01_metadata", "02_raw", "03_clean", "04_marts", "05_warehouse"]
    for d in directories:
        (base_dir / d).mkdir(parents=True, exist_ok=True)
        (base_dir / d / ".keep").touch(exist_ok=True)

def main():
    logger.info("Initializing Omnis 3.0: Stage-Oriented Data Platform...")
    load_dotenv()
    setup_telemetry_environment()
    
    db_path = "data/kestrel_ops.db"
    
    logger.info("--- Phase 1: Metadata Introspection ---")
    metadata.run(db_path, out_dir="data/telemetry/01_metadata")
    
    logger.info("--- Phase 2: Complete Extraction ---")
    extract.run(db_path, meta_dir="data/telemetry/01_metadata", out_dir="data/telemetry/02_raw")
    
    logger.info("--- Phase 3: Sanitization & Salvage ---")
    clean.run(meta_dir="data/telemetry/01_metadata", raw_dir="data/telemetry/02_raw", out_dir="data/telemetry/03_clean")
    
    logger.info("--- Phase 4: Business Transformation ---")
    transform.run(clean_dir="data/telemetry/03_clean", out_dir="data/telemetry/04_marts")
    
    logger.info("--- Phase 5: DuckDB Warehouse Load ---")
    load.run(clean_dir="data/telemetry/03_clean", marts_dir="data/telemetry/04_marts", out_dir="data/telemetry/05_warehouse")
    
    logger.info("--- Phase 5.5: System Context Injection ---")
    logger.info("  -> Pushing JSON telemetry and metadata into DuckDB system tables...")
    load_telemetry_to_duckdb()
    logger.info("  -> AI Context layer injected successfully.")
    
    logger.info("Pipeline Complete. DuckDB Single Source of Truth is live.")

if __name__ == "__main__":
    main()