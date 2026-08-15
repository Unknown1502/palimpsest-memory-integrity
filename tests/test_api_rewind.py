"""
tests/test_api_rewind.py — hits the rewind endpoints end-to-end against a
real seeded scenario and asserts verdict_flips >= 1, per Prompt 5.

Patches api.routes.rewind's chat/embed module attributes directly (rather
than agent.bedrock_client's) so this works regardless of import order and
needs no AWS credentials — the same technique used to verify the demo
script live before Bedrock billing blocked it.
"""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from agent.triage import TriageAgent
from memory.gate import Claim, MemoryGate, Provenance
from memory.lattice import Capability

from .conftest import fake_embedding


def _fake_chat(system: str, messages: list, max_tokens: int = 256) -> str:
    user_text = messages[0]["content"]
    if "SOC alert triage assistant" in system:
        alert_ip_match = re.search(r"source_ip=(\d+\.\d+\.\d+\.\d+)", user_text)
        alert_ip = alert_ip_match.group(1) if alert_ip_match else None
        for line in user_text.splitlines():
            if alert_ip and alert_ip in line and "internal_vuln_scanner" in line:
                return json.dumps({"verdict": "suppress", "rationale": "matches the seeded memory"})
        return json.dumps({"verdict": "escalate", "rationale": "no corroborating memory"})
    return json.dumps({"winner": "incumbent", "rationale": "stub", "adjudicator": "bedrock:test-stub"})


def test_rewind_preview_revoke_apply_flips_verdict(monkeypatch, dsn: str, workspace_id: str, agent_id: str):
    monkeypatch.setattr("api.routes.rewind.chat", _fake_chat)
    monkeypatch.setattr("api.routes.rewind.embed", fake_embedding)

    from api.main import app

    client = TestClient(app)

    gate = MemoryGate(dsn=dsn)
    seed_result = gate.admit(
        workspace_id=workspace_id,
        agent_id=agent_id,
        claim=Claim("ip:10.0.0.7", "classification", "internal_vuln_scanner"),
        provenance=Provenance(source_kind="verified_tool", tool_name="asset-inventory", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.9,
        embedding=fake_embedding("10.0.0.7 internal_vuln_scanner"),
    )

    triage = TriageAgent(gate, workspace_id=workspace_id, agent_id=agent_id, embed_fn=fake_embedding, chat_fn=_fake_chat)
    alert = {
        "alert_ref": "AL-REWIND-1",
        "source_ip": "10.0.0.7",
        "dest_host": "db-01",
        "signature": "port-scan",
        "raw_log": "scan",
    }
    decision = triage.decide(alert, capability=Capability.SUPPRESSIVE)
    assert decision.verdict == "suppress"

    rewind_resp = client.post(
        f"/workspaces/{workspace_id}/rewind",
        json={"target_hlc": decision.decided_hlc, "trigger_memory": seed_result.memory_id},
    )
    assert rewind_resp.status_code == 200, rewind_resp.text
    rewind_body = rewind_resp.json()
    rewind_id = rewind_body["rewind_id"]
    assert rewind_body["decisions_in_blast_radius"] == 1

    revoke_resp = client.post(
        f"/workspaces/{workspace_id}/memories/{seed_result.memory_id}/revoke",
        json={"reason": "test revoke", "actor": "tester"},
    )
    assert revoke_resp.status_code == 200, revoke_resp.text

    apply_resp = client.post(f"/workspaces/{workspace_id}/rewind/{rewind_id}/apply")
    assert apply_resp.status_code == 200, apply_resp.text
    apply_body = apply_resp.json()
    assert apply_body["state"] == "applied"
    assert apply_body["verdict_flips"] >= 1
    assert apply_body["replays"][0]["verdict_before"] == "suppress"
    assert apply_body["replays"][0]["verdict_after"] == "escalate"

    # applying an already-applied rewind must be rejected, not silently re-run
    reapply_resp = client.post(f"/workspaces/{workspace_id}/rewind/{rewind_id}/apply")
    assert reapply_resp.status_code == 409
