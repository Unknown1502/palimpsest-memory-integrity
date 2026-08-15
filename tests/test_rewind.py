"""
tests/test_rewind.py — MemoryGate.revoke() / blast_radius() / belief_state_at()
against a real cluster.

belief_state_at() specifically regression-tests a real bug found while
building demo/attack_scenario.py: a table-level AS OF SYSTEM TIME clause
must be the first statement of a transaction with no timestamp already
established. MemoryGate._connect()'s autocommit=False connections start an
implicit transaction that (confirmed empirically) can already have a
timestamp assigned before the AOST query runs, which CockroachDB then
rejects with "inconsistent AS OF SYSTEM TIME timestamp". Fixed by using a
dedicated autocommit=True connection for this one method.
"""

from __future__ import annotations

import psycopg

from memory.gate import Claim, MemoryGate, Provenance
from memory.lattice import Capability

from .conftest import fake_embedding


def _write_decision(dsn: str, workspace_id: str, agent_id: str, alert_ref: str, verdict: str) -> tuple[str, str]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO decisions (workspace_id, agent_id, alert_ref, verdict, rationale, decided_hlc) "
                "VALUES (%s, %s, %s, %s, %s, cluster_logical_timestamp()) "
                "RETURNING decision_id, decided_hlc",
                (workspace_id, agent_id, alert_ref, verdict, "test rationale"),
            )
            decision_id, decided_hlc = cur.fetchone()
            return str(decision_id), str(decided_hlc)


def _link_decision_to_memory(dsn: str, decision_id: str, memory_id: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO decision_memory_refs "
                "(decision_id, memory_id, rank, semantic_score, eff_confidence, integrity_level, total_score, influence) "
                "VALUES (%s, %s, 1, 0.9, 0.9, 3, 0.81, 1.0)",
                (decision_id, memory_id),
            )


def test_belief_state_at_reflects_pre_revoke_state(gate: MemoryGate, workspace_id: str, agent_id: str):
    result = gate.admit(
        workspace_id=workspace_id,
        agent_id=agent_id,
        claim=Claim("ip:203.0.113.50", "classification", "benign_vendor_scanner"),
        provenance=Provenance(source_kind="verified_tool", tool_name="asset-db", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.9,
        embedding=fake_embedding("203.0.113.50 benign_vendor_scanner"),
    )

    decision_id, decided_hlc = _write_decision(gate.dsn, workspace_id, agent_id, "AL-TEST-1", "suppress")
    _link_decision_to_memory(gate.dsn, decision_id, result.memory_id)

    gate.revoke(workspace_id=workspace_id, memory_id=result.memory_id, reason="test revoke", actor="tester")

    # AS OF SYSTEM TIME at decided_hlc (before the revoke) must show the
    # memory as it was THEN, not its current post-revoke state.
    past_state = gate.belief_state_at(workspace_id=workspace_id, hlc=decided_hlc)
    past_row = next((m for m in past_state if m["memory_id"] == result.memory_id), None)
    assert past_row is not None
    assert past_row["status"] == "active"

    # Current state, by contrast, must show 'revoked'.
    with psycopg.connect(gate.dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM memories WHERE memory_id = %s", (result.memory_id,))
            assert cur.fetchone()[0] == "revoked"


def test_blast_radius_and_revoke(gate: MemoryGate, workspace_id: str, agent_id: str):
    result = gate.admit(
        workspace_id=workspace_id,
        agent_id=agent_id,
        claim=Claim("ip:203.0.113.51", "classification", "benign_vendor_scanner"),
        provenance=Provenance(source_kind="verified_tool", tool_name="asset-db", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.9,
        embedding=fake_embedding("203.0.113.51 benign_vendor_scanner"),
    )

    decision_ids = []
    for i in range(3):
        decision_id, _ = _write_decision(gate.dsn, workspace_id, agent_id, f"AL-TEST-{i}", "suppress")
        _link_decision_to_memory(gate.dsn, decision_id, result.memory_id)
        decision_ids.append(decision_id)

    revoke_result = gate.revoke(workspace_id=workspace_id, memory_id=result.memory_id, reason="test", actor="tester")
    assert revoke_result["blast_radius_count"] == 3
    assert {d["decision_id"] for d in revoke_result["blast_radius"]} == set(decision_ids)
