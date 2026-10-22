"""HTTP API: POST /agent runs the agent; GET /documents/{run_id} downloads the memo; GET / is the demo page."""

import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .docx_writer import OUT, unique
from .graph import run
from .schemas import AgentRequest, AgentResponse

app = FastAPI(title="Credit memo agent")
INDEX = Path(__file__).parent / "static" / "index.html"


@app.post("/agent", response_model=AgentResponse)
def agent(body: AgentRequest) -> AgentResponse:
    run_id, t = uuid.uuid4().hex[:12], time.time()
    try:
        s = run(body.request, run_id)
    except Exception as e:  # both models down, or a bug: report it, don't 500
        return AgentResponse(run_id=run_id, status="failed", error=f"{type(e).__name__}: {str(e)[:300]}",
                             total_ms=int((time.time() - t) * 1000))
    plan = s.get("plan")
    flags = list({(f["level"], f["text"]): f for f in s["flags"]}.values())
    return AgentResponse(
        run_id=run_id,
        status="failed" if s.get("error") else "completed",
        error=s.get("error"),
        borrower=(s.get("data") or {}).get("profile", {}).get("name") or (plan.borrower if plan else None),
        recommendation=(s.get("data") or {}).get("recommendation"),
        plan_attempts=s.get("plan_attempts", 0),
        tasks=s["task_log"],
        assumptions=plan.assumptions if plan else [],
        flags=flags,
        open_questions=unique(s["open_questions"]),
        check=s.get("check"),
        document_url=f"/documents/{run_id}" if s.get("doc_path") else None,
        llm_calls=s.get("llm_calls", 0),
        total_ms=int((time.time() - t) * 1000),
    )


@app.get("/documents/{run_id}")
def document(run_id: str):
    path = OUT / f"{run_id}.docx"
    if not re.fullmatch(r"[a-z0-9-]{1,40}", run_id) or not path.exists():
        raise HTTPException(404, "no such document")
    return FileResponse(path, filename=f"credit-memo-{run_id}.docx",
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(INDEX)
