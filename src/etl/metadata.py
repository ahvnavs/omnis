import sqlite3
import time
import json
import requests
from pathlib import Path

def run(db_path: str, out_dir: str):
    t0 = time.time()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    report = {"module": "metadata_engine", "sources": {}}

    print("  -> Introspecting DB, Web, and API...")
    
    # 1. DB Metadata
    conn = sqlite3.connect(f"file:{Path(db_path).absolute()}?mode=ro", uri=True)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    db_meta = {t[0]: {"expected_rows": cursor.execute(f'SELECT COUNT(*) FROM "{t[0]}"').fetchone()[0]} for t in cursor.fetchall()}
    report["sources"]["sqlite_db"] = db_meta
    conn.close()

    # 2. Web Metadata
    try:
        resp = requests.get("http://localhost:8080/sitemap.txt", timeout=3)
        report["sources"]["bazaarpulse"] = {
            "status": "online",
            "endpoints": [line.strip() for line in resp.text.split('\n') if line.strip()]
        }
    except:
        report["sources"]["bazaarpulse"] = {"status": "offline"}

    # 3. API Metadata
    try:
        resp = requests.get("http://localhost:8088/v1/health", timeout=3)
        report["sources"]["partner_api"] = {"status": "online", "health": resp.json()}
    except:
        report["sources"]["partner_api"] = {"status": "offline"}

    with open(out_path / "01_system_metadata.json", "w") as f:
        json.dump(report, f, indent=4)
    print(f"  -> Metadata mapped in {round(time.time()-t0, 2)}s")