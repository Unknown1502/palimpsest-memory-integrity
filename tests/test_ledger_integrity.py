"""
tests/test_ledger_integrity.py — the hash chain is genuine tamper
evidence, not decorative. Admits a few real memories (exercising
memory.gate._append_ledger for real), verifies via the actual
GET /ledger/verify endpoint, then deliberately corrupts one row's payload
directly via SQL — bypassing the gate entirely, as an attacker with raw DB
access would — and confirms verification correctly detects it.
"""

from __future__ import annotations

import datetime as dt

import psycopg
from fastapi.testclient import TestClient

from memory.gate import Claim, MemoryGate, Provenance
from memory.ledger_replay import replay_state_at
from memory.lattice import Capability

from .conftest import fake_embedding


def test_ledger_verify_passes_on_untampered_chain(dsn: str, workspace_id: str, agent_id: str):
    gate = MemoryGate(dsn=dsn)
    gate.admit(
        workspace_id=workspace_id,
        agent_id=agent_id,
        claim=Claim("ip:198.51.100.10", "classification", "scanner_a"),
        provenance=Provenance(source_kind="verified_tool", tool_name="a", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.8,
        embedding=fake_embedding("198.51.100.10 scanner_a"),
    )
    gate.admit(
        workspace_id=workspace_id,
        agent_id=agent_id,
        claim=Claim("ip:198.51.100.11", "classification", "scanner_b"),
        provenance=Provenance(source_kind="human_confirmed", operator_id="op1"),
        capability=Capability.ACTUATING,
        confidence=0.9,
        embedding=fake_embedding("198.51.100.11 scanner_b"),
    )

    from api.main import app

    client = TestClient(app)
    resp = client.get(f"/workspaces/{workspace_id}/ledger/verify")
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["broken_at_seq"] is None
    assert body["entries_checked"] >= 2


def test_ledger_verify_detects_tampering(dsn: str, workspace_id: str, agent_id: str):
    gate = MemoryGate(dsn=dsn)
    gate.admit(
        workspace_id=workspace_id,
        agent_id=agent_id,
        claim=Claim("ip:198.51.100.20", "classification", "scanner_c"),
        provenance=Provenance(source_kind="verified_tool", tool_name="c", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.8,
        embedding=fake_embedding("198.51.100.20 scanner_c"),
    )
    gate.admit(
        workspace_id=workspace_id,
        agent_id=agent_id,
        claim=Claim("ip:198.51.100.21", "classification", "scanner_d"),
        provenance=Provenance(source_kind="verified_tool", tool_name="d", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.8,
        embedding=fake_embedding("198.51.100.21 scanner_d"),
    )

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seq FROM memory_ledger WHERE workspace_id = %s ORDER BY seq ASC LIMIT 1",
                (workspace_id,),
            )
            first_seq = cur.fetchone()[0]
            cur.execute(
                "UPDATE memory_ledger SET payload = payload || '{\"tampered\": true}'::JSONB "
                "WHERE workspace_id = %s AND seq = %s",
                (workspace_id, first_seq),
            )

    from api.main import app

    client = TestClient(app)
    resp = client.get(f"/workspaces/{workspace_id}/ledger/verify")
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert body["broken_at_seq"] == first_seq


def test_ledger_replay_matches_belief_state_at_shape(gate: MemoryGate, dsn: str, workspace_id: str, agent_id: str):
    """
    Prompt 6's requirement: AS OF SYSTEM TIME and the ledger-replay
    fallback must produce compatible output shapes so api/routes/rewind.py
    doesn't need to know which one served a request. For a plain 'admit'
    (no contradiction, no quarantine), every field should actually agree,
    not just have matching keys — replay's documented gaps
    (memory.ledger_replay's module docstring) only apply to quarantined
    memories, which this test deliberately doesn't exercise.
    """
    result = gate.admit(
        workspace_id=workspace_id,
        agent_id=agent_id,
        claim=Claim("ip:198.51.100.30", "classification", "scanner_e"),
        provenance=Provenance(source_kind="verified_tool", tool_name="e", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.8,
        embedding=fake_embedding("198.51.100.30 scanner_e"),
    )

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT created_at FROM memories WHERE memory_id = %s", (result.memory_id,))
            (admitted_at,) = cur.fetchone()

    after_ts = admitted_at + dt.timedelta(seconds=1)

    aost_state = gate.belief_state_at(workspace_id=workspace_id, hlc=_cluster_ts_after(dsn))
    aost_row = next(m for m in aost_state if m["memory_id"] == result.memory_id)

    replay_state = replay_state_at(dsn, workspace_id=workspace_id, before_ts=after_ts)
    replay_row = next(m for m in replay_state if m["memory_id"] == result.memory_id)

    assert set(aost_row.keys()) == set(replay_row.keys())
    for field in ("status", "claim", "source_kind", "integrity_level", "capability_ceiling"):
        assert aost_row[field] == replay_row[field], f"{field} disagreed: {aost_row[field]!r} != {replay_row[field]!r}"


def _cluster_ts_after(dsn: str) -> str:
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT cluster_logical_timestamp()")
            return str(cur.fetchone()[0])
