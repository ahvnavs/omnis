import time
import json
from pathlib import Path
from src.pipeline.marts.financial import build_mart_financial_service
from src.pipeline.marts.kpi import build_mart_q1_kpi
from src.pipeline.marts.logistics import build_mart_cold_chain_leakage, build_mart_freight_cost_per_case
from src.pipeline.marts.competitor import build_mart_competitor_pricing

def run(clean_dir: str, out_dir: str) -> dict:
    t0 = time.perf_counter()
    clean_p = Path(clean_dir)
    out_p = Path(out_dir)
    out_p.mkdir(parents=True, exist_ok=True)

    report = {"marts": {}}
    steps = [
        ("mart_financial_service", build_mart_financial_service),
        ("mart_q1_kpi", build_mart_q1_kpi),
        ("mart_cold_chain_leakage", build_mart_cold_chain_leakage),
        ("mart_freight_cost_per_case", build_mart_freight_cost_per_case),
        ("mart_competitor_pricing", build_mart_competitor_pricing),
    ]

    for name, fn in steps:
        try:
            count = fn(clean_p, out_p)
            report["marts"][name] = {"rows": count, "status": "OK"}
        except Exception as exc:
            report["marts"][name] = {"rows": 0, "status": "ERROR", "error": str(exc)}

    report["elapsed_s"] = round(time.perf_counter() - t0, 3)
    (out_p / "04_transform_report.json").write_text(json.dumps(report, indent=2, default=str))
    return report
