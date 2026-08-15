"""
tests/test_concurrent_admission.py — proves CONTEXT.md's non-negotiable #2
against a real cluster: atomic, serializable contradiction adjudication
under real concurrent writes, with a real 40001 retry loop (not mocked).

Two threads simultaneously admit OPPOSING claims about the same
(subject_key, predicate) from equal-integrity sources. CockroachDB's
SERIALIZABLE isolation guarantees the two admit() calls cannot both
observe "no incumbent exists" and both insert an active row — one of them
must either lose the race at the SELECT ... FOR UPDATE step (and go
through contradiction adjudication as the challenger) or hit a genuine
40001 SerializationFailure and transparently retry via
memory.gate._with_retry. Either outcome is correct; what's NOT correct is
ending up with two simultaneously 'active' memories for the same claim
identity, which would mean the isolation guarantee silently failed.
"""

from __future__ import annotations

import logging
import threading
import time

import psycopg

from memory.gate import Claim, MemoryGate, Provenance
from memory.lattice import Capability

from .conftest import fake_embedding


def _safe(s: str) -> str:
    """
    CockroachDB's raw transaction-retry error text embeds row key bytes
    (arbitrary binary, since a UUID primary key can decode to any
    codepoint) — printing it verbatim can raise UnicodeEncodeError on a
    Windows console using a legacy code page. Never let a diagnostic print
    crash the test that's reporting real, useful information.
    """
    return s.encode("ascii", errors="backslashreplace").decode("ascii")


def _active_memories_for(dsn: str, workspace_id: str, subject_key: str, predicate: str):
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT memory_id, object_value FROM memories "
                "WHERE workspace_id = %s AND subject_key = %s AND predicate = %s AND status = 'active'",
                (workspace_id, subject_key, predicate),
            )
            return cur.fetchall()


def test_concurrent_opposing_admits_resolve_to_one_active_belief(dsn: str, workspace_id: str, caplog):
    subject_key, predicate = "ip:203.0.113.9", "classification"

    def adjudicate(incumbent: dict, challenger: dict) -> dict:
        # A real Bedrock call would race just as badly here; a fast
        # deterministic stand-in keeps this test fast without weakening
        # what it proves about the CockroachDB transaction boundary, which
        # is what's actually under test.
        return {
            "winner": "incumbent",
            "rationale": "test stub: first writer holds under equal integrity",
            "adjudicator": "bedrock:test-stub",
        }

    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def make_gate() -> MemoryGate:
        return MemoryGate(dsn=dsn, adjudicate_fn=adjudicate)

    def admit_a():
        try:
            results["a"] = make_gate().admit(
                workspace_id=workspace_id,
                agent_id=None,
                claim=Claim(subject_key, predicate, "benign_vendor_scanner"),
                provenance=Provenance(source_kind="verified_tool", tool_name="scanner-db-a", signed=True),
                capability=Capability.INFORMATIONAL,
                confidence=0.8,
                embedding=fake_embedding("203.0.113.9 benign_vendor_scanner"),
            )
        except BaseException as e:  # noqa: BLE001 - captured to fail the test with full context
            errors["a"] = e

    def admit_b():
        try:
            results["b"] = make_gate().admit(
                workspace_id=workspace_id,
                agent_id=None,
                claim=Claim(subject_key, predicate, "known_malicious_scanner"),
                provenance=Provenance(source_kind="verified_tool", tool_name="scanner-db-b", signed=True),
                capability=Capability.INFORMATIONAL,
                confidence=0.8,
                embedding=fake_embedding("203.0.113.9 known_malicious_scanner"),
            )
        except BaseException as e:  # noqa: BLE001
            errors["b"] = e

    caplog.set_level(logging.WARNING, logger="palimpsest.gate")

    t_a = threading.Thread(target=admit_a)
    t_b = threading.Thread(target=admit_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=30)
    t_b.join(timeout=30)

    assert not errors, _safe(f"admit() raised unexpectedly under concurrency: {errors}")
    assert "a" in results and "b" in results, "both threads must complete"

    active = _active_memories_for(dsn, workspace_id, subject_key, predicate)
    print(f"\n[concurrent admission] final active memories for ({subject_key}, {predicate}): {active}")
    assert len(active) == 1, (
        f"expected exactly one active belief after concurrent opposing admits, found {len(active)}: {active}"
    )

    retry_lines = [r for r in caplog.records if "SerializationFailure" in r.getMessage()]
    if retry_lines:
        print(f"[concurrent admission] retry loop exercised: {len(retry_lines)} retry log line(s)")
        for r in retry_lines:
            print(f"  {_safe(r.getMessage())}")
    else:
        print(
            "[concurrent admission] no 40001 observed this run (row-level locking via "
            "SELECT ... FOR UPDATE serialized the two admits without a client-visible "
            "conflict) — final state is still consistent, which is the property under test."
        )

    a_status, b_status = results["a"].status, results["b"].status
    print(f"[concurrent admission] thread A result: {a_status}, thread B result: {b_status}")
    statuses = {a_status, b_status}
    # Exactly one side ends up 'active'; the other is either 'quarantined'
    # (lost the tie-break) or 'corroborated' is impossible here (opposing
    # claims), so the only valid pairing is {'active', 'quarantined'} —
    # unless one admit landed as the very first row with no contention at
    # all and the other became its challenger, which is the same pairing.
    assert statuses == {"active", "quarantined"}, f"unexpected status pairing: {statuses}"


def test_retry_loop_fires_under_forced_write_write_contention(dsn: str, workspace_id: str, caplog):
    """
    test_concurrent_opposing_admits_resolve_to_one_active_belief above proves
    the correctness property (exactly one consistent final state) but, on
    any given run, CockroachDB's row-level locking on SELECT ... FOR UPDATE
    may serialize the two admits without either one ever observing a
    client-visible 40001 — so that test alone can't prove the retry branch
    in memory.gate._with_retry is more than dead code.

    A blind UPDATE with no prior read in the same transaction turns out not
    to work for forcing this deterministically either: CockroachDB resolves
    those via write-intent queuing at the storage layer, with no client-
    visible abort (confirmed empirically — an earlier version of this test
    raced 8 threads doing exactly that and never observed a single 40001).
    A genuine, client-visible SerializationFailure needs the classic
    recipe instead: each transaction SELECTs the row first (establishing a
    read timestamp), THEN writes. If another transaction commits a write to
    that row in between, the earlier reader's transaction is provably stale
    and CockroachDB aborts it with 40001 rather than silently serving an
    inconsistent view. The barrier + sleep below widens the window so every
    thread's SELECT lands before any thread's UPDATE commits.
    """
    from memory.gate import _with_retry

    # Deliberately modest: enough concurrency to reliably force a genuine
    # 40001 (proven — see test file history), not so much that 5 threads
    # holding a long overlap window on ONE row would legitimately exhaust
    # memory.gate._MAX_RETRIES's backoff budget, which is correct behavior
    # for that scenario, not a bug this test should manufacture.
    n_threads = 3
    start_barrier = threading.Barrier(n_threads)
    results: dict[int, str] = {}
    errors: dict[int, BaseException] = {}

    def work_factory(new_name: str):
        def work(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM workspaces WHERE workspace_id = %s", (workspace_id,))
                cur.fetchone()
                time.sleep(0.15)  # widen the overlap window between read and write
                cur.execute(
                    "UPDATE workspaces SET name = %s WHERE workspace_id = %s",
                    (new_name, workspace_id),
                )

        return work

    def run(i: int):
        try:
            start_barrier.wait(timeout=15)
            with psycopg.connect(dsn, autocommit=False) as conn:
                _with_retry(conn, work_factory(f"stress-{i}"))
            results[i] = "ok"
        except BaseException as e:  # noqa: BLE001
            errors[i] = e

    caplog.set_level(logging.WARNING, logger="palimpsest.gate")
    threads = [threading.Thread(target=run, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, _safe(f"_with_retry failed to absorb contention for {len(errors)}/{n_threads} threads: {errors}")
    assert len(results) == n_threads, "every thread must eventually succeed"

    retry_lines = [r for r in caplog.records if "SerializationFailure" in r.getMessage()]
    print(f"\n[retry loop] {n_threads} threads raced SELECT-then-UPDATE on one row.")
    print(f"[retry loop] 40001 SerializationFailure observed and retried {len(retry_lines)} time(s):")
    for r in retry_lines:
        print(f"  {_safe(r.getMessage())}")
    assert retry_lines, (
        f"expected at least one genuine 40001 among {n_threads} threads racing a read-then-write "
        "on the same row — if this ever fails, rerun; if it persists, this is worth investigating "
        "rather than loosening the assertion."
    )
