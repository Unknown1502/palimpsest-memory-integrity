"""
api/main.py — FastAPI entrypoint.

    uvicorn api.main:app --reload

All routes are scoped by a workspace_id path param. This service is what
console/ calls, and what the CockroachDB MCP Server's "Interrogate the
Ledger" panel reads from.
"""

from __future__ import annotations

import logging
import os
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import approvals, decisions, ledger, memories, rewind

logger = logging.getLogger("palimpsest.api")

app = FastAPI(title="Palimpsest API", version="0.1.0")

READONLY = os.environ.get("PALIMPSEST_READONLY", "false").strip().lower() in ("1", "true", "yes")

# Routes blocked when PALIMPSEST_READONLY=true. Chosen by what a call
# actually COSTS or DESTROYS, not by HTTP verb -- POST /rewind is
# deliberately still allowed because it only computes a preview
# (belief diff + blast radius) and writes one inert row: no LLM calls, no
# state change to any belief. That preview is the whole visual argument of
# the project, so a read-only deployment should still show it.
_READONLY_BLOCKED = (
    re.compile(r"/memories/[^/]+/revoke/?$"),    # destroys belief state
    re.compile(r"/rewind/[^/]+/apply/?$"),       # replays decisions -> real LLM spend
    re.compile(r"/approvals/[^/]+/resolve/?$"),  # mutates the approval queue
)


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


class ReadOnlyMiddleware:
    """
    Blocks cost-incurring and destructive routes when PALIMPSEST_READONLY
    is set. Exists because the public demo deployment's Function URL has
    no authentication of its own: without this, anyone with the URL could
    revoke beliefs or drive `/rewind/apply` in a loop, which makes real
    LLM calls against a real, metered API key.

    Raw ASGI rather than BaseHTTPMiddleware for the same reason
    UnhandledExceptionMiddleware is (see below): it must sit INSIDE
    CORSMiddleware so the 403 it returns still carries CORS headers, or
    the console reports it to the user as an opaque network failure
    instead of a readable "disabled on the public demo" message.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not READONLY:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if scope.get("method") in ("POST", "PUT", "PATCH", "DELETE") and any(
            p.search(path) for p in _READONLY_BLOCKED
        ):
            logger.info("readonly: blocked %s %s", scope.get("method"), path)
            response = JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "This endpoint is disabled on the public read-only demo deployment "
                        "because it destroys belief state or makes metered LLM calls. "
                        "Clone the repo and run it locally to exercise the full write path — "
                        "see docs/SETUP.md."
                    ),
                    "readonly": True,
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


# Order matters: added first, so CORSMiddleware (added last, below) wraps
# AROUND these, not the other way around. Starlette's add_middleware
# PREPENDS, so whichever is added last ends up outermost.
app.add_middleware(UnhandledExceptionMiddleware)
app.add_middleware(ReadOnlyMiddleware)

# console/ runs on a different origin during local development (Next.js
# dev server), so CORS is open. This service has no authentication layer
# of its own — deliberately out of scope, and the reason the public
# deployment runs with PALIMPSEST_READONLY=true (see ReadOnlyMiddleware
# above). Do not deploy it internet-facing with writes enabled until real
# authentication exists; read-only is a mitigation, not a substitute.
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
