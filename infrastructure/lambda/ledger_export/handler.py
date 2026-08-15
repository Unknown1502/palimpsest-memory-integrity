"""
infrastructure/lambda/ledger_export/handler.py — exports unexported
memory_ledger rows to S3 with Object Lock, then marks them exported.

Triggered on an EventBridge schedule (every 5 minutes — see
infrastructure/stacks/palimpsest_stack.py, Prompt 7). Idempotent: safe to
run on overlapping invocations. Each row's exported_at is claimed exactly
once via `WHERE exported_at IS NULL` in the same UPDATE that follows a
successful S3 write, so a row is never marked exported without its export
having actually landed, and two overlapping invocations racing on the same
row just mean one of them updates 0 rows for that row — never a duplicate
"real" export outcome the ledger's own hash chain would disagree with.

Scans exactly the `ledger_unexported` partial index declared in
database/schema.sql (`WHERE exported_at IS NULL`).

The S3 bucket this writes to MUST have Object Lock enabled — and Object
Lock can only be enabled at bucket CREATION time, not added after the
fact. See infrastructure/stacks/palimpsest_stack.py for the bucket
definition (`object_lock_enabled=True`, governance mode, default
retention) — this handler does not create the bucket, only writes to it.

Environment variables:
  PALIMPSEST_DSN                     CockroachDB connection string (from
                                      Secrets Manager in the CDK stack)
  PALIMPSEST_LEDGER_BUCKET           S3 bucket name (Object Lock enabled)
  PALIMPSEST_EXPORT_RETENTION_DAYS   Object Lock retention, default 2555 (~7y)
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import boto3
import psycopg

BATCH_SIZE = 500


def handler(event: dict, context: Any) -> dict:
    dsn = os.environ["PALIMPSEST_DSN"]
    bucket = os.environ["PALIMPSEST_LEDGER_BUCKET"]
    retention_days = int(os.environ.get("PALIMPSEST_EXPORT_RETENTION_DAYS", "2555"))

    s3 = boto3.client("s3")
    exported_total = 0

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seq, workspace_id, event_type, payload, prev_hash, entry_hash, created_at "
                "FROM memory_ledger WHERE exported_at IS NULL ORDER BY workspace_id, seq LIMIT %s",
                (BATCH_SIZE,),
            )
            rows = cur.fetchall()

            if not rows:
                return {"exported": 0}

            by_workspace: dict[str, list[dict]] = {}
            for seq, workspace_id, event_type, payload, prev_hash, entry_hash, created_at in rows:
                by_workspace.setdefault(str(workspace_id), []).append(
                    {
                        "seq": seq,
                        "workspace_id": str(workspace_id),
                        "event_type": event_type,
                        "payload": payload,
                        "prev_hash": prev_hash,
                        "entry_hash": entry_hash,
                        "created_at": created_at.isoformat(),
                    }
                )

            retain_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=retention_days)

            for workspace_id, entries in by_workspace.items():
                seqs = [e["seq"] for e in entries]
                key = f"ledger/{workspace_id}/{seqs[0]:012d}-{seqs[-1]:012d}.ndjson"
                body = "\n".join(json.dumps(e, default=str) for e in entries) + "\n"

                s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=body.encode("utf-8"),
                    ContentType="application/x-ndjson",
                    ObjectLockMode="GOVERNANCE",
                    ObjectLockRetainUntilDate=retain_until,
                )

                cur.execute(
                    "UPDATE memory_ledger SET exported_at = now() "
                    "WHERE workspace_id = %s AND seq = ANY(%s) AND exported_at IS NULL",
                    (workspace_id, seqs),
                )
                exported_total += len(entries)

    return {"exported": exported_total}
