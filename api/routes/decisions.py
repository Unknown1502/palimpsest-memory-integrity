"""api/routes/decisions.py — read-only: the decision history the console's /timeline reads."""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from api.deps import get_dsn

router = APIRouter(prefix="/workspaces/{workspace_id}/decisions", tags=["decisions"])


def _serialize_decision(row: dict) -> dict:
    return {
        "decision_id": str(row["decision_id"]),
        "agent_id": str(row["agent_id"]) if row["agent_id"] else None,
        "alert_ref": row["alert_ref"],
        "alert_payload": row["alert_payload"],
        "verdict": row["verdict"],
        "rationale": row["rationale"],
        "decided_hlc": str(row["decided_hlc"]),
        "created_at": row["created_at"].isoformat(),
    }


def _serialize_ref(row: dict) -> dict:
    return {
        "memory_id": str(row["memory_id"]),
        "claim": row["claim"],
        "status": row["status"],
        "rank": row["rank"],
        "semantic_score": row["semantic_score"],
        "eff_confidence": row["eff_confidence"],
        "integrity_level": row["integrity_level"],
        "total_score": row["total_score"],
        "influence": row["influence"],
    }


@router.get("")
def list_decisions(
    workspace_id: str,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    dsn: str = Depends(get_dsn),
) -> list[dict]:
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT decision_id, agent_id, alert_ref, alert_payload, verdict, rationale, "
                "       decided_hlc, created_at "
                "FROM decisions WHERE workspace_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (workspace_id, limit, offset),
            )
            rows = cur.fetchall()
    return [_serialize_decision(r) for r in rows]


@router.get("/{decision_id}")
def get_decision(workspace_id: str, decision_id: str, dsn: str = Depends(get_dsn)) -> dict:
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT decision_id, agent_id, alert_ref, alert_payload, verdict, rationale, "
                "       decided_hlc, created_at "
                "FROM decisions WHERE workspace_id = %s AND decision_id = %s",
                (workspace_id, decision_id),
            )
            decision = cur.fetchone()
            if decision is None:
                raise HTTPException(status_code=404, detail="decision not found")

            cur.execute(
                "SELECT dmr.memory_id, dmr.rank, dmr.semantic_score, dmr.eff_confidence, "
                "       dmr.integrity_level, dmr.total_score, dmr.influence, m.claim, m.status "
                "FROM decision_memory_refs dmr JOIN memories m ON m.memory_id = dmr.memory_id "
                "WHERE dmr.decision_id = %s ORDER BY dmr.rank",
                (decision_id,),
            )
            refs = cur.fetchall()

    result = _serialize_decision(decision)
    result["memory_refs"] = [_serialize_ref(r) for r in refs]
    return result
