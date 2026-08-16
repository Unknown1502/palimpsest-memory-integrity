# Database setup

Palimpsest runs against CockroachDB. Any wire-compatible CockroachDB works
for local development (see "Local development" below); the hackathon
submission targets CockroachDB Cloud.

## 1. Create a free CockroachDB Cloud cluster

1. Go to https://cockroachlabs.cloud/ and sign up (no credit card required
   for the free Serverless tier).
2. Create a cluster: **Serverless**, any region close to you.
3. In the console, open **SQL Users** and create a user, or use the one
   generated for you at cluster creation.

## 2. Get the connection string

1. In the Cloud Console, click **Connect** on your cluster.
2. Select **General connection string** (psycopg-compatible).
3. Copy it — it looks like:
   ```
   postgresql://<user>:<password>@<host>:26257/<database>?sslmode=verify-full
   ```
4. Put it in `.env` (copy `.env.example` first) as `PALIMPSEST_DSN`.

## 3. Enable vector indexes (required, one-time per cluster)

Vector indexes are gated behind a cluster setting that is **off by
default** — confirmed empirically against a live v25.2.22 cluster while
building this repo, not just from docs. Without this, `database/migrate.py`
fails with `FeatureNotSupported: vector indexes are not enabled`:

```sql
SET CLUSTER SETTING feature.vector_index.enabled = true;
```

Run this once per cluster, before the first `database/migrate.py` run.

## 4. Configure GC TTL for AS OF SYSTEM TIME rewind

Palimpsest's rewind feature reads historical MVCC state via
`AS OF SYSTEM TIME`, which can only reach as far back as the table's
garbage-collection TTL. The cluster default is short. Run once, after the
schema is applied:

```sql
ALTER TABLE memories CONFIGURE ZONE USING gc.ttlseconds = 172800;
```

**If this is rejected** — some CockroachDB Cloud tiers (in particular the
free Serverless tier) restrict `CONFIGURE ZONE`. If you see a permission or
"not supported on this cluster tier" error, that's expected on those
tiers, not a bug in this repo. Palimpsest still works: `MemoryGate` and the
API layer fall back to `memory/ledger_replay.py`, which reconstructs
belief state from `memory_ledger` (the hash-chained audit trail) instead of
MVCC history. The fallback is always built and always available — it does
not need to be discovered under time pressure later. The tradeoff: ledger
replay only knows what the gate chose to log; `AS OF SYSTEM TIME` knows
everything the table ever held. Prefer the zone-config path when your
cluster tier allows it.

Confirmed empirically against this project's own CockroachDB Cloud cluster
(v26.2.5, `ap-south-1`): `CONFIGURE ZONE` was **not** rejected — the
primary `AS OF SYSTEM TIME` path (not the ledger-replay fallback) is what
actually serves rewind on the deployed submission. Full test suite (19/19)
and `demo/attack_scenario.py`'s all 4 phases, including rewind, verified
end-to-end against this real cluster, not just local Docker.

## 5. Apply the schema

```bash
pip install -r requirements.txt
export PALIMPSEST_DSN="postgresql://..."   # from step 2
python database/migrate.py
```

`database/migrate.py` executes `database/schema.sql` directly — every
statement in it is `CREATE ... IF NOT EXISTS`, so running this command
again later (after a schema change) is always safe.

## 6. Backups (`ccloud` CLI)

Backup automation is deliberately scripted and documented
here, not wired into any code path — it doesn't need to run inside the
demo flow. CockroachDB Cloud clusters take automatic backups, but for an
explicit, on-demand backup via the `ccloud` CLI (install:
`brew install cockroachdb/tap/ccloud` or see
cockroachlabs.com/docs/cockroachcloud/ccloud-get-started):

```bash
ccloud auth login
ccloud cluster list                       # find your cluster's ID
ccloud backup create <cluster-id> --wait  # on-demand backup, JSON output
ccloud backup list <cluster-id>           # confirm it landed
```

Restoring from a `ccloud`-managed backup is a Cloud Console operation
(Cluster → Backup & Restore) or `ccloud backup restore <cluster-id>
<backup-id>` — see `ccloud backup restore --help` for the exact flags for
your `ccloud` version, since restore options (full cluster vs. specific
databases/tables) are version-sensitive.

## Local development

For iterating without touching the Cloud cluster, run a single-node
CockroachDB locally via Docker. Pin the image tag to `latest-v25.2` (or
newer) explicitly — vector indexes require v25.2+, and `:latest` is not
guaranteed to resolve to a version that has them:

```bash
docker run -d --name palimpsest-crdb -p 26257:26257 -p 8080:8080 \
  cockroachdb/cockroach:latest-v25.2 start-single-node --insecure

docker exec palimpsest-crdb ./cockroach sql --insecure \
  -e "CREATE DATABASE IF NOT EXISTS palimpsest; SET CLUSTER SETTING feature.vector_index.enabled = true;"

export PALIMPSEST_DSN="postgresql://root@localhost:26257/palimpsest?sslmode=disable"
python database/migrate.py
```

This exact sequence was run against a live local cluster (v25.2.22) while
building this repo — `database/migrate.py` applies cleanly and is
idempotent (verified by running it twice).

The DB Console is at http://localhost:8080. `CONFIGURE ZONE` (step 4) works
without restriction on a local insecure single-node cluster, so this is
also the easiest way to exercise the real `AS OF SYSTEM TIME` rewind path
end-to-end before pointing at CockroachDB Cloud.
