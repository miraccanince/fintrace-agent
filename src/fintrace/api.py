import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fintrace import agent, banking_db, ollama_adapter, tools
from fintrace.compare import compare_datasets, parse_amount

app = FastAPI()
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")

# Path to the synthetic banking database read by /api/loans/{loan_id}/comparison.
# Generate one with fintrace.synthetic_data.generate_database_with_scenarios.
_DB_PATH = os.environ.get("FINTRACE_DB", "banking.db")


class Loan(BaseModel):
    loan_id: str | None = None
    outstanding_amount: Any = None
    currency: str | None = None


class CompareRequest(BaseModel):
    system_a: list[Loan]
    system_b: list[Loan]


def _loan_to_dict(loan: Loan) -> dict:
    data = loan.model_dump(exclude_none=True)
    if "outstanding_amount" in data:
        data["outstanding_amount"] = parse_amount(data["outstanding_amount"])
    return data


@app.post("/compare")
def compare(request: CompareRequest) -> list[dict]:
    system_a = [_loan_to_dict(loan) for loan in request.system_a]
    system_b = [_loan_to_dict(loan) for loan in request.system_b]
    results = compare_datasets(system_a, system_b)

    for result in results:
        if isinstance(result.get("difference"), Decimal):
            result["difference"] = str(result["difference"])

    return results


@app.get("/api/loans/{loan_id}/comparison")
def loan_comparison(loan_id: str) -> dict:
    """Deterministic comparison of a loan's latest balance snapshots across
    systems - no AI investigation. Reuses the read-only compare_latest_snapshots
    tool, so this performs no business calculation of its own."""
    try:
        connection = banking_db.connect_readonly(_DB_PATH)
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"database unavailable: {error}") from error
    try:
        return tools.make_executor(connection)("compare_latest_snapshots", {"loan_id": loan_id})
    finally:
        connection.close()


_investigation_lock = threading.Lock()  # one local model run at a time per process
_investigation_worker = ThreadPoolExecutor(max_workers=1)
_KEEPALIVE_SECONDS = 10  # a run takes minutes; browsers drop a request that stays silent that long


def get_agent_call_model():
    return ollama_adapter.generate_with_tools


@app.post("/api/loans/{loan_id}/investigation")
def loan_investigation(loan_id: str, call_model=Depends(get_agent_call_model)) -> Any:
    """Run the Ollama agent on one loan through the MCP tool server and return the
    harness report as JSON. Any stop reason other than `final_answer` is an incomplete
    investigation, reported as a normal result, not an HTTP error.

    The run is started before the response, and the body is padded with newlines
    (valid JSON whitespace) until the report is ready, so the connection stays alive.
    It keeps running, and holds the lock, even if the client goes away."""
    try:
        connection = banking_db.connect_readonly(_DB_PATH)
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"database unavailable: {error}") from error
    try:
        found = tools.make_executor(connection)("compare_latest_snapshots", {"loan_id": loan_id}).get("found")
    finally:
        connection.close()
    if not found:
        return {"loan_id": loan_id, "found": False}
    if not _investigation_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="another investigation is already running")

    def run() -> dict:
        try:
            run_connection = banking_db.connect_readonly(_DB_PATH)  # SQLite connections stay in their own thread
            try:
                return agent.investigate(loan_id, run_connection, call_model, mcp_db_path=_DB_PATH)
            finally:
                run_connection.close()
        except Exception as error:
            return {"server_error": f"{type(error).__name__}: {error}"}
        finally:
            _investigation_lock.release()

    pending = _investigation_worker.submit(run)

    async def body():
        waiting = asyncio.wrap_future(pending)
        while not (await asyncio.wait({waiting}, timeout=_KEEPALIVE_SECONDS))[0]:
            yield b"\n"
        yield json.dumps(pending.result()).encode()

    return StreamingResponse(body(), media_type="application/json")
