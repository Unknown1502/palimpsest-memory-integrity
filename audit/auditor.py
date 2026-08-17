"""
audit/auditor.py — an independent, read-only Memory Auditor.

    python -m audit.auditor                      # audits the busiest workspace
    python -m audit.auditor --workspace <uuid>
    python -m audit.auditor --print-sql          # emit the queries, run nothing

Palimpsest does not ask its own application layer whether memory is safe.
`GET /ledger/verify` is the API grading its own homework: if the API layer
were compromised or simply wrong, it would return `{"valid": true}` just as
happily. This module is the independent check — it talks to CockroachDB
directly and re-derives every conclusion from the stored rows.

Three deliberate design choices make "independent" mean something:

  1. READ-ONLY IS ENFORCED BY THE DATABASE, NOT BY THIS CODE. Every
     connection opens with `SET default_transaction_read_only = on`, so a
     mutation attempted through the auditor is refused by CockroachDB with
     ReadOnlySqlTransaction, not by an if-statement here that a future
     refactor could drop. See tests/test_auditor.py, which asserts exactly
     that against a live cluster.

  2. NOTHING IS IMPORTED FROM THE CODE UNDER AUDIT. The integrity lattice
     and the genesis hash are restated below rather than imported from
     memory/lattice.py and memory/gate.py. An auditor that imports its
     subject's definition of "correct" cannot detect a wrong definition —
     it would agree with the bug. Duplication is the point. If these two
     copies ever disagree, that disagreement IS the finding.

  3. EVERY CHECK IS A LABELED SQL STRING. The `sql` field on each Check is
     the literal query run, exposed so it can be pasted into any
     MCP-compatible client (Claude Code, Cursor, VS Code) pointed at the
     CockroachDB Cloud Managed MCP Server and re-run by a third party with
     this repo entirely out of the loop. `--print-sql` dumps them all.
     That is the intended verification path for a judge: don't trust this
     module either — take its queries and run them yourself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

# --- Restated policy. Deliberately NOT imported. See docstring note 2. ---

GENESIS_HASH = "0" * 64

# A capability may be held only by a source of at least this integrity.
# Mirrors database/schema.sql's capability_requires_integrity CHECK.
CAPABILITY_MIN_INTEGRITY: dict[str, int] = {
    "informational": 1,
    "suppressive": 3,
    "actuating": 4,
}

# Mirrors database/schema.sql's source_integrity_consistent CHECK.
SOURCE_INTEGRITY: dict[str, int] = {
    "untrusted_ingest": 1,
    "agent_inferred": 2,
    "verified_tool": 3,
    "human_confirmed": 4,
}


@dataclass(frozen=True)
class Check:
    name: str
    question: str
    sql: str
    # "violation": any row returned is a policy breach and must be zero.
    # "inventory": rows are context for a human, not a failure.
    kind: str = "violation"


CHECKS: list[Check] = [
    Check(
        name="capability_exceeds_integrity",
        question="Does any belief hold a capability its source authority does not permit?",
        kind="violation",
        sql="""
SELECT memory_id, claim, source_kind, integrity_level, capability_ceiling, status
FROM memories
WHERE workspace_id = %(workspace_id)s
  AND (   (capability_ceiling = 'suppressive' AND integrity_level < 3)
       OR (capability_ceiling = 'actuating'   AND integrity_level < 4))
ORDER BY integrity_level ASC
""".strip(),
    ),
    Check(
        name="source_integrity_mismatch",
        question="Does any belief's integrity_level disagree with its declared source_kind?",
        kind="violation",
        sql="""
SELECT memory_id, source_kind, integrity_level, capability_ceiling, status
FROM memories
WHERE workspace_id = %(workspace_id)s
  AND integrity_level <> CASE source_kind
        WHEN 'untrusted_ingest' THEN 1
        WHEN 'agent_inferred'   THEN 2
        WHEN 'verified_tool'    THEN 3
        WHEN 'human_confirmed'  THEN 4 END
ORDER BY source_kind
""".strip(),
    ),
    Check(
        name="untrusted_influenced_suppression",
        question=(
            "Did an untrusted-source belief ever contribute evidence to a decision "
            "that silenced an alert?"
        ),
        kind="violation",
        sql="""
SELECT d.decision_id, d.alert_ref, d.verdict, d.decided_hlc,
       m.memory_id, m.source_kind, m.integrity_level, m.capability_ceiling,
       r.influence
FROM decisions d
JOIN decision_memory_refs r ON r.decision_id = d.decision_id
JOIN memories m             ON m.memory_id  = r.memory_id
WHERE d.workspace_id = %(workspace_id)s
  AND d.verdict = 'suppress'
  AND m.integrity_level < 3
ORDER BY r.influence DESC
""".strip(),
    ),
    Check(
        name="revoked_memory_still_cited",
        question="Which past decisions cited a belief that has since been revoked?",
        kind="inventory",
        sql="""
SELECT m.memory_id, m.claim, m.status,
       d.decision_id, d.alert_ref, d.verdict, d.decided_hlc, r.influence
FROM memories m
JOIN decision_memory_refs r ON r.memory_id  = m.memory_id
JOIN decisions d            ON d.decision_id = r.decision_id
WHERE m.workspace_id = %(workspace_id)s
  AND m.status = 'revoked'
ORDER BY d.decided_hlc ASC
""".strip(),
    ),
    Check(
        name="suppression_capable_beliefs",
        question="Which active beliefs are currently able to silence an alert or trigger action?",
        kind="inventory",
        sql="""
SELECT memory_id, claim, source_kind, integrity_level, capability_ceiling,
       eff_confidence, corroborations
FROM memories
WHERE workspace_id = %(workspace_id)s
  AND status = 'active'
  AND capability_ceiling IN ('suppressive', 'actuating')
ORDER BY integrity_level DESC
""".strip(),
    ),
    Check(
        name="quarantined_beliefs",
        question="What is sitting in quarantine awaiting human review?",
        kind="inventory",
        sql="""
SELECT m.memory_id, m.claim, m.source_kind, m.integrity_level, m.status,
       a.approval_id, a.status AS approval_status, a.reason
FROM memories m
LEFT JOIN approvals a
       ON a.subject_id = m.memory_id AND a.subject_type = 'memory'
WHERE m.workspace_id = %(workspace_id)s
  AND m.status = 'quarantined'
ORDER BY m.created_at DESC
""".strip(),
    ),
    Check(
        name="unresolved_contradictions",
        question="Which contradictions were logged but never resolved by a human?",
        kind="inventory",
        sql="""
SELECT c.contradiction_id, c.verdict, c.adjudicator, c.rationale,
       c.incumbent_memory_id, c.challenger_memory_id, ch.status AS challenger_status
FROM contradictions c
JOIN memories ch ON ch.memory_id = c.challenger_memory_id
WHERE c.workspace_id = %(workspace_id)s
  AND ch.status = 'quarantined'
ORDER BY c.created_at DESC
""".strip(),
    ),
    Check(
        name="orphaned_decision_refs",
        question="Does any decision cite a belief that no longer exists in the store?",
        kind="violation",
        sql="""
SELECT r.decision_id, r.memory_id, r.rank, r.influence
FROM decision_memory_refs r
JOIN decisions d ON d.decision_id = r.decision_id
LEFT JOIN memories m ON m.memory_id = r.memory_id
WHERE d.workspace_id = %(workspace_id)s
  AND m.memory_id IS NULL
""".strip(),
    ),
]

# The ledger chain is verified separately: it needs a hash re-derivation per
# row, which is not expressible in the labeled-SQL form above. This query
# feeds that re-derivation.
LEDGER_SQL = """
SELECT seq, event_type, payload, prev_hash, entry_hash
FROM memory_ledger
WHERE workspace_id = %(workspace_id)s
ORDER BY seq ASC
""".strip()

METRICS_SQL = """
SELECT
  (SELECT count(*) FROM memories  WHERE workspace_id = %(workspace_id)s AND status = 'active')      AS active_memories,
  (SELECT count(*) FROM memories  WHERE workspace_id = %(workspace_id)s AND status = 'revoked')     AS revoked_memories,
  (SELECT count(*) FROM memories  WHERE workspace_id = %(workspace_id)s AND status = 'quarantined') AS quarantined_memories,
  (SELECT count(*) FROM memories  WHERE workspace_id = %(workspace_id)s AND status = 'superseded')  AS superseded_memories,
  (SELECT count(*) FROM decisions WHERE workspace_id = %(workspace_id)s)                            AS decisions,
  (SELECT count(*) FROM approvals WHERE workspace_id = %(workspace_id)s AND status = 'pending')     AS pending_approvals,
  (SELECT count(DISTINCT r.decision_id)
     FROM decision_memory_refs r
     JOIN memories m ON m.memory_id = r.memory_id
    WHERE m.workspace_id = %(workspace_id)s AND m.status = 'revoked')                               AS decisions_touching_revoked
""".strip()


@dataclass
class Finding:
    check: str
    question: str
    kind: str
    rows: list[dict]

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def is_violation(self) -> bool:
        return self.kind == "violation" and self.count > 0


@dataclass
class AuditReport:
    workspace_id: str
    metrics: dict[str, int]
    findings: list[Finding]
    ledger_valid: bool
    ledger_entries_checked: int
    ledger_broken_at_seq: Optional[int] = None
    errors: list[str] = field(default_factory=list)

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.is_violation]

    @property
    def passed(self) -> bool:
        return not self.violations and self.ledger_valid and not self.errors


def connect_read_only(dsn: str) -> psycopg.Connection:
    """
    Open a connection CockroachDB itself will refuse to let us write through.
    This is the auditor's core safety property and it is the database's
    guarantee, not this module's promise — see tests/test_auditor.py.
    """
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    conn.execute("SET default_transaction_read_only = on")
    return conn


def _verify_ledger(conn: psycopg.Connection, workspace_id: str) -> tuple[bool, int, Optional[int]]:
    """
    Re-derive the hash chain from the stored payloads, using this module's
    own genesis constant and its own canonicalization. Independent of
    memory/gate.py's writer and of api/routes/ledger.py's verifier — if
    either of those changed how it hashes, this would disagree, which is
    exactly the signal an independent audit exists to produce.
    """
    with conn.cursor() as cur:
        cur.execute(LEDGER_SQL, {"workspace_id": workspace_id})
        rows = cur.fetchall()

    expected_prev = GENESIS_HASH
    checked = 0
    for row in rows:
        checked += 1
        canonical = json.dumps(row["payload"], sort_keys=True, separators=(",", ":"), default=str)
        expected_hash = hashlib.sha256((expected_prev + canonical).encode("utf-8")).hexdigest()
        if row["prev_hash"] != expected_prev or row["entry_hash"] != expected_hash:
            return False, checked, row["seq"]
        expected_prev = row["entry_hash"]
    return True, checked, None


def busiest_workspace(dsn: str) -> Optional[str]:
    """Pick the workspace with the most decisions — the one worth auditing."""
    with connect_read_only(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT w.workspace_id, count(d.decision_id) AS n
                FROM workspaces w
                LEFT JOIN decisions d ON d.workspace_id = w.workspace_id
                GROUP BY w.workspace_id
                ORDER BY n DESC, w.created_at DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    return str(row["workspace_id"]) if row else None


def run_audit(dsn: str, workspace_id: str) -> AuditReport:
    findings: list[Finding] = []
    errors: list[str] = []

    with connect_read_only(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(METRICS_SQL, {"workspace_id": workspace_id})
            metrics = {k: int(v) for k, v in (cur.fetchone() or {}).items()}

        for check in CHECKS:
            try:
                with conn.cursor() as cur:
                    cur.execute(check.sql, {"workspace_id": workspace_id})
                    rows = [dict(r) for r in cur.fetchall()]
                findings.append(Finding(check.name, check.question, check.kind, rows))
            except Exception as e:  # noqa: BLE001 — one bad check must not void the audit
                errors.append(f"{check.name}: {type(e).__name__}: {e}")

        ledger_valid, checked, broken_at = _verify_ledger(conn, workspace_id)

    metrics["integrity_violations"] = sum(
        f.count for f in findings if f.kind == "violation"
    )
    metrics["unresolved_contradictions"] = next(
        (f.count for f in findings if f.check == "unresolved_contradictions"), 0
    )

    return AuditReport(
        workspace_id=workspace_id,
        metrics=metrics,
        findings=findings,
        ledger_valid=ledger_valid,
        ledger_entries_checked=checked,
        ledger_broken_at_seq=broken_at,
        errors=errors,
    )


def render(report: AuditReport) -> str:
    m = report.metrics
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("PALIMPSEST MEMORY AUDIT - independent, read-only, direct to CockroachDB")
    lines.append("=" * 78)
    lines.append(f"workspace: {report.workspace_id}")
    lines.append("")
    lines.append("  MEMORY INTEGRITY")
    lines.append(f"    {m.get('active_memories', 0):>6}  active beliefs")
    lines.append(f"    {m.get('quarantined_memories', 0):>6}  quarantined (awaiting human review)")
    lines.append(f"    {m.get('superseded_memories', 0):>6}  superseded")
    lines.append(f"    {m.get('revoked_memories', 0):>6}  revoked")
    lines.append(f"    {m.get('integrity_violations', 0):>6}  POLICY VIOLATIONS")
    lines.append("")
    lines.append("  DECISIONS")
    lines.append(f"    {m.get('decisions', 0):>6}  decisions on record")
    lines.append(f"    {m.get('decisions_touching_revoked', 0):>6}  cited a belief since revoked")
    lines.append(f"    {m.get('pending_approvals', 0):>6}  pending approvals")
    lines.append("")

    lines.append("  CHECKS")
    for f in report.findings:
        if f.kind == "violation":
            mark = "FAIL" if f.count else "PASS"
            detail = f"{f.count} violating row(s)" if f.count else "none"
        else:
            mark = "INFO"
            detail = f"{f.count} row(s)"
        lines.append(f"    [{mark}] {f.check:<32} {detail}")
        lines.append(f"           {f.question}")
    lines.append("")

    if report.ledger_valid:
        lines.append(f"  LEDGER  [PASS] hash chain re-derived independently, "
                     f"{report.ledger_entries_checked} entries, unbroken")
    else:
        lines.append(f"  LEDGER  [FAIL] chain broken at seq={report.ledger_broken_at_seq} "
                     f"after {report.ledger_entries_checked} entries")
    lines.append("")

    for f in report.violations:
        lines.append(f"  VIOLATION DETAIL - {f.check}")
        for row in f.rows[:10]:
            lines.append(f"    {row}")
        lines.append("")

    if report.errors:
        lines.append("  ERRORS")
        for e in report.errors:
            lines.append(f"    {e}")
        lines.append("")

    lines.append("  VERDICT: " + ("PASS - memory integrity policy holds" if report.passed
                                  else "FAIL - see violations above"))
    lines.append("=" * 78)
    return "\n".join(lines)


def print_sql() -> None:
    print("-- Palimpsest memory audit queries.")
    print("-- Read-only. Paste into any MCP client pointed at the CockroachDB Cloud")
    print("-- Managed MCP Server and re-run them yourself; this repo is not in the loop.")
    print("-- Substitute your workspace id for :workspace_id\n")
    for check in CHECKS:
        print(f"-- [{check.kind}] {check.name}")
        print(f"-- {check.question}")
        print(check.sql.replace("%(workspace_id)s", "':workspace_id'") + ";\n")
    print("-- [chain] ledger entries, for independent hash re-derivation")
    print(LEDGER_SQL.replace("%(workspace_id)s", "':workspace_id'") + ";\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent read-only audit of Palimpsest memory.")
    parser.add_argument("--workspace", type=str, default=None, help="workspace_id to audit")
    parser.add_argument("--print-sql", action="store_true", help="print the queries and exit")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args()

    if args.print_sql:
        print_sql()
        return 0

    dsn = os.environ.get("PALIMPSEST_DSN")
    if not dsn:
        print("PALIMPSEST_DSN is not set. See database/README.md.")
        return 1

    workspace_id = args.workspace or busiest_workspace(dsn)
    if not workspace_id:
        print("No workspaces found. Run `python -m demo.seed` first.")
        return 1

    report = run_audit(dsn, workspace_id)

    if args.json:
        print(json.dumps(
            {
                "workspace_id": report.workspace_id,
                "metrics": report.metrics,
                "ledger": {
                    "valid": report.ledger_valid,
                    "entries_checked": report.ledger_entries_checked,
                    "broken_at_seq": report.ledger_broken_at_seq,
                },
                "findings": [
                    {"check": f.check, "kind": f.kind, "count": f.count, "question": f.question}
                    for f in report.findings
                ],
                "passed": report.passed,
            },
            indent=2,
            default=str,
        ))
    else:
        print(render(report))

    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
