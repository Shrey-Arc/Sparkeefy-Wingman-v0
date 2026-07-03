"""
backend.py
----------
Thin FastAPI wrapper around wingman.get_wingman_response(). Holds no business
logic of its own — its only jobs are request validation (via schemas.ChatRequest),
calling the service, and shaping the HTTP response.

Run:
    export DEEPSEEK_API_KEY="sk-..."
    uvicorn backend:app --reload --port 8000

Without an API key set, every request automatically falls back to mock mode
so the API is still explorable via /docs.
"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from schemas import ChatRequest, MemoryCandidate
from wingman import get_wingman_response, get_api_status
from memory import MemoryManager
import cache
import pricing_calendar

app = FastAPI(title="Sparkeefy AI Wingman v0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # fine for local v0 demo; tighten before any real deployment
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

memory_manager = MemoryManager()


@app.get("/health")
def health():
    """
    Reports both whether a key is configured AND whether the last real call
    (if any) actually succeeded — 'a key is set' and 'the API is reachable'
    are different facts and this endpoint doesn't conflate them.
    """
    api_key_present = bool(os.environ.get("DEEPSEEK_API_KEY"))
    return {
        "status": "ok",
        "api_key_configured": api_key_present,
        "last_api_call": get_api_status(),
        "cache": cache.stats(),
        "pricing": pricing_calendar.current_rate_note(),
        "storage": {
            "memory_path": os.path.abspath(memory_manager.path),
            "history_path": os.path.abspath(memory_manager.history_path),
        },
    }


@app.get("/relationships")
def list_relationships():
    return {"relationships": memory_manager.list_relationships()}


@app.post("/chat")
def chat(req: ChatRequest):
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    mock = not bool(api_key)

    result = get_wingman_response(
        relationship=req.relationship,
        message=req.message,
        api_key=api_key,
        mock=mock,
        memory_manager=memory_manager,
    )

    if not result["schema_valid"]:
        # This can now only happen if BOTH the real call and the mock
        # fallback failed to produce valid JSON — genuinely unrecoverable,
        # unlike a plain network failure, which is handled gracefully inside
        # get_wingman_response() and never reaches this branch.
        raise HTTPException(
            status_code=502,
            detail={
                "error": result.get("error", "Unknown validation failure"),
                "raw": result.get("raw"),
            },
        )

    response = {
        "output": result["output"],
        "response_time_sec": result["response_time_sec"],
        "mock_mode": mock,
        "cached": result.get("cached", False),
    }
    if result.get("warning"):
        response["warning"] = result["warning"]
    return response


@app.get("/history/{relationship}")
def get_history(relationship: str):
    """Full history with timestamps, for the UI to reload a chat on page load."""
    return {"history": memory_manager.get_full_history(relationship)}


@app.post("/history/{relationship}/clear")
def clear_history(relationship: str):
    memory_manager.clear_history(relationship)
    return {"status": "cleared", "relationship": relationship}


class ApproveCandidateRequest(MemoryCandidate):
    relationship: str


@app.post("/memory/approve")
def approve_memory_candidate(req: ApproveCandidateRequest):
    """
    Explicit user action only — this is the ONE place memory.json gets
    permanently updated from a model-suggested candidate. The model never
    writes here directly.
    """
    try:
        memory_manager.approve_candidate(req.relationship, req.category, req.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "approved", "relationship": req.relationship, "category": req.category, "value": req.value}
