import polars as pl
import time
import json
from pathlib import Path

def run(clean_dir: str = "data/telemetry/03_clean", marts_dir: str = "data/telemetry/04_marts", report_path: str = "data/telemetry/04_transform_report.json"):
    print("Initiating Business Logic Mart Transformation...")
    t0 = time.time()
    
    marts_path = Path(marts_dir)
    marts_path.mkdir(parents=True, exist_ok=True)

    telemetry = {"module": "transformation_engine", "marts_generated": {}}

    def load_clean_df(table_name: str):
        f = Path(clean_dir) / f"{table_name}.jsonl"
        return pl.read_ndjson(f, infer_schema_length=None) if f.exists() else None

    order_lines = load_clean_df("order_lines")
    deliveries = load_clean_df("deliveries")
    returns = load_clean_df("returns_credit_notes")
    products = load_clean_df("products")

    # Mart 1: Fill Rate
    if order_lines is not None:
        if "ordered_qty" in order_lines.columns and "delivered_qty" in order_lines.columns:
            fill_rate_mart = order_lines.group_by("order_id").agg(
                pl.col("ordered_qty").sum().alias("ordered_qty"),
                pl.col("delivered_qty").sum().alias("delivered_qty")
            ).with_columns(
                (pl.col("delivered_qty") / pl.col("ordered_qty")).alias("fill_rate_pct")
            )
            out_file = marts_path / "fill_rate.jsonl"
            if out_file.exists(): out_file.unlink()
            fill_rate_mart.write_ndjson(out_file)
            telemetry["marts_generated"]["fill_rate"] = {"row_count": fill_rate_mart.height, "status": "success"}
            print("  -> Mart generated: fill_rate.jsonl")

    # Mart 2: Freight Leakage
    if deliveries is not None:
        freight_mart = deliveries.select([
            c for c in ["delivery_id", "route_id", "vehicle_registration", "delivery_status", "delay_minutes"] 
            if c in deliveries.columns
        ])
        out_file = marts_path / "freight_leakage.jsonl"
        if out_file.exists(): out_file.unlink()
        freight_mart.write_ndjson(out_file)
        telemetry["marts_generated"]["freight_leakage"] = {"row_count": freight_mart.height, "status": "success"}
        print("  -> Mart generated: freight_leakage.jsonl")

    # Mart 3: Returns Leakage
    if returns is not None and products is not None:
        if "product_id" in returns.columns and "credit_note_value_inr" in returns.columns:
            returns_mart = returns.join(
                products.select(["product_id", "category"]),
                on="product_id",
                how="left"
            ).group_by(["category", "return_reason_code"]).agg(
                pl.col("credit_note_value_inr").sum().alias("total_return_value_inr"),
                pl.col("return_qty").sum().alias("total_return_qty")
            )
            out_file = marts_path / "returns_leakage.jsonl"
            if out_file.exists(): out_file.unlink()
            returns_mart.write_ndjson(out_file)
            telemetry["marts_generated"]["returns_leakage"] = {"row_count": returns_mart.height, "status": "success"}
            print("  -> Mart generated: returns_leakage.jsonl")

    telemetry["status"] = "completed"
    telemetry["execution_time_seconds"] = round(time.time() - t0, 3)
    
    rep_file = Path(report_path)
    if rep_file.exists():
        rep_file.unlink()

    with open(rep_file, "w") as f:
        json.dump(telemetry, f, indent=4)