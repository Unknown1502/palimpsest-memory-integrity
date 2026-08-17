"""
api/routes/audit.py — serves audit/auditor.py's findings to the console.

An honesty note that matters, because it is easy to overclaim here: this
endpoint does NOT make the API self-verifying, and reading it is not the
same as independently verifying anything. It runs the same auditor the CLI
runs and hands you the result over HTTP — if the API layer were
compromised, it could lie about this response exactly as easily as it could
lie about GET /ledger/verify.

What it is for: rendering the control plane's numbers without the console
needing a database connection, and — more importantly — shipping the exact
SQL each number came from alongside the number itself, so a viewer can
leave and re-derive it. `checks[].sql` in this response is the literal
query text executed. Paste it into an MCP client pointed at the
CockroachDB Cloud Managed MCP Server and you get the same rows with
neither this API nor audit/auditor.py in the loop. That path is the one
that carries trust; this one carries convenience.

The auditor's connection is read-only at the CockroachDB session level, so
this route cannot mutate state even if something below it tried to.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from api.deps import get_dsn
from audit.auditor import CHECKS, LEDGER_SQL, METRICS_SQL, run_audit

router = APIRouter(prefix="/workspaces/{workspace_id}/audit", tags=["audit"])

_SQL_BY_CHECK = {c.name: c.sql for c in CHECKS}


@router.get("")
def get_audit(workspace_id: str, dsn: str = Depends(get_dsn)) -> dict:
    """Full audit: metrics, per-check findings, ledger chain, and the SQL behind each."""
    try:
        report = run_audit(dsn, workspace_id)
    except Exception as e:  # noqa: BLE001 — surface as a clean 400, not a 500 stack
        raise HTTPException(status_code=400, detail=f"audit failed: {type(e).__name__}: {e}")

    return jsonable_encoder(
        {
            "workspace_id": report.workspace_id,
            "passed": report.passed,
            "metrics": report.metrics,
            "ledger": {
                "valid": report.ledger_valid,
                "entries_checked": report.ledger_entries_checked,
                "broken_at_seq": report.ledger_broken_at_seq,
                "sql": LEDGER_SQL,
            },
            "checks": [
                {
                    "name": f.check,
                    "question": f.question,
                    "kind": f.kind,
                    "count": f.count,
                    "is_violation": f.is_violation,
                    # Rows are capped: this is a display surface, and an
                    # unbounded join result would be a denial-of-wallet on a
                    # public Lambda. The SQL is included so anyone who wants
                    # the full set can run it themselves.
                    "rows": f.rows[:25],
                    "truncated": f.count > 25,
                    "sql": _SQL_BY_CHECK.get(f.check),
                }
                for f in report.findings
            ],
            "errors": report.errors,
        }
    )


@router.get("/queries")
def get_queries(workspace_id: str) -> dict:
    """
    Every labeled read-only query, without running any of them.

    This is the MCP handoff in HTTP form: it needs no database access, so it
    answers even when the cluster is unreachable, and what it returns is
    exactly what `python -m audit.auditor --print-sql` prints.
    """
    return {
        "workspace_id": workspace_id,
        "note": (
            "Read-only. Run these yourself against the CockroachDB Cloud Managed MCP "
            "Server to verify Palimpsest's claims without trusting this API."
        ),
        "metrics_sql": METRICS_SQL,
        "ledger_sql": LEDGER_SQL,
        "checks": [
            {"name": c.name, "question": c.question, "kind": c.kind, "sql": c.sql}
            for c in CHECKS
        ],
    }
