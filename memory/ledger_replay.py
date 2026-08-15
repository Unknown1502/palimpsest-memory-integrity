"""
memory/ledger_replay.py — AS OF SYSTEM TIME fallback.

FALLBACK PATH: prefer MemoryGate.belief_state_at() (AS OF SYSTEM TIME) when
the cluster tier supports the GC TTL zone config it depends on (see
database/README.md). This module reconstructs belief state purely from
memory_ledger's event log for clusters that don't.

Tradeoff, stated plainly: ledger replay only knows what memory.gate._append_ledger
chose to log. AS OF SYSTEM TIME knows everything the table ever held,
unconditionally — it's MVCC history, not an application-level event log.
Two concrete gaps this has versus the real thing:
  - A quarantined memory's source_kind/integrity_level/capability_ceiling
    aren't in its 'quarantine' ledger payload (only claim + rationale are),
    so those fields come back None here where belief_state_at() would have
    the real values.
  - Anything that somehow mutated `memories` outside memory/gate.py (which
    FILE_STRUCTURE.md's "one rule" forbids, but this module can't verify
    that rule was followed) would be invisible to replay but visible to
    AS OF SYSTEM TIME.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import psycopg


def replay_state_at(dsn: str, *, workspace_id: str, before_ts: dt.datetime) -> list[dict]:
    """
    Reconstruct the last known status/claim per memory_id as of
    `before_ts`, purely from memory_ledger. Returns the same shape as
    MemoryGate.belief_state_at() so callers (api/routes/rewind.py) don't
    need to know which path served the request.
    """
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seq, event_type, payload, created_at FROM memory_ledger "
                "WHERE workspace_id = %s AND created_at <= %s ORDER BY seq ASC",
                (workspace_id, before_ts),
            )
            rows = cur.fetchall()

    state: dict[str, dict[str, Any]] = {}

    for _seq, event_type, payload, _created_at in rows:
        if event_type == "admit":
            memory_id = payload.get("memory_id")
            if memory_id is None or payload.get("event") == "corroborated":
                continue  # corroboration bumps a counter, doesn't change status/claim
            state[memory_id] = {
                "memory_id": memory_id,
                "status": "active",
                "claim": payload.get("claim"),
                "source_kind": payload.get("source_kind"),
                "integrity_level": payload.get("integrity_level"),
                "capability_ceiling": payload.get("capability_ceiling"),
            }

        elif event_type == "supersede":
            # Only updates the LOSING (incumbent) side. The winning
            # challenger's own 'active' entry comes from its own 'admit'
            # event, logged immediately after this one in the same
            # transaction (see memory.gate._admit_tx) and processed by the
            # branch above when this loop reaches it.
            superseded_id = payload.get("superseded_memory_id")
            if superseded_id in state:
                state[superseded_id]["status"] = "superseded"

        elif event_type == "quarantine":
            memory_id = payload.get("memory_id")
            if memory_id is None:
                continue
            if memory_id in state:
                state[memory_id]["status"] = "quarantined"
            else:
                # No preceding 'admit' event for a quarantined memory (see
                # memory.gate._admit_tx) — this IS its first appearance.
                state[memory_id] = {
                    "memory_id": memory_id,
                    "status": "quarantined",
                    "claim": payload.get("claim"),
                    "source_kind": None,
                    "integrity_level": None,
                    "capability_ceiling": None,
                }

        elif event_type == "revoke":
            memory_id = payload.get("memory_id")
            if memory_id in state:
                state[memory_id]["status"] = "revoked"

        elif event_type == "decision":
            continue  # decisions don't touch memory state

    return list(state.values())
