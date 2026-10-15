from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

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


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/static/{asset}", include_in_schema=False)
def static_asset(asset: str):
    path = STATIC / asset
    if not path.is_file() or path.parent != STATIC:
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(path)


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
    service.fault_mode = None if request.mode == "clear" else request.mode
    return {"mode": "FIXTURE", "fault": service.fault_mode or "clear", "allowlist": ["retry", "quarantine", "expire", "rollback", "notify"]}


@app.get("/api/verify-log")
def verify_log():
    ok, message = service.verify_log()
    return {"ok": ok, "message": message}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("deal_grandpa.api.app:app", host=os.getenv("DEAL_GRANDPA_HOST", "0.0.0.0"), port=int(os.getenv("DEAL_GRANDPA_PORT", "8000")))
