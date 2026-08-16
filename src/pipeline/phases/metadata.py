"""
metadata.py  —  Phase 1, Step 1
--------------------------------
Introspects all three sources (SQLite DB, BazaarPulse site, Partner API)
and writes a single 01_metadata.json to the telemetry directory.

This JSON file is the contract every downstream step trusts. It records:
  - DB: table names, row counts, column names + types
  - Web: sitemap entries, robots crawl-delay, server reachability
  - API: health status, known endpoints, chaos behaviour note
"""
import json
import sqlite3
import time
import requests
from datetime import datetime, timezone
from pathlib import Path


API_BASE   = "http://localhost:8088"
WEB_BASE   = "http://localhost:8080"
API_KEY    = "kp_live_7f3a9c21"
API_HEADER = {"X-API-Key": API_KEY}


def _probe_db(db_path: str) -> dict:
    """Read every table: name, row count, columns (name + declared type)."""
    path = Path(db_path)
    if not path.exists():
        return {"error": f"DB not found at {db_path}", "tables": {}}

    conn = sqlite3.connect(f"file:{path.absolute()}?mode=ro", uri=True)
    cur  = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    tables = [r[0] for r in cur.fetchall()]

    meta = {}
    for tbl in tables:
        row_count = cur.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
        cur.execute(f'PRAGMA table_info("{tbl}")')
        cols = [{"name": r[1], "type": r[2] or "TEXT"} for r in cur.fetchall()]
        meta[tbl] = {"row_count": row_count, "columns": cols}

    conn.close()
    return {"path": str(path.absolute()), "tables": meta}


def _probe_web(web_base: str) -> dict:
    """Read robots.txt and sitemap.txt; record reachability and crawl rules."""
    result = {"base_url": web_base, "reachable": False,
              "crawl_delay_seconds": 1, "disallowed_paths": [], "sitemap_entries": []}
    try:
        robots_resp = requests.get(f"{web_base}/robots.txt", timeout=5)
        robots_resp.raise_for_status()
        result["reachable"] = True
        for line in robots_resp.text.splitlines():
            line = line.strip()
            if line.lower().startswith("crawl-delay:"):
                try:
                    result["crawl_delay_seconds"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.lower().startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path:
                    result["disallowed_paths"].append(path)

        sitemap_resp = requests.get(f"{web_base}/sitemap.txt", timeout=5)
        sitemap_resp.raise_for_status()
        result["sitemap_entries"] = [
            ln.strip() for ln in sitemap_resp.text.splitlines() if ln.strip()
        ]
    except Exception as exc:
        result["error"] = str(exc)

    return result


def _probe_api(api_base: str, headers: dict) -> dict:
    """Hit /v1/health and /v1/carriers to confirm API is live and get carrier list."""
    result = {"base_url": api_base, "reachable": False, "carriers": [], "endpoints": [
        "/v1/health", "/v1/carriers",
        "/v1/freight_invoices", "/v1/shipment_events", "/v1/fuel_surcharge"
    ]}
    try:
        health = requests.get(f"{api_base}/v1/health", timeout=5)
        health.raise_for_status()
        result["reachable"]    = True
        result["health"]       = health.json()

        carriers = requests.get(f"{api_base}/v1/carriers", headers=headers, timeout=5)
        carriers.raise_for_status()
        result["carriers"] = carriers.json().get("data", [])
    except Exception as exc:
        result["error"] = str(exc)

    return result


def run(db_path: str, out_dir: str) -> dict:
    t0 = time.perf_counter()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print("  → [metadata] probing DB …")
    db_meta  = _probe_db(db_path)

    print("  → [metadata] probing BazaarPulse …")
    web_meta = _probe_web(WEB_BASE)

    print("  → [metadata] probing Partner API …")
    api_meta = _probe_api(API_BASE, API_HEADER)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(time.perf_counter() - t0, 3),
        "db":  db_meta,
        "web": web_meta,
        "api": api_meta,
    }

    out_file = out / "01_metadata.json"
    out_file.write_text(json.dumps(report, indent=2, default=str))
    print(f"  → [metadata] done in {report['elapsed_s']}s → {out_file}")
    return report