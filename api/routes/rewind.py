"""
api/routes/rewind.py — temporal belief diff, blast radius, and replay.

POST /rewind computes a then-vs-now belief diff (the SQL pattern from
database/schema.sql section 10b) and the blast radius for a suspected
trigger_memory, without mutating anything — the actual revocation happens
through POST /memories/{id}/revoke (api/routes/memories.py). POST
/rewind/{id}/apply then replays every decision in that blast radius
against CURRENT memory state (so it should be called after the trigger
memory has actually been revoked) and reports how many verdicts flipped.
"""

from __future__ import annotations

import json
import re

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel

from agent.bedrock_client import chat, embed
from agent.triage import TriageAgent
from api.deps import get_dsn, get_gate
from memory.gate import MemoryGate
from memory.lattice import Capability

router = APIRouter(prefix="/workspaces/{workspace_id}/rewind", tags=["rewind"])

_HLC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _validate_hlc(hlc: str) -> str:
    # Same validation as memory.gate._validate_hlc: AS OF SYSTEM TIME needs
    # a literal, not a bound parameter, so this must be checked strictly
    # before string-interpolating it into SQL. target_hlc here is
    # caller-supplied (unlike gate.py's internal use), so this check is
    # the only thing standing between a request body and SQL injection via
    # this specific clause.
    if not _HLC_RE.match(hlc):
        raise HTTPException(status_code=422, detail=f"invalid target_hlc: {hlc!r}")
    return hlc


class RewindRequest(BaseModel):
    target_hlc: str
    trigger_memory: str


@router.post("")
def create_rewind(
    workspace_id: str,
    body: RewindRequest,
    dsn: str = Depends(get_dsn),
    gate: MemoryGate = Depends(get_gate),
) -> dict:
    hlc_literal = _validate_hlc(body.target_hlc)

    # Two separate top-level statements, not one combined query. Confirmed
    # empirically: CockroachDB rejects a table-level AS OF SYSTEM TIME
    # clause nested in a subquery alongside a live-read sibling in the same
    # statement ("AS OF SYSTEM TIME must be provided on a top-level
    # statement") -- database/schema.sql's earlier 10b comment showed the
    # combined FULL OUTER JOIN form as if it worked; it doesn't. The diff
    # is computed here in Python instead.
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT memory_id, status, claim FROM memories AS OF SYSTEM TIME {hlc_literal} "
                f"WHERE workspace_id = %s",
                (workspace_id,),
            )
            then_rows = {str(r["memory_id"]): r for r in cur.fetchall()}

    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT memory_id, status, claim FROM memories WHERE workspace_id = %s",
                (workspace_id,),
            )
            now_rows = {str(r["memory_id"]): r for r in cur.fetchall()}

    belief_diff = []
    for memory_id in set(then_rows) | set(now_rows):
        then_row = then_rows.get(memory_id)
        now_row = now_rows.get(memory_id)
        status_then = then_row["status"] if then_row else None
        status_now = now_row["status"] if now_row else None
        if status_then != status_now:
            belief_diff.append(
                {
                    "memory_id": memory_id,
                    "claim": (now_row or then_row)["claim"],
                    "status_then": status_then,
                    "status_now": status_now,
                }
            )

    blast = gate.blast_radius(workspace_id=workspace_id, memory_id=body.trigger_memory)

    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rewinds "
                "  (workspace_id, target_hlc, trigger_memory, belief_diff, decisions_in_blast_radius, state) "
                "VALUES (%s, %s, %s, %s, %s, 'awaiting_approval') "
                "RETURNING rewind_id, created_at",
                (workspace_id, body.target_hlc, body.trigger_memory, json.dumps(belief_diff), len(blast)),
            )
            row = cur.fetchone()

    return {
        "rewind_id": str(row["rewind_id"]),
        "workspace_id": workspace_id,
        "target_hlc": body.target_hlc,
        "trigger_memory": body.trigger_memory,
        "belief_diff": belief_diff,
        "decisions_in_blast_radius": len(blast),
        "blast_radius": blast,
        "state": "awaiting_approval",
        "created_at": row["created_at"].isoformat(),
    }


@router.post("/{rewind_id}/apply")
def apply_rewind(
    workspace_id: str,
    rewind_id: str,
    dsn: str = Depends(get_dsn),
    gate: MemoryGate = Depends(get_gate),
) -> dict:
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rewind_id, trigger_memory, state FROM rewinds WHERE workspace_id = %s AND rewind_id = %s",
                (workspace_id, rewind_id),
            )
            rewind_row = cur.fetchone()
    if rewind_row is None:
        raise HTTPException(status_code=404, detail="rewind not found")
    if rewind_row["state"] != "awaiting_approval":
        raise HTTPException(status_code=409, detail=f"rewind is not awaiting_approval (state={rewind_row['state']})")

    trigger_memory = str(rewind_row["trigger_memory"])
    blast = gate.blast_radius(workspace_id=workspace_id, memory_id=trigger_memory)

    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            decision_ids = [d["decision_id"] for d in blast]
            cur.execute(
                "SELECT decision_id, agent_id, alert_ref, alert_payload, verdict FROM decisions "
                "WHERE workspace_id = %s AND decision_id = ANY(%s)",
                (workspace_id, decision_ids),
            )
            decision_rows = {str(r["decision_id"]): r for r in cur.fetchall()}

    flips = 0
    replays = []
    for d in blast:
        row = decision_rows.get(d["decision_id"])
        if row is None:
            continue
        triage = TriageAgent(
            gate, workspace_id=workspace_id, agent_id=str(row["agent_id"]), embed_fn=embed, chat_fn=chat
        )
        alert = dict(row["alert_payload"])
        alert["alert_ref"] = f"{row['alert_ref']}-REWIND-{rewind_id[:8]}"
        replayed = triage.decide(alert, capability=Capability.SUPPRESSIVE)
        flipped = replayed.verdict != row["verdict"]
        flips += int(flipped)
        replays.append(
            {
                "original_decision_id": str(row["decision_id"]),
                "alert_ref": row["alert_ref"],
                "verdict_before": row["verdict"],
                "verdict_after": replayed.verdict,
                "flipped": flipped,
                "replay_decision_id": replayed.decision_id,
            }
        )

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE rewinds SET state = 'applied', verdict_flips = %s, applied_at = now() "
                "WHERE workspace_id = %s AND rewind_id = %s",
                (flips, workspace_id, rewind_id),
            )

    return {
        "rewind_id": rewind_id,
        "state": "applied",
        "verdict_flips": flips,
        "decisions_replayed": len(replays),
        "replays": replays,
    }
