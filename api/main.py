"""
api/main.py — FastAPI entrypoint.

    uvicorn api.main:app --reload

All routes are scoped by a workspace_id path param. This service is what
console/ calls, and what the CockroachDB MCP Server's "Interrogate the
Ledger" panel reads from.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import approvals, decisions, ledger, memories, rewind

app = FastAPI(title="Palimpsest API", version="0.1.0")

# console/ runs on a different origin during local development (Next.js
# dev server); this is a local-dev/demo API with no auth layer of its own
# (out of scope for the hackathon per CONTEXT.md's cut list), so an open
# CORS policy is acceptable here — do not deploy this service
# internet-facing without adding real authentication first.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(decisions.router)
app.include_router(memories.router)
app.include_router(approvals.router)
app.include_router(rewind.router)
app.include_router(ledger.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
