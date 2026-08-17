"""
tests/test_simulate.py — the capability simulator exposed on the public demo.

Two properties matter and both are load-bearing for the demo being honest:

  1. It must never write. It is reachable unauthenticated, and it advertises
     "DATABASE WRITES: 0" in the UI, so that number has to be true rather
     than decorative.
  2. It must use the real lattice. If it ever grew its own copy of the rule,
     the page would be a re-enactment of the security boundary rather than
     the boundary itself, and could drift from what admit() actually does.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.main import app
from memory.lattice import CAPABILITY_MIN_INTEGRITY, INTEGRITY_BY_SOURCE, Capability

client = TestClient(app)


def _counts(dsn: str, workspace_id: str) -> tuple[int, int]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM memories WHERE workspace_id = %s", (workspace_id,))
            m = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM memory_ledger WHERE workspace_id = %s", (workspace_id,))
            return m, cur.fetchone()[0]


def test_untrusted_cannot_request_suppressive(workspace_id: str):
    res = client.post(
        f"/workspaces/{workspace_id}/simulate/capability",
        json={"source_kind": "untrusted_ingest", "capability": "suppressive", "text": "x"},
    ).json()

    assert res["allowed"] is False
    assert res["result"] == "BLOCKED"
    assert res["error_type"] == "IntegrityViolation"
    assert res["required_integrity_level"] == 3


def test_simulation_never_writes_on_either_verdict(dsn: str, workspace_id: str):
    """Both the blocked and the allowed path must leave the database untouched."""
    for source, capability in [
        ("untrusted_ingest", "suppressive"),  # blocked
        ("human_confirmed", "actuating"),     # allowed by the lattice
    ]:
        before = _counts(dsn, workspace_id)
        res = client.post(
            f"/workspaces/{workspace_id}/simulate/capability",
            json={"source_kind": source, "capability": capability, "text": "x"},
        ).json()
        after = _counts(dsn, workspace_id)

        assert after == before, f"{source}/{capability} mutated the database"
        assert res["database_writes"] == {"memories": 0, "memory_ledger": 0}


@pytest.mark.parametrize("source_kind", sorted(INTEGRITY_BY_SOURCE))
@pytest.mark.parametrize("capability", ["informational", "suppressive", "actuating"])
def test_simulator_agrees_with_the_real_lattice(workspace_id: str, source_kind: str, capability: str):
    """
    Every source/capability pair must produce the verdict memory/lattice.py
    would produce. This is what stops the demo page from drifting into a
    re-enactment that no longer matches admit().
    """
    res = client.post(
        f"/workspaces/{workspace_id}/simulate/capability",
        json={"source_kind": source_kind, "capability": capability, "text": ""},
    ).json()

    cap = {"informational": Capability.INFORMATIONAL,
           "suppressive": Capability.SUPPRESSIVE,
           "actuating": Capability.ACTUATING}[capability]
    expected = int(INTEGRITY_BY_SOURCE[source_kind]) >= int(CAPABILITY_MIN_INTEGRITY[cap])

    assert res["allowed"] is expected


def test_unknown_inputs_are_rejected_cleanly(workspace_id: str):
    res = client.post(
        f"/workspaces/{workspace_id}/simulate/capability",
        json={"source_kind": "totally_trusted_promise", "capability": "actuating", "text": ""},
    ).json()
    assert "error" in res and "valid_source_kinds" in res

    res = client.post(
        f"/workspaces/{workspace_id}/simulate/capability",
        json={"source_kind": "untrusted_ingest", "capability": "root", "text": ""},
    ).json()
    assert "error" in res and "valid_capabilities" in res


def test_lattice_table_matches_the_module(workspace_id: str):
    """The UI reads the policy from here, so it must not be a second copy."""
    table = client.get(f"/workspaces/{workspace_id}/simulate/lattice").json()

    for row in table["sources"]:
        assert int(INTEGRITY_BY_SOURCE[row["source_kind"]]) == row["integrity_level"]

    by_cap = {c["capability"]: c["min_integrity_level"] for c in table["capabilities"]}
    assert by_cap == {
        "informational": int(CAPABILITY_MIN_INTEGRITY[Capability.INFORMATIONAL]),
        "suppressive": int(CAPABILITY_MIN_INTEGRITY[Capability.SUPPRESSIVE]),
        "actuating": int(CAPABILITY_MIN_INTEGRITY[Capability.ACTUATING]),
    }
