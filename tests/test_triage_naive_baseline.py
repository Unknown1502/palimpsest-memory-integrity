"""
tests/test_triage_naive_baseline.py — proves agent/triage.py's
_ask_claude() actually ties integrity disclosure to gate.gate_enabled, not
just retrieval filtering. Found necessary by running demo/attack_scenario.py
against a real, live Claude call for the first time (see agent/triage.py's
_ask_claude() docstring comment): with the retrieval filter disabled but
integrity labels still always shown to the model, a live model consistently
refused to trust the injected untrusted_ingest memory — meaning "gate
disabled" wasn't actually simulating an agent without Palimpsest's
memory-integrity layer, only one with its retrieval filter off. This test
doesn't call a real model — it captures the system/user prompt a fake
chat_fn receives and asserts the SHAPE of what reaches the LLM differs
correctly between gate_enabled=True and gate_enabled=False.
"""

from __future__ import annotations

import json

from agent.triage import TriageAgent
from memory.gate import Claim, MemoryGate, Provenance
from memory.lattice import Capability

from .conftest import fake_embedding


def _capturing_chat_fn(captured: dict):
    def chat_fn(system: str, messages: list[dict], max_tokens: int = 256) -> str:
        captured["system"] = system
        captured["user_prompt"] = messages[0]["content"]
        return json.dumps({"verdict": "escalate", "rationale": "captured for inspection only"})

    return chat_fn


def _seed_suppressive_memory(gate: MemoryGate, workspace_id: str) -> None:
    # verified_tool (integrity 3) legitimately reaches capability=SUPPRESSIVE
    # -- unlike untrusted_ingest (integrity 1), which the lattice forbids
    # from ever being admitted above INFORMATIONAL. This is what makes the
    # memory retrievable under gate_enabled=True for a suppressive request,
    # so the "enabled" test can actually reach the disclosure question.
    gate.admit(
        workspace_id=workspace_id,
        agent_id=None,
        claim=Claim("ip:203.0.113.9", "classification", "benign_vendor_scanner"),
        provenance=Provenance(source_kind="verified_tool", tool_name="asset-inventory", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.9,
        embedding=fake_embedding("203.0.113.9 benign_vendor_scanner (suppressive)"),
    )


def _seed_low_integrity_memory(gate: MemoryGate, workspace_id: str) -> None:
    # untrusted_ingest, capped at INFORMATIONAL -- only retrievable for a
    # suppressive-capability request when gate_enabled=False bypasses both
    # lattice filters (see memory/gate.py retrieve()).
    gate.admit(
        workspace_id=workspace_id,
        agent_id=None,
        claim=Claim("ip:203.0.113.9", "classification", "benign_vendor_scanner"),
        provenance=Provenance(source_kind="untrusted_ingest", ticket_ref="TCK-TEST"),
        capability=Capability.INFORMATIONAL,
        confidence=0.9,
        embedding=fake_embedding("203.0.113.9 benign_vendor_scanner (informational)"),
    )


_ALERT = {
    "alert_ref": "AL-TEST",
    "source_ip": "203.0.113.9",
    "dest_host": "internal-db-01",
    "signature": "test signature",
    "raw_log": "203.0.113.9 benign_vendor_scanner",
}


def test_gate_enabled_discloses_integrity_level_to_the_model(dsn: str, workspace_id: str, agent_id: str):
    gate = MemoryGate(dsn=dsn, gate_enabled=True)
    _seed_suppressive_memory(gate, workspace_id)

    captured: dict = {}
    triage = TriageAgent(
        gate, workspace_id=workspace_id, agent_id=agent_id, embed_fn=fake_embedding, chat_fn=_capturing_chat_fn(captured)
    )
    triage.decide(_ALERT, capability=Capability.SUPPRESSIVE)

    assert "verified_tool" in captured["user_prompt"], (
        "with the gate enabled, the memory block must label the source's integrity level explicitly"
    )
    assert "integrity level" in captured["system"], (
        "with the gate enabled, the system prompt must instruct the model to weigh source integrity"
    )


def test_gate_disabled_strips_integrity_level_from_the_model(dsn: str, workspace_id: str, agent_id: str):
    gate = MemoryGate(dsn=dsn, gate_enabled=False)
    _seed_low_integrity_memory(gate, workspace_id)

    captured: dict = {}
    triage = TriageAgent(
        gate, workspace_id=workspace_id, agent_id=agent_id, embed_fn=fake_embedding, chat_fn=_capturing_chat_fn(captured)
    )
    triage.decide(_ALERT, capability=Capability.SUPPRESSIVE)

    assert "untrusted_ingest" not in captured["user_prompt"], (
        "with the gate disabled, the memory block must carry no integrity labels at all -- "
        "a naive agent with no memory-integrity layer never tracked provenance to begin with"
    )
    assert "integrity level" not in captured["system"], (
        "with the gate disabled, the system prompt must not instruct the model to weigh "
        "source integrity -- there is nothing for it to weigh"
    )
    # The claim text itself must still reach the model -- only the labeling
    # is stripped, not the underlying memory.
    assert "benign_vendor_scanner" in captured["user_prompt"]
