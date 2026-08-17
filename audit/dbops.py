"""
audit/dbops.py — read-only database operations readiness check.

    python -m audit.dbops
    python -m audit.dbops --json

The division of labour this project uses, and the reason each tool is here:

    MCP          data interaction     -- read-only queries against memory
    Agent Skills database expertise   -- the reusable audit knowledge
    ccloud       infrastructure ops   -- cluster lifecycle and backups
    CockroachDB  system of record     -- all of it, in one place

This module covers the third. It answers "is this cluster actually fit to
run Palimpsest right now", which is a different question from "is the
memory in it policy-compliant" (audit/auditor.py's job).

Two deliberate constraints:

  1. NOTHING HERE MUTATES ANYTHING. Every SQL check runs on the auditor's
     read-only connection, and the only ccloud subcommands invoked are
     `cluster list` and `backup list`. There is no path from this module to
     `ccloud cluster delete`, and there must never be one -- the public demo
     is unauthenticated, so a destructive infrastructure command reachable
     from it would be a genuine outage waiting to happen.

  2. A MISSING TOOL IS REPORTED, NEVER SIMULATED. If the ccloud binary is
     absent or unauthenticated, the checks that need it return SKIP with
     the reason. They do not fall back to plausible-looking output. An ops
     readiness report that invents a backup it never saw is worse than no
     report.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

from audit.auditor import connect_read_only

CCLOUD_TIMEOUT_S = 25

# Tables Palimpsest cannot run without. Checked by name rather than by
# querying each one, so a missing table is reported as a schema problem
# instead of surfacing later as a confusing query error.
REQUIRED_TABLES = (
    "workspaces",
    "agents",
    "memories",
    "contradictions",
    "approvals",
    "decisions",
    "decision_memory_refs",
    "memory_ledger",
    "rewinds",
)


@dataclass
class OpsCheck:
    name: str
    status: str  # "PASS" | "FAIL" | "SKIP"
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "PASS"


def _sql_checks(dsn: str) -> list[OpsCheck]:
    checks: list[OpsCheck] = []
    with connect_read_only(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version() AS v")
            version = str(cur.fetchone()["v"])
            checks.append(OpsCheck("cluster_version", "PASS", version.split(" (")[0]))

            # The single most common way to get a broken Palimpsest install:
            # the vector index silently does not exist because the cluster
            # setting defaulted off at CREATE TABLE time.
            try:
                cur.execute("SHOW CLUSTER SETTING feature.vector_index.enabled")
                enabled = bool(cur.fetchone()["feature.vector_index.enabled"])
                checks.append(
                    OpsCheck(
                        "vector_index_feature",
                        "PASS" if enabled else "FAIL",
                        "feature.vector_index.enabled = "
                        + ("true" if enabled else "false -- retrieval will not work"),
                    )
                )
            except Exception as e:  # noqa: BLE001
                checks.append(OpsCheck("vector_index_feature", "SKIP", f"not readable: {e}"))

            cur.execute(
                "SELECT DISTINCT index_name AS n FROM [SHOW INDEXES FROM memories] "
                "WHERE index_name LIKE '%vec%'"
            )
            vec_indexes = [r["n"] for r in cur.fetchall()]
            checks.append(
                OpsCheck(
                    "vector_index_present",
                    "PASS" if vec_indexes else "FAIL",
                    ", ".join(vec_indexes) if vec_indexes else "no vector index on memories",
                )
            )

            cur.execute("SELECT table_name AS t FROM [SHOW TABLES]")
            present = {r["t"] for r in cur.fetchall()}
            missing = [t for t in REQUIRED_TABLES if t not in present]
            checks.append(
                OpsCheck(
                    "schema_complete",
                    "PASS" if not missing else "FAIL",
                    f"{len(REQUIRED_TABLES)} required tables present"
                    if not missing
                    else f"missing: {', '.join(missing)}",
                )
            )

            # Proves AS OF SYSTEM TIME actually works on this cluster, which
            # is what rewind depends on. A cluster with too aggressive a GC
            # TTL will answer this and still fail on older timestamps, so
            # this is a smoke test, not a guarantee of retention depth.
            try:
                cur.execute("SELECT count(*) AS n FROM memories AS OF SYSTEM TIME '-10s'")
                n = cur.fetchone()["n"]
                checks.append(
                    OpsCheck("temporal_query", "PASS", f"AS OF SYSTEM TIME '-10s' returned {n} row(s)")
                )
            except Exception as e:  # noqa: BLE001
                checks.append(OpsCheck("temporal_query", "FAIL", f"rewind would not work: {e}"))

            cur.execute("SELECT count(*) AS n FROM workspaces")
            checks.append(OpsCheck("connectivity", "PASS", f"{cur.fetchone()['n']} workspace(s)"))

    return checks


def _run_ccloud(args: list[str]) -> tuple[bool, str]:
    """Run a read-only ccloud subcommand. Returns (ok, output-or-reason)."""
    if shutil.which("ccloud") is None:
        return False, "ccloud CLI not installed (https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-get-started)"
    try:
        proc = subprocess.run(
            ["ccloud", *args],
            capture_output=True,
            text=True,
            timeout=CCLOUD_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"ccloud {' '.join(args)} timed out after {CCLOUD_TIMEOUT_S}s"
    except OSError as e:
        return False, f"could not execute ccloud: {e}"
    if proc.returncode != 0:
        reason = (proc.stderr or proc.stdout).strip().splitlines()
        return False, reason[0] if reason else f"exit {proc.returncode}"
    return True, proc.stdout.strip()


def _ccloud_checks() -> list[OpsCheck]:
    checks: list[OpsCheck] = []

    ok, out = _run_ccloud(["cluster", "list"])
    checks.append(
        OpsCheck(
            "ccloud_cluster_list",
            "PASS" if ok else "SKIP",
            (out.splitlines()[0] if out else "no output") if ok else out,
        )
    )

    ok, out = _run_ccloud(["backup", "list"])
    if ok:
        lines = [l for l in out.splitlines() if l.strip()]
        # Header plus at least one row means a backup exists to restore from.
        has_backup = len(lines) > 1
        checks.append(
            OpsCheck(
                "ccloud_backup_available",
                "PASS" if has_backup else "FAIL",
                f"{len(lines) - 1} backup(s) listed" if has_backup else "no backups found",
            )
        )
    else:
        checks.append(OpsCheck("ccloud_backup_available", "SKIP", out))

    return checks


def run_dbops(dsn: str, *, include_ccloud: bool = True) -> list[OpsCheck]:
    checks = _sql_checks(dsn)
    if include_ccloud:
        checks.extend(_ccloud_checks())
    return checks


def render(checks: list[OpsCheck]) -> str:
    failures = [c for c in checks if c.status == "FAIL"]
    skipped = [c for c in checks if c.status == "SKIP"]

    lines = ["=" * 78, "PALIMPSEST DATABASE OPERATIONS - read-only readiness check", "=" * 78, ""]
    for c in checks:
        lines.append(f"  [{c.status:<4}] {c.name:<26} {c.detail}")
    lines.append("")
    if failures:
        lines.append(f"  VERDICT: NOT READY - {len(failures)} failing check(s)")
    elif skipped:
        lines.append(
            f"  VERDICT: READY - all database checks pass "
            f"({len(skipped)} check(s) skipped, tooling unavailable)"
        )
    else:
        lines.append("  VERDICT: READY - all checks pass")
    lines.append("=" * 78)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only CockroachDB operational readiness check.")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--no-ccloud", action="store_true", help="skip ccloud CLI checks")
    args = parser.parse_args()

    dsn = os.environ.get("PALIMPSEST_DSN")
    if not dsn:
        print("PALIMPSEST_DSN is not set. See database/README.md.")
        return 1

    checks = run_dbops(dsn, include_ccloud=not args.no_ccloud)

    if args.json:
        print(json.dumps([{"name": c.name, "status": c.status, "detail": c.detail} for c in checks], indent=2))
    else:
        print(render(checks))

    return 0 if not any(c.status == "FAIL" for c in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
