"""api/routes/memories.py — list/filter memories, blast radius, revoke."""

from __future__ import annotations

from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel

from api.deps import get_dsn, get_gate
from memory.gate import MemoryGate

router = APIRouter(prefix="/workspaces/{workspace_id}/memories", tags=["memories"])

VALID_STATUSES = ("active", "superseded", "quarantined", "revoked")


def _serialize_memory(row: dict) -> dict:
    return {
        "memory_id": str(row["memory_id"]),
        "status": row["status"],
        "claim": row["claim"],
        "subject_key": row["subject_key"],
        "predicate": row["predicate"],
        "object_value": row["object_value"],
        "source_kind": row["source_kind"],
        "integrity_level": row["integrity_level"],
        "capability_ceiling": row["capability_ceiling"],
        "confidence": row["confidence"],
        "eff_confidence": row["eff_confidence"],
        "corroborations": row["corroborations"],
        "refutations": row["refutations"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@router.get("")
def list_memories(
    workspace_id: str,
    status: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    dsn: str = Depends(get_dsn),
) -> list[dict]:
    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {VALID_STATUSES}")

    query = (
        "SELECT memory_id, status, claim, subject_key, predicate, object_value, source_kind, "
        "       integrity_level, capability_ceiling, confidence, eff_confidence, corroborations, "
        "       refutations, created_at, updated_at "
        "FROM memories WHERE workspace_id = %s"
    )
    params: list = [workspace_id]
    if status:
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
    return [_serialize_memory(r) for r in rows]


@router.get("/{memory_id}/blast_radius")
def blast_radius(workspace_id: str, memory_id: str, gate: MemoryGate = Depends(get_gate)) -> dict:
    decisions = gate.blast_radius(workspace_id=workspace_id, memory_id=memory_id)
    return {"memory_id": memory_id, "decisions": decisions, "count": len(decisions)}


class RevokeRequest(BaseModel):
    reason: str
    actor: str


@router.post("/{memory_id}/revoke")
def revoke_memory(
    workspace_id: str, memory_id: str, body: RevokeRequest, gate: MemoryGate = Depends(get_gate)
) -> dict:
    try:
        return gate.revoke(workspace_id=workspace_id, memory_id=memory_id, reason=body.reason, actor=body.actor)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
