"""
api/routes/approvals.py — the human-in-the-loop queue: quarantined memories
and decisions above workspace.autonomy_ceiling both land here (see
database/schema.sql section 5). Not in the original FILE_STRUCTURE.md file
list, but api/main.py's own router list names "approvals" explicitly —
added as its own small file rather than folding read/write concerns for a
different table into memories.py.
"""

from __future__ import annotations

from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel

from api.deps import get_dsn

router = APIRouter(prefix="/workspaces/{workspace_id}/approvals", tags=["approvals"])

VALID_STATUSES = ("pending", "approved", "rejected")


def _serialize_approval(row: dict) -> dict:
    return {
        "approval_id": str(row["approval_id"]),
        "subject_type": row["subject_type"],
        "subject_id": str(row["subject_id"]),
        "reason": row["reason"],
        "actor": row["actor"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
        "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
    }


@router.get("")
def list_approvals(
    workspace_id: str,
    status: Optional[str] = Query("pending"),
    dsn: str = Depends(get_dsn),
) -> list[dict]:
    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {VALID_STATUSES}")

    query = (
        "SELECT approval_id, subject_type, subject_id, reason, actor, status, created_at, resolved_at "
        "FROM approvals WHERE workspace_id = %s"
    )
    params: list = [workspace_id]
    if status:
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY created_at DESC"

    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
    return [_serialize_approval(r) for r in rows]


class ResolveRequest(BaseModel):
    status: str  # 'approved' | 'rejected'
    actor: str


@router.post("/{approval_id}/resolve")
def resolve_approval(workspace_id: str, approval_id: str, body: ResolveRequest, dsn: str = Depends(get_dsn)) -> dict:
    if body.status not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="status must be 'approved' or 'rejected'")

    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE approvals SET status = %s, actor = %s, resolved_at = now() "
                "WHERE workspace_id = %s AND approval_id = %s AND status = 'pending' "
                "RETURNING approval_id, subject_type, subject_id, reason, actor, status, created_at, resolved_at",
                (body.status, body.actor, workspace_id, approval_id),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="approval not found or already resolved")
    return _serialize_approval(row)
