"""
tests/test_dbops.py — operational readiness checks.

The property worth protecting here is honesty: when a tool is missing, the
report must say SKIP and why. A readiness report that invents a backup it
never saw is more dangerous than no report, because someone will rely on it.
"""

from __future__ import annotations

import audit.dbops as dbops
from audit.dbops import REQUIRED_TABLES, OpsCheck, run_dbops


def _by_name(checks: list[OpsCheck]) -> dict[str, OpsCheck]:
    return {c.name: c for c in checks}


def test_sql_checks_pass_against_a_real_cluster(dsn: str):
    checks = _by_name(run_dbops(dsn, include_ccloud=False))

    assert checks["connectivity"].ok
    assert checks["schema_complete"].ok, checks["schema_complete"].detail
    assert checks["vector_index_present"].ok, "the vector index backing retrieve() is missing"
    assert checks["temporal_query"].ok, "AS OF SYSTEM TIME is required for rewind"
    assert "CockroachDB" in checks["cluster_version"].detail


def test_vector_index_feature_is_enabled(dsn: str):
    """
    The single most common broken install: feature.vector_index.enabled
    defaults off, so CREATE TABLE silently produces no vector index and
    retrieval quietly returns nothing.
    """
    checks = _by_name(run_dbops(dsn, include_ccloud=False))
    assert checks["vector_index_feature"].status in ("PASS", "SKIP")
    if checks["vector_index_feature"].status == "PASS":
        assert "true" in checks["vector_index_feature"].detail


def test_dbops_cannot_mutate(dsn: str, workspace_id: str):
    """
    dbops runs on the auditor's read-only connection. Proven by observing
    that the workspace it can see, it cannot delete.
    """
    import psycopg
    import pytest

    from audit.auditor import connect_read_only

    with connect_read_only(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM workspaces WHERE workspace_id = %s", (workspace_id,))
            assert cur.fetchone()["n"] == 1

        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            with conn.cursor() as cur:
                cur.execute("DELETE FROM workspaces WHERE workspace_id = %s", (workspace_id,))


def test_missing_ccloud_is_reported_not_simulated(monkeypatch):
    """A tool that isn't there must produce SKIP with a reason, never invented output."""
    monkeypatch.setattr(dbops.shutil, "which", lambda _: None)

    checks = _by_name(dbops._ccloud_checks())

    for name in ("ccloud_cluster_list", "ccloud_backup_available"):
        assert checks[name].status == "SKIP"
        assert "not installed" in checks[name].detail


def test_ccloud_failure_is_reported_not_swallowed(monkeypatch):
    """An authenticated-but-failing ccloud must surface its reason, not pass."""
    monkeypatch.setattr(dbops.shutil, "which", lambda _: "/usr/bin/ccloud")

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "ERROR: not logged in"

    monkeypatch.setattr(dbops.subprocess, "run", lambda *a, **k: _Proc())

    checks = _by_name(dbops._ccloud_checks())
    assert checks["ccloud_cluster_list"].status == "SKIP"
    assert "not logged in" in checks["ccloud_cluster_list"].detail


def test_ccloud_reports_absence_of_backups_as_failure(monkeypatch):
    """
    A cluster with zero backups is a real operational finding, not a skip --
    the distinction between "couldn't check" and "checked, it's bad" is the
    whole value of the report.
    """
    monkeypatch.setattr(dbops.shutil, "which", lambda _: "/usr/bin/ccloud")

    class _Proc:
        returncode = 0
        stdout = "NAME  CREATED\n"  # header only, no rows
        stderr = ""

    monkeypatch.setattr(dbops.subprocess, "run", lambda *a, **k: _Proc())

    checks = _by_name(dbops._ccloud_checks())
    assert checks["ccloud_backup_available"].status == "FAIL"
    assert "no backups" in checks["ccloud_backup_available"].detail


def test_required_tables_match_the_shipped_schema(dsn: str):
    """REQUIRED_TABLES must not drift from database/schema.sql."""
    from audit.auditor import connect_read_only

    with connect_read_only(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name AS t FROM [SHOW TABLES]")
            present = {r["t"] for r in cur.fetchall()}

    assert set(REQUIRED_TABLES) <= present
