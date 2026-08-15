"""
tests/conftest.py — shared fixtures.

Every test in this suite runs against a REAL CockroachDB connection (see
CONTEXT.md non-negotiable #2: "not a mocked" retry loop, and Prompt 2's
"do not mock the database"). Point PALIMPSEST_DSN at a local or Cloud
cluster before running pytest — see database/README.md.

The only thing mocked anywhere in this suite is the Bedrock adjudicator,
and only in the one test that specifically exercises the equal-integrity
tie-break path (test_integrity_lattice.py::test_equal_integrity_invokes_adjudicator).
Embeddings use a small deterministic hash-based stub (`fake_embedding`
below) so every test — including ones that never touch Bedrock — still
exercises the real vector index and real vector search, just without an
AWS credential requirement. agent/bedrock_client.py gets its own live
smoke test in Prompt 3 for the real embedding call.
"""

from __future__ import annotations

import hashlib
import os
import random
import uuid

import psycopg
import pytest

from memory.gate import EMBED_DIMS, MemoryGate


def _dsn() -> str:
    dsn = os.environ.get("PALIMPSEST_DSN")
    if not dsn:
        pytest.fail("PALIMPSEST_DSN is not set. See database/README.md.")
    return dsn


def fake_embedding(text: str) -> list[float]:
    """
    Deterministic, hash-seeded fake embedding. Same text -> same vector,
    different text -> a different (effectively uncorrelated) vector — good
    enough to exercise real vector-index nearest-neighbor search without a
    real embedding model. Real Bedrock/Titan embeddings are smoke-tested in
    agent/bedrock_client.py (Prompt 3) and used for real in demo/seed.py.
    """
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(EMBED_DIMS)]


@pytest.fixture
def dsn() -> str:
    return _dsn()


@pytest.fixture
def workspace_id(dsn: str):
    """Creates a throwaway workspace, tears it down (cascades) after the test."""
    ws_id = str(uuid.uuid4())
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO workspaces (workspace_id, name) VALUES (%s, %s)",
                (ws_id, f"test-{ws_id[:8]}"),
            )
    yield ws_id
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM workspaces WHERE workspace_id = %s", (ws_id,))


@pytest.fixture
def agent_id(dsn: str, workspace_id: str):
    ag_id = str(uuid.uuid4())
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agents (agent_id, workspace_id, role, model_id) VALUES (%s, %s, %s, %s)",
                (ag_id, workspace_id, "triage", "test-agent"),
            )
    return ag_id


@pytest.fixture
def gate(dsn: str) -> MemoryGate:
    return MemoryGate(dsn=dsn)
