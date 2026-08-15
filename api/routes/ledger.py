"""
api/routes/ledger.py — tamper-evident ledger tail + hash-chain verification.

/verify re-derives every entry_hash from scratch (sha256(prev_hash ||
canonical_json(payload))) and compares against what's stored — a genuine
tamper-evidence check, not a decorative one. Uses the exact same
canonicalization as memory.gate._append_ledger (sort_keys=True,
separators=(",", ":"), default=str) so a hash computed here always agrees
with the hash computed at write time.
"""

from __future__ import annotations

import hashlib
import json

import psycopg
from fastapi import APIRouter, Depends, Query
from psycopg.rows import dict_row

from api.deps import get_dsn
from memory.gate import GENESIS_HASH

router = APIRouter(prefix="/workspaces/{workspace_id}/ledger", tags=["ledger"])


def _serialize_entry(row: dict) -> dict:
    return {
        "seq": row["seq"],
        "event_type": row["event_type"],
        "payload": row["payload"],
        "prev_hash": row["prev_hash"],
        "entry_hash": row["entry_hash"],
        "exported_at": row["exported_at"].isoformat() if row["exported_at"] else None,
        "created_at": row["created_at"].isoformat(),
    }


@router.get("")
def tail_ledger(
    workspace_id: str,
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    dsn: str = Depends(get_dsn),
) -> list[dict]:
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seq, event_type, payload, prev_hash, entry_hash, exported_at, created_at "
                "FROM memory_ledger WHERE workspace_id = %s ORDER BY seq DESC LIMIT %s OFFSET %s",
                (workspace_id, limit, offset),
            )
            rows = cur.fetchall()
    return [_serialize_entry(r) for r in rows]


@router.get("/verify")
def verify_ledger(workspace_id: str, dsn: str = Depends(get_dsn)) -> dict:
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seq, payload, prev_hash, entry_hash FROM memory_ledger "
                "WHERE workspace_id = %s ORDER BY seq ASC",
                (workspace_id,),
            )
            rows = cur.fetchall()

    expected_prev = GENESIS_HASH
    checked = 0
    for row in rows:
        checked += 1
        canonical = json.dumps(row["payload"], sort_keys=True, separators=(",", ":"), default=str)
        expected_hash = hashlib.sha256((expected_prev + canonical).encode("utf-8")).hexdigest()
        if row["prev_hash"] != expected_prev or row["entry_hash"] != expected_hash:
            return {"valid": False, "broken_at_seq": row["seq"], "entries_checked": checked}
        expected_prev = row["entry_hash"]

    return {"valid": True, "broken_at_seq": None, "entries_checked": checked}
