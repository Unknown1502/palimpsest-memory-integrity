"""
memory/gate.py — MemoryGate: the only write path into `memories`.

No other module may INSERT INTO memories, UPDATE memories, or write to
memory_ledger. See FILE_STRUCTURE.md ("The one rule that matters more than
the tree"). agent/, api/, console/ all call through admit() / retrieve() /
revoke() / blast_radius() / belief_state_at().

Implements CONTEXT.md's three non-negotiables:

  1. Integrity lattice (Biba no-write-up) — checked in memory/lattice.py
     BEFORE any database connection is opened, and re-enforced by
     database/schema.sql's CHECK constraints as a second, independent
     layer (defense in depth: the DB constraint still holds even if some
     future caller bypasses this module).
  2. Atomic contradiction adjudication — detection, arbitration, and write
     happen inside one SERIALIZABLE transaction, with a real retry loop on
     CockroachDB's 40001 (SerializationFailure). This is expected, routine
     behavior under concurrent writes, not an error path to work around.
  3. Rewind primitives — blast_radius() and belief_state_at() (AS OF SYSTEM
     TIME) give api/routes/rewind.py everything it needs.

This module has zero dependency on agent/ (no boto3, no Bedrock import).
The Bedrock-backed tie-break adjudicator is injected by the caller as a
plain callable — see `adjudicate_fn` on MemoryGate.__init__ — so this
module stays testable against a real CockroachDB connection without any
AWS credentials, and agent/bedrock_client.py stays the only place that
talks to Bedrock.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Optional, Sequence

import psycopg
from psycopg import errors as pg_errors

from memory.lattice import (
    CAPABILITY_MIN_INTEGRITY,
    CAPABILITY_TO_STR,
    INTEGRITY_BY_SOURCE,
    Capability,
    Integrity,
    IntegrityViolation,
    check_capability_allowed,
)

logger = logging.getLogger("palimpsest.gate")

EMBED_DIMS = 1024  # Titan Text Embeddings V2. Verified against a live call
# in agent/bedrock_client.py's smoke test — a silent dimension mismatch
# here would corrupt every vector index write, so admit() hard-fails if a
# caller ever passes a vector of the wrong length.

GENESIS_HASH = "0" * 64  # prev_hash of seq=0 in memory_ledger, per workspace.

_MAX_RETRIES = 5
_HLC_RE = re.compile(r"^-?\d+(\.\d+)?$")


# =============================================================================
# Data model
# =============================================================================


@dataclass(frozen=True)
class Provenance:
    """Where a claim came from. source_kind drives the integrity lattice."""

    source_kind: str  # one of INTEGRITY_BY_SOURCE's keys
    ticket_ref: Optional[str] = None
    tool_name: Optional[str] = None
    signed: Optional[bool] = None
    operator_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.source_kind not in INTEGRITY_BY_SOURCE:
            raise ValueError(f"unknown source_kind: {self.source_kind!r}")

    @property
    def integrity(self) -> Integrity:
        return INTEGRITY_BY_SOURCE[self.source_kind]

    def to_jsonb(self) -> dict:
        return {
            "ticket_ref": self.ticket_ref,
            "tool_name": self.tool_name,
            "signed": self.signed,
            "operator_id": self.operator_id,
        }


@dataclass(frozen=True)
class Claim:
    """The identity of a belief: what it's about, and what it asserts."""

    subject_key: str
    predicate: str
    object_value: str
    polarity: str = "assert"

    def __post_init__(self) -> None:
        if self.polarity not in ("assert", "negate"):
            raise ValueError(f"polarity must be 'assert' or 'negate', got {self.polarity!r}")

    def render(self) -> str:
        verb = "NOT " if self.polarity == "negate" else ""
        return f"{self.subject_key} {self.predicate} {verb}{self.object_value}"


@dataclass(frozen=True)
class AdmitResult:
    memory_id: str
    status: str  # 'active' | 'quarantined' | 'corroborated'
    contradiction: Optional[dict] = None


@dataclass(frozen=True)
class RetrievedMemory:
    memory_id: str
    claim: str
    subject_key: str
    predicate: str
    object_value: str
    status: str
    source_kind: str
    integrity_level: int
    capability_ceiling: str
    rank: int
    semantic_score: float
    eff_confidence: float
    total_score: float
    influence: float


AdjudicateFn = Callable[[dict, dict], dict]
# adjudicate_fn(incumbent: dict, challenger: dict) -> {
#     "winner": "incumbent" | "challenger",
#     "rationale": str,
#     "adjudicator": str,   # e.g. "bedrock:anthropic.claude-..."
# }


def _default_adjudicate(incumbent: dict, challenger: dict) -> dict:
    """
    Used when no adjudicate_fn is injected. Equal-integrity contradictions
    are safety-critical — silently letting either side win without an
    arbiter is wrong, so the default-safe policy is: the incumbent holds,
    the challenger is quarantined for human review. Real deployments
    inject agent.bedrock_client.adjudicate here instead.
    """
    return {
        "winner": "incumbent",
        "rationale": (
            "No adjudicate_fn configured; equal-integrity contradiction "
            "routed to human review by default-safe policy."
        ),
        "adjudicator": "rule:no_adjudicator_configured",
    }


# =============================================================================
# Vector helpers
# =============================================================================


def _normalize(embedding: Sequence[float]) -> list[float]:
    """L2-normalise so <-> (L2) is rank-equivalent to cosine distance."""
    norm = math.sqrt(sum(x * x for x in embedding))
    if norm == 0:
        raise ValueError("cannot normalize a zero vector")
    return [x / norm for x in embedding]


def _vec_literal(embedding: Sequence[float]) -> str:
    """Render as a CockroachDB VECTOR literal, e.g. '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.12f}" for x in embedding) + "]"


def _validate_hlc(hlc: Any) -> str:
    """
    AS OF SYSTEM TIME requires a literal, not a bound parameter, in a plain
    SQL statement — so the HLC value must be interpolated directly into the
    query text. Every value passed here originates from decisions.decided_hlc
    (a DECIMAL this codebase wrote itself via cluster_logical_timestamp()),
    never from raw user input, but we still validate the shape strictly
    before interpolating: only digits, an optional leading '-', and an
    optional decimal point are accepted. Anything else raises.
    """
    s = str(hlc)
    if not _HLC_RE.match(s):
        raise ValueError(f"invalid HLC timestamp: {hlc!r}")
    return s


# =============================================================================
# MemoryGate
# =============================================================================


class MemoryGate:
    def __init__(
        self,
        dsn: str,
        adjudicate_fn: Optional[AdjudicateFn] = None,
        gate_enabled: Optional[bool] = None,
    ) -> None:
        self.dsn = dsn
        self.adjudicate_fn: AdjudicateFn = adjudicate_fn or _default_adjudicate
        if gate_enabled is None:
            gate_enabled = os.environ.get("PALIMPSEST_GATE_ENABLED", "true").strip().lower() not in (
                "false",
                "0",
                "no",
            )
        self.gate_enabled = gate_enabled

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, autocommit=False)

    # -------------------------------------------------------------------
    # admit
    # -------------------------------------------------------------------

    def admit(
        self,
        *,
        workspace_id: str,
        agent_id: Optional[str],
        claim: Claim,
        provenance: Provenance,
        capability: Capability,
        confidence: float,
        embedding: Sequence[float],
    ) -> AdmitResult:
        if len(embedding) != EMBED_DIMS:
            raise ValueError(f"embedding has {len(embedding)} dims, expected {EMBED_DIMS}")
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {confidence}")

        integrity = provenance.integrity

        # Biba no-write-up. This raises BEFORE _connect() is ever called —
        # a rejected admission touches the database zero times.
        check_capability_allowed(integrity, capability)

        normalized = _normalize(embedding)
        eff_confidence = (int(integrity) / int(Integrity.HUMAN_CONFIRMED)) * confidence
        claim_text = claim.render()

        def work(conn: psycopg.Connection) -> AdmitResult:
            return self._admit_tx(
                conn,
                workspace_id=workspace_id,
                agent_id=agent_id,
                claim=claim,
                provenance=provenance,
                integrity=integrity,
                capability=capability,
                confidence=confidence,
                eff_confidence=eff_confidence,
                embedding=normalized,
                claim_text=claim_text,
            )

        with self._connect() as conn:
            return _with_retry(conn, work)

    def _admit_tx(
        self,
        conn: psycopg.Connection,
        *,
        workspace_id: str,
        agent_id: Optional[str],
        claim: Claim,
        provenance: Provenance,
        integrity: Integrity,
        capability: Capability,
        confidence: float,
        eff_confidence: float,
        embedding: list[float],
        claim_text: str,
    ) -> AdmitResult:
        cur = conn.cursor()
        vec = _vec_literal(embedding)

        cur.execute(
            """
            SELECT memory_id, object_value, polarity, integrity_level
            FROM memories
            WHERE workspace_id = %s AND subject_key = %s AND predicate = %s AND status = 'active'
            FOR UPDATE
            """,
            (workspace_id, claim.subject_key, claim.predicate),
        )
        incumbent = cur.fetchone()

        if incumbent is None:
            memory_id = self._insert_memory(
                cur,
                workspace_id=workspace_id,
                claim=claim,
                claim_text=claim_text,
                provenance=provenance,
                integrity=integrity,
                capability=capability,
                confidence=confidence,
                eff_confidence=eff_confidence,
                vec=vec,
                status="active",
            )
            self._append_ledger(
                cur,
                workspace_id,
                "admit",
                {
                    "memory_id": memory_id,
                    "claim": claim_text,
                    "source_kind": provenance.source_kind,
                    "integrity_level": int(integrity),
                    "capability_ceiling": CAPABILITY_TO_STR[capability],
                },
            )
            return AdmitResult(memory_id=memory_id, status="active")

        incumbent_id, incumbent_object, incumbent_polarity, incumbent_integrity = incumbent

        if incumbent_object == claim.object_value and incumbent_polarity == claim.polarity:
            # Corroboration, not a contradiction: same claim, another source.
            cur.execute(
                "UPDATE memories SET corroborations = corroborations + 1, updated_at = now() "
                "WHERE workspace_id = %s AND memory_id = %s",
                (workspace_id, incumbent_id),
            )
            self._append_ledger(
                cur,
                workspace_id,
                "admit",
                {
                    "memory_id": str(incumbent_id),
                    "event": "corroborated",
                    "by_source_kind": provenance.source_kind,
                },
            )
            return AdmitResult(memory_id=str(incumbent_id), status="corroborated")

        # Contradiction: same (subject_key, predicate), different claim.
        challenger_id = self._insert_memory(
            cur,
            workspace_id=workspace_id,
            claim=claim,
            claim_text=claim_text,
            provenance=provenance,
            integrity=integrity,
            capability=capability,
            confidence=confidence,
            eff_confidence=eff_confidence,
            vec=vec,
            status="quarantined",  # provisional; corrected below if it wins
        )

        incumbent_dict = {
            "memory_id": str(incumbent_id),
            "object_value": incumbent_object,
            "polarity": incumbent_polarity,
            "integrity_level": int(incumbent_integrity),
        }
        challenger_dict = {
            "memory_id": challenger_id,
            "object_value": claim.object_value,
            "polarity": claim.polarity,
            "integrity_level": int(integrity),
        }

        if int(integrity) > int(incumbent_integrity):
            verdict, adjudicator, rationale, winner = (
                "supersede",
                "rule:integrity_dominance",
                f"challenger integrity {int(integrity)} > incumbent integrity {int(incumbent_integrity)}",
                "challenger",
            )
        elif int(integrity) < int(incumbent_integrity):
            verdict, adjudicator, rationale, winner = (
                "quarantine",
                "rule:integrity_subordinate",
                f"integrity {int(integrity)} cannot override integrity {int(incumbent_integrity)} without adjudication",
                "incumbent",
            )
        else:
            decision = self.adjudicate_fn(incumbent_dict, challenger_dict)
            winner = decision["winner"]
            adjudicator = decision.get("adjudicator", "bedrock:unknown")
            rationale = decision["rationale"]
            verdict = "llm_adjudicated"

        if winner == "challenger":
            cur.execute(
                "UPDATE memories SET status = 'active', updated_at = now() "
                "WHERE workspace_id = %s AND memory_id = %s",
                (workspace_id, challenger_id),
            )
            cur.execute(
                "UPDATE memories SET status = 'superseded', updated_at = now() "
                "WHERE workspace_id = %s AND memory_id = %s",
                (workspace_id, incumbent_id),
            )
            result_status = "active"
            self._append_ledger(
                cur,
                workspace_id,
                "supersede",
                {"superseded_memory_id": str(incumbent_id), "by_memory_id": challenger_id, "adjudicator": adjudicator},
            )
            self._append_ledger(
                cur,
                workspace_id,
                "admit",
                {"memory_id": challenger_id, "claim": claim_text, "adjudicator": adjudicator},
            )
        else:
            # challenger stays 'quarantined' as inserted; incumbent untouched.
            result_status = "quarantined"
            self._append_ledger(
                cur,
                workspace_id,
                "quarantine",
                {"memory_id": challenger_id, "claim": claim_text, "rationale": rationale, "adjudicator": adjudicator},
            )
            cur.execute(
                "INSERT INTO approvals (workspace_id, subject_type, subject_id, reason) "
                "VALUES (%s, 'memory', %s, %s)",
                (workspace_id, challenger_id, rationale),
            )

        cur.execute(
            "INSERT INTO contradictions "
            "(workspace_id, incumbent_memory_id, challenger_memory_id, verdict, adjudicator, rationale) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (workspace_id, incumbent_id, challenger_id, verdict, adjudicator, rationale),
        )

        return AdmitResult(
            memory_id=challenger_id,
            status=result_status,
            contradiction={
                "incumbent_memory_id": str(incumbent_id),
                "verdict": verdict,
                "adjudicator": adjudicator,
                "rationale": rationale,
            },
        )

    def _insert_memory(
        self,
        cur: psycopg.Cursor,
        *,
        workspace_id: str,
        claim: Claim,
        claim_text: str,
        provenance: Provenance,
        integrity: Integrity,
        capability: Capability,
        confidence: float,
        eff_confidence: float,
        vec: str,
        status: str,
    ) -> str:
        cur.execute(
            """
            INSERT INTO memories (
                workspace_id, status, subject_key, predicate, object_value, polarity, claim,
                source_kind, integrity_level, capability_ceiling, provenance,
                confidence, eff_confidence, embedding
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s::VECTOR
            )
            RETURNING memory_id
            """,
            (
                workspace_id,
                status,
                claim.subject_key,
                claim.predicate,
                claim.object_value,
                claim.polarity,
                claim_text,
                provenance.source_kind,
                int(integrity),
                CAPABILITY_TO_STR[capability],
                json.dumps(provenance.to_jsonb()),
                confidence,
                eff_confidence,
                vec,
            ),
        )
        return str(cur.fetchone()[0])

    # -------------------------------------------------------------------
    # retrieve
    # -------------------------------------------------------------------

    def retrieve(
        self,
        *,
        workspace_id: str,
        embedding: Sequence[float],
        capability: Capability,
        top_k: int = 5,
    ) -> list[RetrievedMemory]:
        if len(embedding) != EMBED_DIMS:
            raise ValueError(f"embedding has {len(embedding)} dims, expected {EMBED_DIMS}")

        normalized = _normalize(embedding)
        vec = _vec_literal(normalized)

        # Two independent filters, deliberately redundant with each other —
        # mirrors schema.sql's two separate CHECK constraints
        # (source_integrity_consistent, capability_requires_integrity).
        #
        # capability_ceiling is the PRIMARY filter: it's a per-memory choice
        # made at admit time, and may be lower than what the source's
        # integrity would allow (e.g. a human_confirmed source can still
        # write an informational-only memory that must never be used to
        # justify a suppress decision). Filtering on integrity_level alone
        # would miss this — a high-integrity, low-capability memory would
        # wrongly leak into a high-capability retrieval.
        #
        # integrity_level is kept as a second, redundant filter: since
        # capability_requires_integrity guarantees capability_ceiling can
        # never exceed what integrity_level allows, this filter is
        # logically implied by the first — but enforcing it independently
        # here means a future bug in the capability_ceiling filter (or a
        # row that somehow bypassed the CHECK) still can't leak a
        # low-integrity belief into a high-capability decision.
        capability_rank_case = (
            "CASE capability_ceiling "
            "WHEN 'informational' THEN 1 WHEN 'suppressive' THEN 2 WHEN 'actuating' THEN 3 END"
        )

        if not self.gate_enabled:
            # This bypass exists ONLY to prove the attack scenario in
            # demo/attack_scenario.py Phase 3A. Never enable in a real
            # deployment — it skips BOTH lattice filters entirely, which is
            # the entire point of the lattice.
            logger.warning(
                "PALIMPSEST_GATE_ENABLED=false — integrity/capability filter BYPASSED on "
                "this retrieve() call. This path exists only to demonstrate the attack; "
                "it must never be enabled outside the controlled demo."
            )
            min_integrity = int(Integrity.UNTRUSTED_INGEST)
            min_capability_rank = 1
        else:
            min_integrity = int(CAPABILITY_MIN_INTEGRITY[capability])
            min_capability_rank = int(capability)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT memory_id, claim, subject_key, predicate, object_value, status,
                           source_kind, integrity_level, capability_ceiling, eff_confidence,
                           embedding <-> %s::VECTOR AS distance
                    FROM memories
                    WHERE workspace_id = %s AND status = 'active' AND integrity_level >= %s
                          AND ({capability_rank_case}) >= %s
                    ORDER BY embedding <-> %s::VECTOR
                    LIMIT %s
                    """,
                    (vec, workspace_id, min_integrity, min_capability_rank, vec, top_k),
                )
                rows = cur.fetchall()

        scored = []
        for row in rows:
            (
                memory_id,
                claim_text,
                subject_key,
                predicate,
                object_value,
                status,
                source_kind,
                integrity_level,
                capability_ceiling,
                eff_confidence,
                distance,
            ) = row
            semantic_score = 1.0 / (1.0 + float(distance))
            total_score = semantic_score * float(eff_confidence)
            scored.append(
                {
                    "memory_id": str(memory_id),
                    "claim": claim_text,
                    "subject_key": subject_key,
                    "predicate": predicate,
                    "object_value": object_value,
                    "status": status,
                    "source_kind": source_kind,
                    "integrity_level": integrity_level,
                    "capability_ceiling": capability_ceiling,
                    "semantic_score": semantic_score,
                    "eff_confidence": float(eff_confidence),
                    "total_score": total_score,
                }
            )

        score_sum = sum(s["total_score"] for s in scored) or 1.0
        results = []
        for rank, s in enumerate(scored, start=1):
            results.append(
                RetrievedMemory(
                    memory_id=s["memory_id"],
                    claim=s["claim"],
                    subject_key=s["subject_key"],
                    predicate=s["predicate"],
                    object_value=s["object_value"],
                    status=s["status"],
                    source_kind=s["source_kind"],
                    integrity_level=s["integrity_level"],
                    capability_ceiling=s["capability_ceiling"],
                    rank=rank,
                    semantic_score=s["semantic_score"],
                    eff_confidence=s["eff_confidence"],
                    total_score=s["total_score"],
                    influence=s["total_score"] / score_sum,
                )
            )
        return results

    # -------------------------------------------------------------------
    # revoke / blast_radius / belief_state_at
    # -------------------------------------------------------------------

    def revoke(self, *, workspace_id: str, memory_id: str, reason: str, actor: str) -> dict:
        def work(conn: psycopg.Connection) -> None:
            cur = conn.cursor()
            cur.execute(
                "SELECT status FROM memories WHERE workspace_id = %s AND memory_id = %s FOR UPDATE",
                (workspace_id, memory_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"memory {memory_id} not found in workspace {workspace_id}")
            prior_status = row[0]
            cur.execute(
                "UPDATE memories SET status = 'revoked', updated_at = now() "
                "WHERE workspace_id = %s AND memory_id = %s",
                (workspace_id, memory_id),
            )
            self._append_ledger(
                cur,
                workspace_id,
                "revoke",
                {"memory_id": memory_id, "reason": reason, "actor": actor, "prior_status": prior_status},
            )

        with self._connect() as conn:
            _with_retry(conn, work)

        blast = self.blast_radius(workspace_id=workspace_id, memory_id=memory_id)
        return {
            "memory_id": memory_id,
            "status": "revoked",
            "reason": reason,
            "actor": actor,
            "blast_radius": blast,
            "blast_radius_count": len(blast),
        }

    def blast_radius(self, *, workspace_id: str, memory_id: str) -> list[dict]:
        """Every decision that ever cited `memory_id` as evidence."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT d.decision_id, d.alert_ref, d.verdict, d.decided_hlc, d.created_at
                    FROM decision_memory_refs dmr
                    JOIN decisions d ON d.decision_id = dmr.decision_id
                    WHERE dmr.memory_id = %s AND d.workspace_id = %s
                    ORDER BY d.created_at
                    """,
                    (memory_id, workspace_id),
                )
                rows = cur.fetchall()
        return [
            {
                "decision_id": str(r[0]),
                "alert_ref": r[1],
                "verdict": r[2],
                "decided_hlc": str(r[3]),
                "created_at": r[4].isoformat(),
            }
            for r in rows
        ]

    def belief_state_at(self, *, workspace_id: str, hlc: Any) -> list[dict]:
        """
        Reconstruct exactly what the belief store looked like at `hlc`, via
        AS OF SYSTEM TIME. Bounded by gc.ttlseconds (see database/README.md);
        if the read fails because the timestamp has been garbage collected,
        callers should fall back to memory/ledger_replay.py's
        replay_state_at(), which serves the same output shape from
        memory_ledger instead of MVCC history.
        """
        hlc_literal = _validate_hlc(hlc)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT memory_id, status, claim, subject_key, predicate, object_value,
                           source_kind, integrity_level, capability_ceiling
                    FROM memories AS OF SYSTEM TIME {hlc_literal}
                    WHERE workspace_id = %s
                    """,
                    (workspace_id,),
                )
                rows = cur.fetchall()
        return [
            {
                "memory_id": str(r[0]),
                "status": r[1],
                "claim": r[2],
                "subject_key": r[3],
                "predicate": r[4],
                "object_value": r[5],
                "source_kind": r[6],
                "integrity_level": r[7],
                "capability_ceiling": r[8],
            }
            for r in rows
        ]

    # -------------------------------------------------------------------
    # ledger
    # -------------------------------------------------------------------

    def _append_ledger(self, cur: psycopg.Cursor, workspace_id: str, event_type: str, payload: dict) -> dict:
        """
        Append one hash-chained row to memory_ledger. MUST be called with a
        cursor that belongs to the SAME transaction as the state change it
        is recording — the ledger entry and the state change commit or
        abort together, never independently.
        """
        cur.execute("SELECT COALESCE(MAX(seq), -1) FROM memory_ledger WHERE workspace_id = %s", (workspace_id,))
        seq = cur.fetchone()[0] + 1

        if seq == 0:
            prev_hash = GENESIS_HASH
        else:
            cur.execute(
                "SELECT entry_hash FROM memory_ledger WHERE workspace_id = %s AND seq = %s",
                (workspace_id, seq - 1),
            )
            prev_hash = cur.fetchone()[0]

        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        entry_hash = hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()

        cur.execute(
            "INSERT INTO memory_ledger (seq, workspace_id, event_type, payload, prev_hash, entry_hash) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (seq, workspace_id, event_type, json.dumps(payload, default=str), prev_hash, entry_hash),
        )
        return {"seq": seq, "entry_hash": entry_hash}


# =============================================================================
# Retry loop for CockroachDB's 40001 (SerializationFailure)
# =============================================================================


def _with_retry(conn: psycopg.Connection, work: Callable[[psycopg.Connection], Any]) -> Any:
    """
    Real retry loop, not a mocked one, per CONTEXT.md's non-negotiable #2.
    SerializationFailure (40001) under concurrent writes to overlapping
    data is expected, routine CockroachDB behavior — applications are
    expected to retry with backoff, not to lower isolation to avoid it.
    """
    last_err: Optional[BaseException] = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with conn.transaction():
                return work(conn)
        except pg_errors.SerializationFailure as e:
            last_err = e
            if attempt == _MAX_RETRIES:
                break
            backoff = min(0.05 * (2**attempt), 2.0) + random.uniform(0, 0.05)
            logger.warning(
                "40001 SerializationFailure on attempt %d/%d, retrying in %.3fs: %s",
                attempt,
                _MAX_RETRIES,
                backoff,
                e,
            )
            time.sleep(backoff)
    assert last_err is not None
    raise last_err
