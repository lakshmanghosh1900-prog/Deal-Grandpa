from __future__ import annotations

import os
from collections import deque
from pathlib import Path
from threading import Lock
from time import monotonic

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from deal_grandpa.adapters.sqlite import SQLiteStore
from deal_grandpa.orchestrator import DealGrandpaService, decision_to_dict
from deal_grandpa.schemas import FaultInput, QueryInput


ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
DB_PATH = os.getenv("DEAL_GRANDPA_DB", "var/deal_grandpa.sqlite3")
FIXTURE_PATH = os.getenv("DEAL_GRANDPA_FIXTURE", "tests/fixtures/golden_offers.json")
store = SQLiteStore(DB_PATH)
service = DealGrandpaService(store, FIXTURE_PATH)
app = FastAPI(title="Deal Grandpa", version="0.1.0")

_RATE_LIMIT_WINDOW_SECONDS = 60.0
_rate_limit_hits: dict[str, deque[float]] = {}
_rate_limit_lock = Lock()


def _rate_limit_per_minute() -> int:
    """Return a bounded per-process API request limit."""
    try:
        configured = int(os.getenv("DEAL_GRANDPA_RATE_LIMIT_PER_MINUTE", "120"))
    except ValueError:
        configured = 120
    return max(1, min(configured, 600))


@app.middleware("http")
async def api_rate_limit(request: Request, call_next):
    """Best-effort per-client IP throttle for the single-process demo runtime.

    Do not trust X-Forwarded-For here. Hosted deployments must also enforce
    rate limits at their trusted edge/reverse proxy.
    """
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    client_key = request.client.host if request.client else "unknown"
    now = monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
    with _rate_limit_lock:
        bucket = _rate_limit_hits.setdefault(client_key, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= _rate_limit_per_minute():
            retry_after = max(1, int(bucket[0] + _RATE_LIMIT_WINDOW_SECONDS - now))
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please retry later."},
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)

        if len(_rate_limit_hits) > 2048:
            for key in [key for key, values in _rate_limit_hits.items() if not values or values[-1] <= cutoff]:
                _rate_limit_hits.pop(key, None)

    return await call_next(request)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/static/{asset}", include_in_schema=False)
def static_asset(asset: str):
    path = STATIC / asset
    if not path.is_file() or path.parent != STATIC:
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(path)


def fault_injection_enabled() -> bool:
    """Fault controls are opt-in for local development, never on by default."""
    return os.getenv("DEAL_GRANDPA_ENABLE_FAULT_INJECTION", "false").strip().lower() in {"1", "true", "yes", "on"}


@app.get("/api/config")
def config():
    return {"mode": "FIXTURE", "fault_injection_enabled": fault_injection_enabled()}


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": "FIXTURE", "advisor": os.getenv("DEAL_GRANDPA_ADVISOR", "none")}


@app.post("/api/runs")
def create_run(request: QueryInput):
    try:
        decision = service.run(request.query)
        return decision_to_dict(decision)
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    row = store.run(run_id)
    if row is None or row["result_json"] is None:
        raise HTTPException(status_code=404, detail="run not found")
    import json
    return json.loads(row["result_json"])


@app.get("/api/runs/{run_id}/evidence")
def get_evidence(run_id: str):
    row = store.run(run_id)
    if row is None or row["result_json"] is None:
        raise HTTPException(status_code=404, detail="run not found")
    import json
    result = json.loads(row["result_json"])
    return {
        "run_id": run_id,
        "outcome": result["outcome"],
        "recommendation": result["recommendation"],
        "ranked": result["ranked"],
        "reasons": result["reasons"],
    }


@app.post("/api/runs/{run_id}/replay")
def replay(run_id: str):
    try:
        return service.replay(run_id, actor="api")
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/sources")
def sources():
    return {"mode": "FIXTURE", "sources": store.source_health()}


@app.post("/api/fault-injection")
def fault_injection(request: FaultInput):
    if not fault_injection_enabled():
        raise HTTPException(status_code=404, detail="Fault injection is disabled.")
    service.fault_mode = None if request.mode == "clear" else request.mode
    return {"mode": "FIXTURE", "fault": service.fault_mode or "clear", "allowlist": ["retry", "quarantine", "expire", "rollback", "notify"]}


@app.get("/api/verify-log")
def verify_log():
    ok, message = service.verify_log()
    return {"ok": ok, "message": message}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("deal_grandpa.api.app:app", host=os.getenv("DEAL_GRANDPA_HOST", "0.0.0.0"), port=int(os.getenv("DEAL_GRANDPA_PORT", "8000")))
