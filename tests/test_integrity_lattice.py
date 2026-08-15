"""
tests/test_integrity_lattice.py — proves the core thesis against a real
CockroachDB connection: the integrity lattice and atomic contradiction
adjudication actually work, not just the happy path.
"""

from __future__ import annotations

import psycopg
import pytest

from memory.gate import Claim, MemoryGate, Provenance
from memory.lattice import Capability, IntegrityViolation

from .conftest import fake_embedding


def _memory_row(dsn: str, memory_id: str):
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, integrity_level, capability_ceiling FROM memories WHERE memory_id = %s",
                (memory_id,),
            )
            return cur.fetchone()


def _count_memories(dsn: str, workspace_id: str) -> int:
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM memories WHERE workspace_id = %s", (workspace_id,))
            return cur.fetchone()[0]


def test_human_confirmed_can_admit_actuating(gate: MemoryGate, workspace_id: str):
    result = gate.admit(
        workspace_id=workspace_id,
        agent_id=None,
        claim=Claim("ip:10.0.0.9", "classification", "quarantine_host"),
        provenance=Provenance(source_kind="human_confirmed", operator_id="alice"),
        capability=Capability.ACTUATING,
        confidence=0.95,
        embedding=fake_embedding("10.0.0.9 quarantine_host"),
    )
    assert result.status == "active"

    status, integrity_level, capability_ceiling = _memory_row(gate.dsn, result.memory_id)
    assert status == "active"
    assert integrity_level == 4
    assert capability_ceiling == "actuating"


def test_untrusted_ingest_cannot_admit_suppressive(gate: MemoryGate, workspace_id: str):
    before = _count_memories(gate.dsn, workspace_id)

    with pytest.raises(IntegrityViolation):
        gate.admit(
            workspace_id=workspace_id,
            agent_id=None,
            claim=Claim("ip:185.220.101.44", "classification", "benign_vendor_scanner"),
            provenance=Provenance(source_kind="untrusted_ingest", ticket_ref="TCK-1"),
            capability=Capability.SUPPRESSIVE,
            confidence=0.9,
            embedding=fake_embedding("185.220.101.44 benign_vendor_scanner"),
        )

    # The violation must be raised BEFORE any database write — assert no
    # row was created, not even a rejected/quarantined one.
    after = _count_memories(gate.dsn, workspace_id)
    assert after == before


def test_higher_integrity_challenger_supersedes(gate: MemoryGate, workspace_id: str):
    incumbent = gate.admit(
        workspace_id=workspace_id,
        agent_id=None,
        claim=Claim("ip:10.0.0.7", "classification", "unknown"),
        provenance=Provenance(source_kind="agent_inferred"),
        capability=Capability.INFORMATIONAL,
        confidence=0.5,
        embedding=fake_embedding("10.0.0.7 unknown"),
    )
    assert incumbent.status == "active"

    challenger = gate.admit(
        workspace_id=workspace_id,
        agent_id=None,
        claim=Claim("ip:10.0.0.7", "classification", "internal_vuln_scanner"),
        provenance=Provenance(source_kind="verified_tool", tool_name="asset-inventory", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.9,
        embedding=fake_embedding("10.0.0.7 internal_vuln_scanner"),
    )
    assert challenger.status == "active"
    assert challenger.contradiction is not None
    assert challenger.contradiction["verdict"] == "supersede"
    assert challenger.contradiction["adjudicator"] == "rule:integrity_dominance"

    incumbent_status, _, _ = _memory_row(gate.dsn, incumbent.memory_id)
    challenger_status, _, _ = _memory_row(gate.dsn, challenger.memory_id)
    assert incumbent_status == "superseded"
    assert challenger_status == "active"

    with psycopg.connect(gate.dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT verdict, adjudicator FROM contradictions "
                "WHERE workspace_id = %s AND incumbent_memory_id = %s AND challenger_memory_id = %s",
                (workspace_id, incumbent.memory_id, challenger.memory_id),
            )
            row = cur.fetchone()
    assert row == ("supersede", "rule:integrity_dominance")


def test_lower_integrity_challenger_quarantined(gate: MemoryGate, workspace_id: str):
    incumbent = gate.admit(
        workspace_id=workspace_id,
        agent_id=None,
        claim=Claim("ip:10.0.0.7", "classification", "internal_vuln_scanner"),
        provenance=Provenance(source_kind="verified_tool", tool_name="asset-inventory", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.9,
        embedding=fake_embedding("10.0.0.7 internal_vuln_scanner"),
    )
    assert incumbent.status == "active"

    challenger = gate.admit(
        workspace_id=workspace_id,
        agent_id=None,
        claim=Claim("ip:10.0.0.7", "classification", "compromised_host"),
        provenance=Provenance(source_kind="agent_inferred"),
        capability=Capability.INFORMATIONAL,
        confidence=0.6,
        embedding=fake_embedding("10.0.0.7 compromised_host"),
    )
    assert challenger.status == "quarantined"
    assert challenger.contradiction["verdict"] == "quarantine"
    assert challenger.contradiction["adjudicator"] == "rule:integrity_subordinate"

    incumbent_status, _, _ = _memory_row(gate.dsn, incumbent.memory_id)
    challenger_status, _, _ = _memory_row(gate.dsn, challenger.memory_id)
    assert incumbent_status == "active"  # untouched
    assert challenger_status == "quarantined"

    with psycopg.connect(gate.dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reason FROM approvals WHERE workspace_id = %s AND subject_id = %s AND subject_type = 'memory'",
                (workspace_id, challenger.memory_id),
            )
            row = cur.fetchone()
    assert row is not None


def test_equal_integrity_invokes_adjudicator(dsn: str, workspace_id: str):
    calls = []

    def mock_adjudicate(incumbent: dict, challenger: dict) -> dict:
        calls.append((incumbent, challenger))
        return {
            "winner": "challenger",
            "rationale": "mock Bedrock adjudicator: challenger is more specific",
            "adjudicator": "bedrock:anthropic.claude-haiku-4-5-20251001-v1:0",
        }

    gate = MemoryGate(dsn=dsn, adjudicate_fn=mock_adjudicate)

    incumbent = gate.admit(
        workspace_id=workspace_id,
        agent_id=None,
        claim=Claim("ip:10.0.0.7", "classification", "internal_vuln_scanner"),
        provenance=Provenance(source_kind="verified_tool", tool_name="asset-inventory-a", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.7,
        embedding=fake_embedding("10.0.0.7 internal_vuln_scanner A"),
    )
    challenger = gate.admit(
        workspace_id=workspace_id,
        agent_id=None,
        claim=Claim("ip:10.0.0.7", "classification", "decommissioned_scanner"),
        provenance=Provenance(source_kind="verified_tool", tool_name="asset-inventory-b", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.8,
        embedding=fake_embedding("10.0.0.7 decommissioned_scanner"),
    )

    assert len(calls) == 1, "equal-integrity contradiction must invoke the adjudicator exactly once"
    assert challenger.contradiction["verdict"] == "llm_adjudicated"
    assert challenger.contradiction["adjudicator"] == "bedrock:anthropic.claude-haiku-4-5-20251001-v1:0"

    incumbent_status, _, _ = _memory_row(dsn, incumbent.memory_id)
    challenger_status, _, _ = _memory_row(dsn, challenger.memory_id)
    assert incumbent_status == "superseded"
    assert challenger_status == "active"


def test_retrieve_excludes_informational_memory_from_suppressive_request(gate: MemoryGate, workspace_id: str):
    """
    Regression test for a real gap found while building the demo: a
    human_confirmed source (integrity 4) can deliberately write an
    informational-only memory (capability_ceiling='informational') — e.g.
    "this host was scanned last week" is a fact worth keeping, but was
    never meant to justify suppressing an alert on its own. Filtering
    retrieve() on integrity_level alone would wrongly surface it for a
    SUPPRESSIVE-capability request, since integrity 4 >= the integrity 3
    floor for suppressive. capability_ceiling is the dimension that must
    actually gate retrieval — see memory/gate.py retrieve()'s
    capability_rank_case filter.
    """
    gate.admit(
        workspace_id=workspace_id,
        agent_id=None,
        claim=Claim("ip:198.51.100.5", "scan_history", "scanned_last_week"),
        provenance=Provenance(source_kind="human_confirmed", operator_id="bob"),
        capability=Capability.INFORMATIONAL,  # deliberately capped below what integrity 4 would allow
        confidence=0.9,
        embedding=fake_embedding("198.51.100.5 scanned_last_week"),
    )

    informational_hits = gate.retrieve(
        workspace_id=workspace_id,
        embedding=fake_embedding("198.51.100.5 scanned_last_week"),
        capability=Capability.INFORMATIONAL,
        top_k=5,
    )
    assert any(m.subject_key == "ip:198.51.100.5" for m in informational_hits), (
        "sanity check: the memory must be retrievable at its own capability level"
    )

    suppressive_hits = gate.retrieve(
        workspace_id=workspace_id,
        embedding=fake_embedding("198.51.100.5 scanned_last_week"),
        capability=Capability.SUPPRESSIVE,
        top_k=5,
    )
    assert not any(m.subject_key == "ip:198.51.100.5" for m in suppressive_hits), (
        "an informational-ceiling memory must never surface for a suppressive-capability "
        "retrieval, even from a high-integrity source"
    )
