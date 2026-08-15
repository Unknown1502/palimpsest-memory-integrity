"""
database/migrate.py — idempotent schema apply.

    python database/migrate.py

Connects via PALIMPSEST_DSN, executes database/schema.sql (every statement
in it is CREATE ... IF NOT EXISTS, so re-running this is always safe), then
prints a summary of tables and row counts so you can eyeball that the apply
actually landed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Order matches schema.sql's dependency order (FK references), so a fresh
# apply followed immediately by this summary always succeeds.
TABLES = [
    "workspaces",
    "agents",
    "memories",
    "contradictions",
    "approvals",
    "decisions",
    "decision_memory_refs",
    "memory_ledger",
    "rewinds",
]


def main() -> int:
    dsn = os.environ.get("PALIMPSEST_DSN")
    if not dsn:
        print("PALIMPSEST_DSN is not set. See database/README.md.", file=sys.stderr)
        return 1

    sql = SCHEMA_PATH.read_text(encoding="utf-8")

    print(f"Connecting to {_redact(dsn)} ...")
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            print(f"Applying {SCHEMA_PATH} ...")
            cur.execute(sql)
            print("Schema applied.\n")

            print(f"{'table':<24}{'row_count':>10}")
            print("-" * 34)
            for table in TABLES:
                cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - table names are our own constant list
                (count,) = cur.fetchone()
                print(f"{table:<24}{count:>10}")

    return 0


def _redact(dsn: str) -> str:
    """Never print a password to a terminal, even a local dev one."""
    if "@" not in dsn:
        return dsn
    scheme_and_creds, rest = dsn.split("@", 1)
    if "://" not in scheme_and_creds:
        return dsn
    scheme, creds = scheme_and_creds.split("://", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{rest}"


if __name__ == "__main__":
    raise SystemExit(main())
