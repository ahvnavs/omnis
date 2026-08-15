import polars as pl
import time
import json
from pathlib import Path

def run(clean_dir: str, out_dir: str):
    t0 = time.time()
    in_path = Path(clean_dir)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    report = {"module": "transformation_engine", "marts": []}

    def load_df(name):
        f = in_path / f"{name}.jsonl"
        return pl.read_ndjson(f) if f.exists() else None

    print("  -> Generating Analytical Business Marts...")

    orders = load_df("clean_orders")
    order_lines = load_df("clean_order_lines")
    deliveries = load_df("clean_deliveries")
    invoices = load_df("clean_api_freight_invoices")

    # MART 1: True Financials (KP-2301)
    if order_lines is not None and orders is not None:
        truth_mart = order_lines.group_by("order_id").agg(
            pl.col("line_value_inr").sum().alias("true_order_value_inr"),
            pl.col("ordered_qty").sum().alias("total_eaches_ordered"),
            pl.col("delivered_qty").sum().alias("total_eaches_delivered")
        ).with_columns((pl.col("total_eaches_delivered") / pl.col("total_eaches_ordered")).alias("fill_rate_pct"))
        
        truth_mart = truth_mart.join(orders.select(["order_id", "outlet_id", "order_date", "salesperson_id"]), on="order_id", how="left")
        truth_mart.write_ndjson(out_path / "mart_financial_and_service.jsonl")
        report["marts"].append("mart_financial_and_service")

    # MART 2: Freight Leakage
    if deliveries is not None and invoices is not None and "delivery_id" in invoices.columns:
        freight_mart = deliveries.join(invoices, on="delivery_id", how="inner").select([
            "delivery_id", "route_id", "fuel_cost_inr", "amount_inr"
        ]).with_columns((pl.col("amount_inr") - pl.col("fuel_cost_inr")).alias("freight_leakage_variance_inr"))
        freight_mart.write_ndjson(out_path / "mart_freight_leakage.jsonl")
        report["marts"].append("mart_freight_leakage")

    with open(out_path / "04_transform_report.json", "w") as f:
        json.dump(report, f, indent=4)
    print(f"  -> Transformation complete in {round(time.time()-t0, 2)}s")