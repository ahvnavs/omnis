"""
main.py  —  Phase 1 Orchestrator
-----------------------------------
Runs the five ETL steps in strict order.
Each step is self-contained and reads only from its declared input directory.

Directory layout (created fresh on every run):
  data/telemetry/
    01_metadata/    ← metadata.py output
    02_raw/         ← extract.py output
    03_clean/       ← clean.py output
    04_marts/       ← transform.py output
    05_warehouse/   ← load.py output (DuckDB + manifest)

Usage:
  python src/etl/main.py              # full pipeline
  python src/etl/main.py --from clean # resume from clean step
"""
import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

# Make src importable when run directly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.pipeline.phases import metadata, extract
from src.pipeline.cleaners import orchestrator as clean
from src.pipeline.marts import orchestrator as transform
from src.pipeline import load

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("omnis")

# ── Paths ─────────────────────────────────────────────────────────────────────
# All paths are relative to the repo root (two levels up from src/etl/)
REPO_ROOT   = Path(__file__).resolve().parents[2]
DB_PATH     = REPO_ROOT / "data" / "kestrel_ops.db"
TELEMETRY   = REPO_ROOT / "data" / "telemetry"

DIRS = {
    "metadata":  TELEMETRY / "01_metadata",
    "raw":       TELEMETRY / "02_raw",
    "clean":     TELEMETRY / "03_clean",
    "marts":     TELEMETRY / "04_marts",
    "warehouse": TELEMETRY / "05_warehouse",
}

STEP_ORDER = ["metadata", "extract", "clean", "transform", "load"]


def _prepare_dirs(from_step: str) -> None:
    """Create directories. If from_step is 'metadata', nuke telemetry first."""
    if from_step == "metadata":
        if TELEMETRY.exists():
            shutil.rmtree(TELEMETRY)
        log.info("Telemetry directory wiped for a clean run.")
    for d in DIRS.values():
        d.mkdir(parents=True, exist_ok=True)


def _check_prerequisites() -> None:
    """Fail fast if the database file is missing."""
    if not DB_PATH.exists():
        log.error(f"Database not found: {DB_PATH}")
        log.error("Place kestrel_ops.db in data/ and re-run.")
        sys.exit(1)


def main(from_step: str = "metadata") -> None:
    t_total = time.perf_counter()
    _check_prerequisites()
    _prepare_dirs(from_step)

    step_idx = STEP_ORDER.index(from_step)
    log.info(f"Starting pipeline from step: {from_step}")

    # ── Step 1: Metadata ──────────────────────────────────────────────────────
    if step_idx <= STEP_ORDER.index("metadata"):
        log.info("━━ Step 1/5 · Metadata ━━")
        metadata.run(
            db_path=str(DB_PATH),
            out_dir=str(DIRS["metadata"]),
        )

    # ── Step 2: Extract ───────────────────────────────────────────────────────
    if step_idx <= STEP_ORDER.index("extract"):
        log.info("━━ Step 2/5 · Extract ━━")
        extract.run(
            db_path=str(DB_PATH),
            meta_dir=str(DIRS["metadata"]),
            out_dir=str(DIRS["raw"]),
        )

    # ── Step 3: Clean ─────────────────────────────────────────────────────────
    if step_idx <= STEP_ORDER.index("clean"):
        log.info("━━ Step 3/5 · Clean ━━")
        clean.run(
            meta_dir=str(DIRS["metadata"]),
            raw_dir=str(DIRS["raw"]),
            out_dir=str(DIRS["clean"]),
        )

    # ── Step 4: Transform ─────────────────────────────────────────────────────
    if step_idx <= STEP_ORDER.index("transform"):
        log.info("━━ Step 4/5 · Transform ━━")
        transform.run(
            clean_dir=str(DIRS["clean"]),
            out_dir=str(DIRS["marts"]),
        )

    # ── Step 5: Load ──────────────────────────────────────────────────────────
    if step_idx <= STEP_ORDER.index("load"):
        log.info("━━ Step 5/5 · Load ━━")
        load.run(
            clean_dir=str(DIRS["clean"]),
            marts_dir=str(DIRS["marts"]),
            out_dir=str(DIRS["warehouse"]),
        )

    elapsed = round(time.perf_counter() - t_total, 1)
    log.info(f"━━ Pipeline complete in {elapsed}s ━━")
    log.info(f"   Warehouse → {DIRS['warehouse'] / 'omnis_warehouse.duckdb'}")
    log.info(f"   Manifest  → {DIRS['warehouse'] / '05_load_manifest.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Omnis ETL pipeline")
    parser.add_argument(
        "--from",
        dest="from_step",
        choices=STEP_ORDER,
        default="metadata",
        help="Resume pipeline from this step (default: metadata = full run)",
    )
    args = parser.parse_args()
    main(from_step=args.from_step)