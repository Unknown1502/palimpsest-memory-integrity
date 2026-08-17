"""
api/routes/simulate.py — evaluate the integrity lattice without writing.

This exists so the security boundary is demonstrable on the PUBLIC demo,
where writes are deliberately blocked. It is not a re-enactment and not an
animation: it calls memory.lattice.check_capability_allowed — the exact
function MemoryGate.admit() calls, from the same module, with no parallel
copy of the rule — and reports what it did.

Why that is safe to expose unauthenticated, and why the numbers are real:

  * The lattice check happens BEFORE admit() opens a database connection.
    That ordering is the actual security property (a rejected admission
    leaves zero trace), so evaluating the check in isolation is not a
    simplification of the real path — it IS the first step of the real
    path, stopped after the part that decides.
  * `database_writes` is MEASURED, not asserted. The handler counts rows in
    memories and memory_ledger before and after, and returns the deltas. If
    this endpoint ever did write something, the response would say so.
  * No LLM call, no embedding call, so no metered spend. A visitor holding
    the button down costs two COUNT queries per press.

What it deliberately does NOT do: admit anything, even when the lattice
would allow it. An "allowed" verdict here means "the lattice would permit
this", not "this was stored". Storing is what the write path is for, and
the public deployment does not have one.
"""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.deps import get_dsn
from memory.lattice import (
    CAPABILITY_MIN_INTEGRITY,
    CAPABILITY_TO_STR,
    INTEGRITY_BY_SOURCE,
    STR_TO_CAPABILITY,
    Capability,
    IntegrityViolation,
    check_capability_allowed,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/simulate", tags=["simulate"])


class CapabilityRequest(BaseModel):
    source_kind: str = Field(..., description="untrusted_ingest | agent_inferred | verified_tool | human_confirmed")
    capability: str = Field(..., description="informational | suppressive | actuating")
    text: str = Field("", max_length=2000, description="the claim text, echoed back for display only")


def _row_counts(dsn: str, workspace_id: str) -> tuple[int, int]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM memories WHERE workspace_id = %s", (workspace_id,))
            memories = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM memory_ledger WHERE workspace_id = %s", (workspace_id,))
            ledger = cur.fetchone()[0]
    return memories, ledger


@router.post("/capability")
def simulate_capability(
    workspace_id: str, body: CapabilityRequest, dsn: str = Depends(get_dsn)
) -> dict:
    source_kind = body.source_kind.strip()
    capability_str = body.capability.strip()

    if source_kind not in INTEGRITY_BY_SOURCE:
        return {
            "error": f"unknown source_kind {source_kind!r}",
            "valid_source_kinds": sorted(INTEGRITY_BY_SOURCE),
        }
    if capability_str not in STR_TO_CAPABILITY:
        return {
            "error": f"unknown capability {capability_str!r}",
            "valid_capabilities": sorted(STR_TO_CAPABILITY),
        }

    integrity = INTEGRITY_BY_SOURCE[source_kind]
    capability: Capability = STR_TO_CAPABILITY[capability_str]
    required = CAPABILITY_MIN_INTEGRITY[capability]

    before = _row_counts(dsn, workspace_id)

    allowed = True
    error: str | None = None
    try:
        check_capability_allowed(integrity, capability)
    except IntegrityViolation as e:
        allowed = False
        error = str(e)

    after = _row_counts(dsn, workspace_id)

    return {
        "text": body.text[:2000],
        "source_kind": source_kind,
        "integrity_level": int(integrity),
        "integrity_name": integrity.name.lower(),
        "requested_capability": CAPABILITY_TO_STR[capability],
        "required_integrity_level": int(required),
        "required_integrity_name": required.name.lower(),
        "allowed": allowed,
        "result": "ALLOWED" if allowed else "BLOCKED",
        "error_type": None if allowed else "IntegrityViolation",
        "error": error,
        # Measured, not claimed. See the module docstring.
        "database_writes": {
            "memories": after[0] - before[0],
            "memory_ledger": after[1] - before[1],
        },
        "note": (
            "The lattice would permit this. Nothing was stored — this endpoint never "
            "admits, on any verdict."
            if allowed
            else "Rejected by memory/lattice.py before admit() opens a database connection."
        ),
    }


@router.get("/lattice")
def lattice_table() -> dict:
    """The whole policy as data, so a UI never hard-codes a second copy of it."""
    return {
        "sources": [
            {"source_kind": k, "integrity_level": int(v), "name": v.name.lower()}
            for k, v in sorted(INTEGRITY_BY_SOURCE.items(), key=lambda kv: int(kv[1]))
        ],
        "capabilities": [
            {
                "capability": CAPABILITY_TO_STR[c],
                "min_integrity_level": int(CAPABILITY_MIN_INTEGRITY[c]),
                "min_integrity_name": CAPABILITY_MIN_INTEGRITY[c].name.lower(),
            }
            for c in (Capability.INFORMATIONAL, Capability.SUPPRESSIVE, Capability.ACTUATING)
        ],
    }
