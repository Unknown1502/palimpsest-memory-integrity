"""
demo/seed.py — seeds a fresh workspace for the attack scenario demo.

    python -m demo.seed

Seeds exactly what CONTEXT.md's demo needs and nothing else: one
workspace, one triage agent, and one verified_tool memory establishing
that 10.0.0.7 is the internal vulnerability scanner. The attack memory
(185.220.101.44) is NOT seeded here — it gets planted live, by
demo/attack_scenario.py itself, because that's the point being
demonstrated.
"""

from __future__ import annotations

import os
import uuid

import psycopg

from agent.bedrock_client import DEFAULT_CLAUDE_MODEL, embed
from memory.gate import Claim, MemoryGate, Provenance
from memory.lattice import Capability

DEMO_WORKSPACE_NAME = "palimpsest-demo"


def seed(dsn: str) -> dict:
    workspace_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    model_id = os.environ.get("PALIMPSEST_ADJUDICATOR_MODEL", DEFAULT_CLAUDE_MODEL)

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO workspaces (workspace_id, name) VALUES (%s, %s)",
                (workspace_id, DEMO_WORKSPACE_NAME),
            )
            cur.execute(
                "INSERT INTO agents (agent_id, workspace_id, role, model_id) VALUES (%s, %s, %s, %s)",
                (agent_id, workspace_id, "triage", model_id),
            )

    gate = MemoryGate(dsn=dsn)
    seed_claim_text = "10.0.0.7 is the internal vulnerability scanner"
    result = gate.admit(
        workspace_id=workspace_id,
        agent_id=agent_id,
        claim=Claim(subject_key="ip:10.0.0.7", predicate="classification", object_value="internal_vuln_scanner"),
        provenance=Provenance(source_kind="verified_tool", tool_name="asset-inventory", signed=True),
        capability=Capability.SUPPRESSIVE,
        confidence=0.9,
        embedding=embed(seed_claim_text),
    )
    assert result.status == "active", f"seed memory did not land active: {result}"

    return {
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "seed_memory_id": result.memory_id,
    }


def wipe(dsn: str) -> int:
    """Delete every demo workspace (and everything that cascades from it)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM workspaces WHERE name = %s", (DEMO_WORKSPACE_NAME,))
            return cur.rowcount


def main() -> int:
    dsn = os.environ.get("PALIMPSEST_DSN")
    if not dsn:
        print("PALIMPSEST_DSN is not set. See database/README.md.")
        return 1
    info = seed(dsn)
    print(f"workspace_id={info['workspace_id']}")
    print(f"agent_id={info['agent_id']}")
    print(f"seed_memory_id={info['seed_memory_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
