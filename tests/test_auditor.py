"""
tests/test_auditor.py — the independent Memory Auditor.

Real CockroachDB throughout, matching the rest of this suite: no database
mocking anywhere. The single most important test here is
test_auditor_connection_cannot_mutate — the auditor's read-only property
must be enforced by the database, not by a convention in our own code that
a later refactor could quietly drop.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from audit.auditor import (
    CAPABILITY_MIN_INTEGRITY,
    CHECKS,
    SOURCE_INTEGRITY,
    connect_read_only,
    run_audit,
)
from memory.gate import Claim, MemoryGate, Provenance
from memory.lattice import Capability
from tests.conftest import fake_embedding


def _admit(gate: MemoryGate, workspace_id: str, agent_id: str, *, subject: str,
           value: str, source_kind: str, capability: Capability):
    return gate.admit(
        workspace_id=workspace_id,
        agent_id=agent_id,
        claim=Claim(subject_key=subject, predicate="classification", object_value=value),
        provenance=Provenance(source_kind=source_kind, tool_name="t", signed=True)
        if source_kind == "verified_tool"
        else Provenance(source_kind=source_kind, ticket_ref="TCK-1"),
        capability=capability,
        confidence=0.9,
        embedding=fake_embedding(f"{subject} {value}"),
    )


def test_auditor_connection_cannot_mutate(dsn: str, workspace_id: str):
    """
    The auditor's guarantee is CockroachDB's, not ours. A write attempted on
    an auditor connection must be refused by the server.
    """
    with connect_read_only(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM memories WHERE workspace_id = %s", (workspace_id,))
            assert cur.fetchone()["n"] == 0  # reads still work

        for statement, params in [
            ("UPDATE memories SET status = 'active' WHERE workspace_id = %s", (workspace_id,)),
            ("DELETE FROM memories WHERE workspace_id = %s", (workspace_id,)),
            ("INSERT INTO workspaces (workspace_id, name) VALUES (%s, %s)", (str(uuid.uuid4()), "x")),
        ]:
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                with conn.cursor() as cur:
                    cur.execute(statement, params)


def test_auditor_restates_policy_rather_than_importing_it():
    """
    The auditor must not import memory/lattice.py — an auditor that adopts
    its subject's definition of "correct" cannot detect a wrong definition.
    These constants are duplicated on purpose; this test pins the values so
    a drift in either copy is caught here rather than silently agreed with.
    """
    assert CAPABILITY_MIN_INTEGRITY == {"informational": 1, "suppressive": 3, "actuating": 4}
    assert SOURCE_INTEGRITY == {
        "untrusted_ingest": 1,
        "agent_inferred": 2,
        "verified_tool": 3,
        "human_confirmed": 4,
    }


def test_every_check_executes_and_is_read_only(dsn: str, workspace_id: str):
    """Every labeled query must actually run — a check that errors audits nothing."""
    report = run_audit(dsn, workspace_id)
    assert report.errors == [], f"checks failed to execute: {report.errors}"
    assert {f.check for f in report.findings} == {c.name for c in CHECKS}


def test_clean_workspace_passes_with_no_violations(dsn: str, workspace_id: str, agent_id: str):
    gate = MemoryGate(dsn=dsn)
    _admit(gate, workspace_id, agent_id, subject="ip:10.0.0.7", value="internal_scanner",
           source_kind="verified_tool", capability=Capability.SUPPRESSIVE)

    report = run_audit(dsn, workspace_id)

    assert report.violations == []
    assert report.ledger_valid is True
    assert report.passed is True
    assert report.metrics["active_memories"] == 1
    assert report.metrics["integrity_violations"] == 0


def test_auditor_verifies_ledger_chain_independently(dsn: str, workspace_id: str, agent_id: str):
    gate = MemoryGate(dsn=dsn)
    for i in range(3):
        _admit(gate, workspace_id, agent_id, subject=f"ip:10.0.0.{i}", value=f"v{i}",
               source_kind="verified_tool", capability=Capability.INFORMATIONAL)

    report = run_audit(dsn, workspace_id)

    assert report.ledger_valid is True
    assert report.ledger_entries_checked == 3
    assert report.ledger_broken_at_seq is None


def test_auditor_detects_a_tampered_ledger(dsn: str, workspace_id: str, agent_id: str):
    """
    Rewrite a committed ledger payload behind the gate's back. The hash chain
    must no longer re-derive, and the auditor must say where it broke. This
    is the whole point of the chain being tamper-EVIDENT rather than
    tamper-proof.
    """
    gate = MemoryGate(dsn=dsn)
    for i in range(3):
        _admit(gate, workspace_id, agent_id, subject=f"ip:10.0.1.{i}", value=f"v{i}",
               source_kind="verified_tool", capability=Capability.INFORMATIONAL)

    assert run_audit(dsn, workspace_id).ledger_valid is True

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE memory_ledger SET payload = jsonb_set(payload, ARRAY['claim'], '\"tampered\"') "
                "WHERE workspace_id = %s AND seq = 1",
                (workspace_id,),
            )

    report = run_audit(dsn, workspace_id)
    assert report.ledger_valid is False
    assert report.ledger_broken_at_seq == 1
    assert report.passed is False


def test_auditor_finds_decisions_that_cited_a_revoked_belief(dsn: str, workspace_id: str, agent_id: str):
    """
    Revoked-memory discovery is what makes an audit actionable: it is the
    same set rewind's blast_radius() computes, derived independently from
    the raw rows.
    """
    gate = MemoryGate(dsn=dsn)
    admitted = _admit(gate, workspace_id, agent_id, subject="ip:185.220.101.44",
                      value="benign_scanner", source_kind="untrusted_ingest",
                      capability=Capability.INFORMATIONAL)

    decision_ids = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            for ref in ("AL-1", "AL-2"):
                cur.execute(
                    "INSERT INTO decisions (workspace_id, agent_id, alert_ref, alert_payload, "
                    "verdict, rationale, decided_hlc) VALUES (%s, %s, %s, '{}', 'suppress', 'r', "
                    "cluster_logical_timestamp()) RETURNING decision_id",
                    (workspace_id, agent_id, ref),
                )
                decision_id = cur.fetchone()[0]
                decision_ids.append(decision_id)
                cur.execute(
                    "INSERT INTO decision_memory_refs (decision_id, memory_id, rank, semantic_score, "
                    "eff_confidence, integrity_level, total_score, influence) "
                    "VALUES (%s, %s, 1, 0.9, 0.25, 1, 0.22, 0.5)",
                    (decision_id, admitted.memory_id),
                )

    # Before revocation: the belief is untrusted and it drove suppressions,
    # which the auditor flags on its own.
    before = run_audit(dsn, workspace_id)
    untrusted = next(f for f in before.findings if f.check == "untrusted_influenced_suppression")
    assert untrusted.count == 2
    assert before.passed is False

    gate.revoke(workspace_id=workspace_id, memory_id=admitted.memory_id,
                reason="confirmed injection", actor="test")

    after = run_audit(dsn, workspace_id)
    cited = next(f for f in after.findings if f.check == "revoked_memory_still_cited")
    assert cited.count == 2
    assert after.metrics["revoked_memories"] == 1
    assert after.metrics["decisions_touching_revoked"] == 2
    assert {str(r["decision_id"]) for r in cited.rows} == {str(d) for d in decision_ids}


def test_auditor_flags_a_quarantined_belief(dsn: str, workspace_id: str, agent_id: str):
    """A losing challenger lands in quarantine; the audit must surface it."""
    gate = MemoryGate(dsn=dsn)
    _admit(gate, workspace_id, agent_id, subject="ip:203.0.113.9", value="malicious",
           source_kind="verified_tool", capability=Capability.INFORMATIONAL)
    challenger = _admit(gate, workspace_id, agent_id, subject="ip:203.0.113.9", value="benign",
                        source_kind="untrusted_ingest", capability=Capability.INFORMATIONAL)

    assert challenger.status == "quarantined"

    report = run_audit(dsn, workspace_id)
    quarantined = next(f for f in report.findings if f.check == "quarantined_beliefs")
    assert quarantined.count == 1
    assert str(quarantined.rows[0]["memory_id"]) == challenger.memory_id
    assert report.metrics["quarantined_memories"] == 1
