import polars as pl
import time
import json
from pathlib import Path

def run(meta_dir: str, raw_dir: str, out_dir: str):
    t0 = time.time()
    raw_path = Path(raw_dir)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    report = {"module": "sanitization_engine", "actions_taken": []}

    print("  -> Sanitizing Data & Salvaging Defects...")

    def load_df(name):
        f = raw_path / f"{name}.jsonl"
        if not f.exists() or f.stat().st_size == 0:
            return pl.DataFrame()
        try:
            return pl.read_ndjson(f, infer_schema_length=None)
        except Exception:
            return pl.DataFrame()

    # --- 1. CLEAN DB DATA ---
    outlets = load_df("db_outlets")
    if not outlets.is_empty():
        mask_valid = (pl.col("is_deleted") == 0) & (pl.col("status").str.to_uppercase() != "CLOSED") & (~pl.col("outlet_name").str.to_lowercase().str.contains("test|migration"))
        outlets.filter(mask_valid).unique("outlet_code", keep="first").write_ndjson(out_path / "clean_outlets.jsonl")
        outlets.filter(~mask_valid).write_ndjson(out_path / "salvaged_outlets.jsonl")
        report["actions_taken"].append("KP-2377/2211: Bad outlets partitioned to salvaged view.")

    order_lines = load_df("db_order_lines")
    if not order_lines.is_empty():
        if "qty_uom" in order_lines.columns:
            order_lines = order_lines.with_columns(
                pl.when(pl.col("qty_uom").is_in(["G", "ML"])).then(pl.col("ordered_qty") / 1000)
                .otherwise(pl.col("ordered_qty")).alias("ordered_qty_std")
            )
        order_lines.write_ndjson(out_path / "clean_order_lines.jsonl")

    returns = load_df("db_returns_credit_notes")
    if not returns.is_empty():
        if "return_qty" in returns.columns:
            returns = returns.with_columns(pl.col("return_qty").abs())
        returns.write_ndjson(out_path / "clean_returns.jsonl")

    for tbl in ["orders", "deliveries", "products", "warehouses", "routes", "regions", "salespeople", "promotions"]:
        df = load_df(f"db_{tbl}")
        if not df.is_empty(): df.write_ndjson(out_path / f"clean_{tbl}.jsonl")

    # --- 2. CLEAN WEB DATA ---
    web_data = load_df("web_competitor_prices")
    if not web_data.is_empty():
        web_data = web_data.with_columns(
            pl.col("raw_text_block").str.extract(r"(?:₹|Rs\.?)\s*([\d\,\.]+)").str.replace(",","").cast(pl.Float64, strict=False).alias("clean_price_inr")
        ).drop_nulls(subset=["clean_price_inr"])
        web_data.write_ndjson(out_path / "clean_competitor_prices.jsonl")
        report["actions_taken"].append("Web: Extracted pure INR float prices using Regex. Handled dirty cast errors gracefully.")

    # --- 3. CLEAN API DATA ---
    for api_tbl in ["api_freight_invoices", "api_carriers", "api_fuel_surcharge", "api_shipment_events"]:
        api_data = load_df(api_tbl)
        if not api_data.is_empty():
            if "amount_paise" in api_data.columns:
                api_data = api_data.with_columns((pl.col("amount_paise") / 100).alias("amount_inr"))
            api_data.write_ndjson(out_path / f"clean_{api_tbl}.jsonl")

    with open(out_path / "03_clean_report.json", "w") as f:
        json.dump(report, f, indent=4)
    print(f"  -> Sanitization complete in {round(time.time()-t0, 2)}s")