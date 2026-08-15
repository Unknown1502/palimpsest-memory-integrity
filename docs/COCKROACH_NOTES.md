# CockroachDB Technical Notes — verified, not assumed

Everything below was confirmed against official CockroachDB documentation
and engineering blog posts during research for this project (Aug 2026).
Use this as ground truth in place of training-data recall, which may be
stale on a fast-moving feature like distributed vector indexing.

## Vector indexing (C-SPANN)

- **Confirmed empirically against a live v25.2.22 cluster (this project,
  Aug 2026), not just from docs**: vector indexes are gated behind a
  cluster setting that is OFF by default. Applying `database/schema.sql`
  against a fresh cluster fails with
  `FeatureNotSupported: vector indexes are not enabled; enable with the
  feature.vector_index.enabled cluster setting` until you run:
  ```sql
  SET CLUSTER SETTING feature.vector_index.enabled = true;
  ```
  This is a one-time, cluster-wide setting — run it before
  `database/migrate.py` on any new cluster (local or Cloud). See
  `database/README.md` step 0.
- CockroachDB has a native `VECTOR` type and a `CREATE VECTOR INDEX`
  statement. Similarity operators: `<->` (L2), `<#>` (inner product), `<=>`
  (cosine). Compatible with the pgvector extension surface.
- The indexing algorithm is called **C-SPANN** (Cockroach Space Partition
  Approximate Nearest Neighbor), introduced in v25.2, built specifically to
  avoid a central coordinator, avoid large in-memory caches, support
  real-time incremental updates, and avoid hot spots under CockroachDB's
  distributed architecture — it treats the index as ordinary table data
  rather than a bolted-on separate system.
- **Prefix columns**: `VECTOR INDEX (col_a, col_b, embedding)` partitions
  the index — CockroachDB maintains a *separate k-means tree per distinct
  value of the prefix columns*. This is the mechanism Palimpsest uses for
  both tenant isolation (`workspace_id`) and quarantine
  (`status`) — quarantining a memory doesn't hide a row, it moves the
  vector into a different tree entirely.
- **Filter acceleration is prefix-only.** A `WHERE` clause on a non-prefix
  column will not be accelerated by the vector index. Design your prefix
  columns around what you actually need to filter cheaply at query time.
- **Known limitations (as of the version researched):**
  - Index acceleration with filters only works on prefix columns.
  - No index recommendations are provided for vector indexes.
  - Vector index queries may return incorrect results if the underlying
    table uses multiple column families — keep vector-indexed tables to a
    single column family.
  - `CREATE VECTOR INDEX` against a populated table disables mutations
    (INSERT/UPDATE/DELETE touching the vector column) until backfill
    completes. Declare vector indexes inline at `CREATE TABLE` time to
    avoid this entirely.
- Vector inserts perform best with smaller batch sizes; large batch inserts
  of VECTOR types can cause performance degradation (per LangChain's
  integration docs, batch_size=100 default).

## Transactions and consistency

- CockroachDB provides serializable isolation by default for every
  transaction.
- Vector indexes participate in the **same transaction and index
  maintenance model as any other secondary index** — meaning a vector
  index read is guaranteed consistent with the row data in the same
  transaction, with no separate-store consistency gap.
- Expect `40001` (`SerializationFailure`) under concurrent writes to
  overlapping data. This is expected, routine behavior, not a bug —
  applications are expected to retry with backoff.

## Temporal queries

- `AS OF SYSTEM TIME <timestamp-or-hlc>` reads historical MVCC state.
  How far back you can read is bounded by the garbage collection TTL
  (`gc.ttlseconds`, configurable via `ALTER TABLE ... CONFIGURE ZONE
  USING gc.ttlseconds = <seconds>`), which may not be configurable on all
  cluster tiers.
- `cluster_logical_timestamp()` returns the current transaction's HLC
  timestamp — this is what should be stored alongside any record you may
  later want to time-travel back to, rather than `now()`.

## Multi-region

- `ALTER DATABASE ... SET PRIMARY REGION` / `ADD REGION`, plus
  `ALTER TABLE ... SET LOCALITY REGIONAL BY ROW`, automatically adds a
  `crdb_region` column that can also participate as a vector index prefix
  column — meaning per-region data locality and per-region vector search
  isolation come from the same mechanism.

## Agent tooling surfaced by this hackathon specifically

- **CockroachDB Cloud Managed MCP Server** — endpoint
  `https://cockroachlabs.cloud/mcp`. Config copied from Cloud Console,
  works natively with Claude Code, Cursor, VS Code. **Read-only mode by
  default**, full audit logging, no custom proxy required. Read/write
  access is also available for self-hosted deployments via a separately
  documented CockroachDB MCP Server.
- **`ccloud` CLI** — noun-verb command structure, JSON output on every
  command, service-account-based RBAC. Designed to be automatable by
  agents specifically (not just humans).
- **Agent Skills Repo** (`cockroachlabs/cockroachdb-skills`, Apache-2.0,
  installable via `npx skills add cockroachlabs/cockroachdb-skills`) —
  organized into domains: onboarding-and-migrations,
  application-development, performance-and-scaling,
  operations-and-lifecycle, resilience-and-disaster-recovery,
  observability-and-diagnostics, security-and-governance,
  integrations-and-ecosystem, cost-and-usage-management. Each skill is a
  directory with a `SKILL.md` following the Agent Skills Specification
  (agentskills.io). Skills encode operational reasoning, not raw docs —
  contributions are validated via `scripts/validate-spec.py` and CI.
- **LangChain integration** (`langchain-cockroachdb`) — provides
  `CSPANNIndex` with configurable `distance_strategy` (COSINE, EUCLIDEAN,
  INNER_PRODUCT) and `min_partition_size`/`max_partition_size` tuning.
  Not required for this build (we talk to CockroachDB directly via
  psycopg for full control over the transaction boundaries the gate
  depends on), but documented here in case a future integration wants it.

## Sources consulted

- cockroachlabs.com/docs/v26.2/cockroachdb-and-ai
- cockroachlabs.com/blog/distributed-vector-indexing-cockroachdb
- cockroachlabs.com/blog/cspann-real-time-indexing-billions-vectors
- cockroachlabs.com/blog/recommendation-engines-cockroachdb
- cockroachlabs.com/docs/v25.2/vector-indexes.html
- github.com/cockroachdb/cockroach issues #144443, #143206, #146145,
  #146146, #146046
- docs.langchain.com/oss/python/integrations/vectorstores/cockroachdb
- cockroachlabs.com/blog/cockroachdb-ai-agents-agent-ready-database
- cockroachlabs.com/blog/cockroachdb-ai-agents-database-lifecycle-automation
- github.com/cockroachlabs/cockroachdb-skills
- cockroachdb-ai.devpost.com (rules, resources, FAQ)

If you're building against a CockroachDB version newer than v26.2, re-verify
anything version-sensitive above (especially vector index limitations —
several are tagged as actively being worked on and may be resolved).
