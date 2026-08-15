"""
api/main.py — FastAPI entrypoint.

    uvicorn api.main:app --reload

All routes are scoped by a workspace_id path param. This service is what
console/ calls, and what the CockroachDB MCP Server's "Interrogate the
Ledger" panel reads from.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import approvals, decisions, ledger, memories, rewind

logger = logging.getLogger("palimpsest.api")

app = FastAPI(title="Palimpsest API", version="0.1.0")


class UnhandledExceptionMiddleware:
    """
    Converts an unhandled exception into a plain JSON 500, INSIDE the
    middleware stack rather than letting it fall through to Starlette's
    ServerErrorMiddleware.

    `@app.exception_handler(Exception)` looks like the obvious fix, but
    isn't one — confirmed by reading Starlette's own source
    (starlette/applications.py, build_middleware_stack): it special-cases
    the literal `Exception` class (and status 500) to install the handler
    on ServerErrorMiddleware, which FastAPI always adds as the ABSOLUTE
    outermost layer — outside CORSMiddleware, regardless of registration
    order. A response built there is sent via the raw, unwrapped ASGI
    `send`, so it never passes back through CORSMiddleware's header
    injection.

    A plain, raw ASGI middleware (this class — `__call__(scope, receive,
    send)`, not a `BaseHTTPMiddleware` subclass) doesn't have that
    problem: it's just another ordinary layer. Registered BEFORE
    CORSMiddleware below (Starlette's `add_middleware` prepends, so
    whichever is added LAST ends up OUTERMOST) — CORSMiddleware wraps
    AROUND this one, so a response built here still passes through
    CORSMiddleware's wrapped `send` and gets proper CORS headers. Verified
    with curl against a genuinely fresh server process: before this fix, a
    real unhandled AccessDeniedException (from agent/bedrock_client.py)
    produced a 500 with no Access-Control-Allow-Origin header, which a
    browser's fetch() reports as an opaque "TypeError: Failed to fetch"
    rather than a readable error; after, the same failure returns a clean
    JSON body with the header present.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, this IS the catch-all
            logger.exception("unhandled exception on %s %s", scope.get("method"), scope.get("path"))
            response = JSONResponse(status_code=500, content={"detail": str(exc)})
            await response(scope, receive, send)


# Order matters: added first, so CORSMiddleware (added second, below) wraps
# AROUND this one, not the other way around. See the class docstring above.
app.add_middleware(UnhandledExceptionMiddleware)

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
