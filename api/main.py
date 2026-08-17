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
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from api import landing
from api.routes import approvals, audit, decisions, ledger, memories, rewind, simulate

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
app.include_router(audit.router)
app.include_router(simulate.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "readonly": READONLY}


@app.get("/demo-workspace", include_in_schema=False)
def demo_workspace() -> dict:
    """
    The workspace the console should show when the visitor hasn't chosen one.

    Without this, a first-time visitor lands on an empty console reading
    "No workspace selected. Run python -m demo.seed and paste the printed
    workspace_id" -- instructions that are correct for a developer running
    locally and useless to someone who just opened the public demo link.
    Returning the most interesting seeded workspace lets the console show
    real data on first paint with nothing to paste.

    Picks by decision count, not recency: the newest workspace is often a
    bare seed with one memory and no decisions, which looks broken.
    """
    workspace_id, counts = landing.pick_demo_workspace(os.environ.get("PALIMPSEST_DSN", ""))
    return {"workspace_id": workspace_id, **counts}


# ---------------------------------------------------------------------------
# The console (Next.js static export), served from this same app, so the
# deployed demo is ONE origin: the UI and the API it calls. That also means
# the console's fetches are same-origin and CORS never applies to them.
#
# Registered LAST so every API route above matches first; only unmatched
# paths fall through to the static resolver. Absent (an API-only run, or
# before `npm run build`), "/" degrades to the API landing page rather than
# failing at startup.
#
# Deliberately NOT StaticFiles(html=True) mounted at "/". That redirects a
# directory request to the trailing-slash form, and behind Mangum the path
# StaticFiles sees doesn't match what the client sent, so it emits a
# redirect to the URL already requested -- /timeline/ -> /timeline/, an
# infinite loop. It only reproduces on Lambda: plain uvicorn serves the same
# routes 200/text/html, so this has to be checked against the deployed URL,
# not just locally. Resolving paths to files explicitly here avoids the
# redirect machinery altogether: every response is a file or a 404.
# ---------------------------------------------------------------------------
_CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console" / "out"

_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json",
}


def _resolve_console_file(url_path: str) -> Path | None:
    """
    Map a URL path to a file in the static export, or None.

    Tries, in order: the literal file, `<path>/index.html` (how Next.js
    emits routes under trailingSlash), and `<path>.html`.

    The candidate is resolved and re-checked against the export directory
    before returning, so `..` segments or an absolute path can't escape it
    into the rest of the Lambda package.
    """
    relative = url_path.strip("/")
    base = _CONSOLE_DIR.resolve()
    candidates = (
        (base / relative) if relative else (base / "index.html"),
        (base / relative / "index.html") if relative else None,
        (base / f"{relative}.html") if relative else None,
    )
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.is_file():
            continue
        if not resolved.is_relative_to(base):  # path-traversal guard
            logger.warning("console path escaped the export directory: %s", url_path)
            continue
        return resolved
    return None


if _CONSOLE_DIR.is_dir():
    logger.info("serving console static export from %s", _CONSOLE_DIR)

    # Immutable, content-hashed assets get a long max-age; HTML must not be
    # cached, or a redeploy leaves visitors on a stale page referencing
    # chunk files that no longer exist.
    @app.get("/{console_path:path}", include_in_schema=False)
    def console(console_path: str) -> FileResponse:
        target = _resolve_console_file(console_path)
        if target is None:
            not_found = _resolve_console_file("404")
            if not_found is None:
                raise HTTPException(status_code=404, detail="Not Found")
            return FileResponse(not_found, status_code=404, media_type="text/html; charset=utf-8")
        media_type = _MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
        immutable = console_path.startswith("_next/static/")
        return FileResponse(
            target,
            media_type=media_type,
            headers={
                "Cache-Control": (
                    "public, max-age=31536000, immutable" if immutable else "no-cache"
                )
            },
        )

else:
    logger.info("no console build at %s -- serving the API landing page at /", _CONSOLE_DIR)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(landing.render(dsn=os.environ.get("PALIMPSEST_DSN", ""), readonly=READONLY))
