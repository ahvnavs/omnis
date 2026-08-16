import json
import math
import os
import time
from pathlib import Path

import psutil
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.ai.orchestration import ask_agent_stream

def _sanitize(obj):
    """Recursively replace NaN/Inf floats with None for JSON safety."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj

REPO_ROOT  = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "src" / "web"
DASH_JSON  = REPO_ROOT / ".ai_cache" / "dashboard.json"
AGENT_LOG  = REPO_ROOT / "agent.log"

STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Omnis Control Tower", version="3.0.0")
app.mount("/ui", StaticFiles(directory=str(STATIC_DIR)), name="ui")


class ChatRequest(BaseModel):
    query: str
    bypass_cache: bool = False
    hide_thinking: bool = False


@app.get("/")
def read_root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/chat")
def chat(request: ChatRequest):
    return StreamingResponse(
        ask_agent_stream(request.query, request.bypass_cache, request.hide_thinking),
        media_type="text/event-stream",
    )


@app.get("/api/dashboard")
def api_dashboard():
    """Return pre-warmed dashboard JSON. Served from disk cache — instant."""
    if DASH_JSON.exists():
        raw = json.loads(DASH_JSON.read_text())
        return JSONResponse(content=_sanitize(raw))
    return JSONResponse(content={"error": "Dashboard cache not found. Run make engine."}, status_code=503)


@app.get("/api/perf")
def api_perf():
    """Return agent log tail + system resource usage."""
    log_lines = []
    if AGENT_LOG.exists():
        with open(AGENT_LOG) as f:
            log_lines = f.readlines()[-60:]

    proc = psutil.Process(os.getpid())
    mem_mb = round(proc.memory_info().rss / 1024 / 1024, 1)

    return JSONResponse(content={
        "log": "".join(log_lines),
        "system": {
            "cpu_pct": psutil.cpu_percent(interval=0.2),
            "mem_total_mb": round(psutil.virtual_memory().total / 1024 / 1024),
            "mem_used_mb":  round(psutil.virtual_memory().used  / 1024 / 1024),
            "mem_pct":      psutil.virtual_memory().percent,
            "process_mem_mb": mem_mb,
        },
        "ts": time.time(),
    })
