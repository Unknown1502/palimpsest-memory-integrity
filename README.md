# Palimpsest

![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)

A memory integrity layer for AI agents.

## Why this exists

Agents write beliefs to long-term memory constantly and read them back
with total trust — which makes memory the softest attack surface in the
stack. Poison it once (plant an instruction in a ticket comment, a
document, any tool output an agent ingests) and the bad instruction is
permanent: every future retrieval launders it back in as trusted context,
with no login required and no alert raised. This is indirect prompt
injection with a persistence layer.

Palimpsest gates every belief through an **integrity lattice** (a
belief's source authority caps what kind of decision it may influence),
adjudicates contradictions **atomically** inside a single CockroachDB
`SERIALIZABLE` transaction, and can **rewind** an agent's memory to any
past decision via `AS OF SYSTEM TIME` — finding every decision a
poisoned belief touched and replaying them against corrected memory.
Every piece of this — the lattice, the atomic adjudication, the retrieval
filter, rewind end-to-end — was built and run against a real CockroachDB
cluster with no database mocking anywhere in the test suite. The
Bedrock-dependent paths (Titan embeddings, Claude triage/adjudication)
were verified live too; embeddings are unaffected, and Claude calls are
currently blocked on the development AWS account by an unrelated
Marketplace billing issue (`INVALID_PAYMENT_INSTRUMENT`) that surfaced
mid-build — an account-level problem, not a code one. Everywhere that
blocked a live Claude call, verification continued with a plain
Python function standing in for `chat()` (see the git history for Prompts
3–5 and 8), never a change to the actual `agent/`, `api/`, or `memory/`
code paths themselves.

Built for the [CockroachDB × AWS Hackathon](https://cockroachdb-ai.devpost.com/) — Build with Agentic Memory.

## Architecture

```
[1] UNTRUSTED CONTENT SOURCES
    ticket comments · tool output · operator statements
        |
        v
[2] agent/ingest.py -- capability requested by the CALLER, never by extraction
        |  Claim + Provenance
        v
====================================================================
[3] memory/gate.py -- MemoryGate
    THE ONLY WRITE PATH INTO `memories` / `memory_ledger`
      admit()      lattice check BEFORE any DB connection opens;
                    contradiction detection + adjudication + ledger
                    write, one SERIALIZABLE transaction, real 40001 retry
      retrieve()    filters on capability_ceiling AND integrity_level
      revoke() / blast_radius() / belief_state_at()  -- rewind primitives
====================================================================
        |
        v
[4] CockroachDB -- memories (VECTOR INDEX prefixed workspace_id, status),
    contradictions, approvals, decisions, memory_ledger (hash-chained), rewinds
        |
        +--------------------+--------------------+
        v                    v                    v
[5] agent/triage.py   [6] api/main.py       [7] memory/ledger_replay.py
    TriageAgent             FastAPI service       AS OF SYSTEM TIME fallback
        |                    |
        v                    v
[8] agent/llm.py             [9] console/ (Next.js)
    provider switch:              /timeline /memories /rewind + SQL pane
    bedrock (default) or
    anthropic_api -- Titan
    embeddings always stay
    on Bedrock either way

[10] infrastructure/ (AWS CDK) -- GateHandler + LedgerExportHandler Lambdas,
     S3 with Object Lock, Secrets Manager
```

Full prose version, component by component: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Formal threat model: [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## Which CockroachDB tools, and how

- **Distributed Vector Indexing (C-SPANN)** — `memories.embedding` has a
  `VECTOR INDEX` declared inline with prefix columns `(workspace_id,
  status)`. This isn't just a query-speed optimization: CockroachDB
  maintains a *separate k-means tree per distinct prefix value*, which is
  the actual mechanism behind both tenant isolation and quarantine
  isolation — quarantining a memory moves its vector into a different
  tree, it doesn't just hide a row from a `WHERE` clause. `retrieve()`
  additionally filters on `capability_ceiling`, independently of the
  index prefix — see the regression test that exists specifically because
  filtering on integrity alone wasn't sufficient
  (`tests/test_integrity_lattice.py::test_retrieve_excludes_informational_memory_from_suppressive_request`).
- **CockroachDB Cloud Managed MCP Server** — gives any MCP-compatible
  client (Claude Code, Cursor, VS Code) direct, read-only, audited access
  to run the same labeled SQL queries the console's SQL pane runs — the
  quarantine check, the ledger hash-chain verification — directly against
  the live Cloud cluster, independent of trusting the API layer. This
  repo was built and verified against a local CockroachDB instance
  (`database/README.md`'s "Local development" section) via direct
  `psycopg`/`ccloud sql` access, not the MCP Server itself; once pointed
  at a Cloud cluster, a judge can connect the MCP Server and independently
  re-run the exact same verification queries this repo's own tests run —
  e.g. re-deriving `memory_ledger`'s `entry_hash` chain by hand — without
  needing to trust the API layer's `GET /ledger/verify` response at all.
- **`ccloud` CLI** — cluster lifecycle and on-demand backups (see
  [`database/README.md`](database/README.md) step 6 for the exact
  commands: `ccloud backup create`, `ccloud backup list`).
- **Agent Skills Repo** — `skills/audit-agent-memory-integrity/SKILL.md`
  is an upstream contribution prepared for
  `cockroachlabs/cockroachdb-skills` (security-and-governance domain): a
  read-only skill that audits any CockroachDB-backed agent memory table
  for the same four integrity gaps this project's own schema closes.
  Exact PR steps: [`docs/SKILLS_PR.md`](docs/SKILLS_PR.md).

[`docs/COCKROACH_NOTES.md`](docs/COCKROACH_NOTES.md) collects the
CockroachDB behavior this repo depends on, sourced against official docs
during research (not assumed from training data, which can be stale on a
fast-moving feature like distributed vector indexing). Three specific
facts in it go further — found the hard way, against a live cluster,
while building this repo, each with the exact error text that surfaced
it: `feature.vector_index.enabled` defaulting off, `AS OF SYSTEM TIME`
requiring a top-level statement (not valid nested in a subquery beside a
live-read sibling), and `belief_state_at()` needing a dedicated
`autocommit=True` connection for that same reason.

## Which AWS services, and how

- **Bedrock** — Titan Text Embeddings V2 (1024 dims, L2-normalized before
  insert so `<->` is rank-equivalent to cosine) for `retrieve()`'s vector
  search, and Claude (via the Messages API, invoked through a
  cross-region inference profile — Claude Sonnet 4.5 rejects on-demand
  invocation by bare model ID, confirmed empirically) for triage
  decisions and equal-integrity contradiction adjudication.
- **Lambda** — `GateHandler` runs the exact same FastAPI app as
  `api/main.py`, via Mangum, behind a Function URL. `LedgerExportHandler`
  exports the hash-chained ledger to S3 on a schedule.
- **S3 with Object Lock** — the ledger export target, governance mode,
  ~7 year default retention, enabled at bucket creation (the only time
  it can be).
- **Secrets Manager** — `PALIMPSEST_DSN`, injected into both Lambdas via
  a CloudFormation dynamic reference, never a plaintext CDK value.

Full stack details and what's deliberately *not* deployed (Step
Functions, API Gateway, multi-region — see the cut list in
[`CONTEXT.md`](CONTEXT.md)): [`infrastructure/README.md`](infrastructure/README.md).

## Setup

1. [`database/README.md`](database/README.md) — create a CockroachDB
   cluster (Cloud or local via Docker), enable vector indexes, apply the
   schema.
2. `pip install -r requirements.txt`, then `python -m agent.bedrock_client`
   to confirm Bedrock connectivity.
3. `uvicorn api.main:app --reload` for the API.
4. `cd console && npm install && npm run dev` for the console.
5. [`infrastructure/README.md`](infrastructure/README.md) if you want the
   AWS-deployed version (`cdk synth` / `cdk deploy`).
6. `python -m demo.seed` then `python -m demo.attack_scenario` for the
   full 4-phase demo — see [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

## Proof, not just claims

<details>
<summary><code>tests/test_concurrent_admission.py</code> — real 40001 SerializationFailure, real retry, real recovery (verbatim output against a live local CockroachDB v25.2 cluster)</summary>

```
tests/test_concurrent_admission.py::test_concurrent_opposing_admits_resolve_to_one_active_belief
[concurrent admission] final active memories for (ip:203.0.113.9, classification): [(UUID('aceba90b-106b-4d20-ad77-859beab24865'), 'known_malicious_scanner')]
[concurrent admission] retry loop exercised: 1 retry log line(s)
  40001 SerializationFailure on attempt 1/5, retrying in 0.122s: restart transaction: searching for partition to update: searching level 0: TransactionRetryWithProtoRefreshError: ReadWithinUncertaintyIntervalError: read at time 1786795457.264390508,0 encountered previous write with future timestamp 1786795457.264390508,1 (local=1786795457.252981653,0) within uncertainty interval `t <= (local=1786795457.264390508,0, global=1786795457.764390508,0)`; observed timestamps: [{1 1786795457.264390508,0}]
  HINT: See: https://www.cockroachlabs.com/docs/v25.2/transaction-retry-error-reference.html#readwithinuncertaintyinterval
[concurrent admission] thread A result: quarantined, thread B result: active
PASSED

tests/test_concurrent_admission.py::test_retry_loop_fires_under_forced_write_write_contention
[retry loop] 3 threads raced SELECT-then-UPDATE on one row.
[retry loop] 40001 SerializationFailure observed and retried 3 time(s):
  40001 SerializationFailure on attempt 1/5, retrying in 0.123s: restart transaction: TransactionRetryWithProtoRefreshError: WriteTooOldError: write for key /Table/118/1/... at timestamp 1786795457.660251937,1 too old; must write at or above 1786795457.660251937,3
  HINT: See: https://www.cockroachlabs.com/docs/v25.2/transaction-retry-error-reference.html
  40001 SerializationFailure on attempt 1/5, retrying in 0.137s: restart transaction: TransactionRetryWithProtoRefreshError: WriteTooOldError: write for key /Table/118/1/... at timestamp 1786795457.660251937,0 too old; must write at or above 1786795457.660251937,3
  HINT: See: https://www.cockroachlabs.com/docs/v25.2/transaction-retry-error-reference.html
  40001 SerializationFailure on attempt 2/5, retrying in 0.234s: restart transaction: TransactionRetryWithProtoRefreshError: WriteTooOldError: write for key /Table/118/1/... at timestamp 1786795457.992824170,0 too old; must write at or above 1786795457.992824170,2
  HINT: See: https://www.cockroachlabs.com/docs/v25.2/transaction-retry-error-reference.html
PASSED

============================== 2 passed in 1.54s ==============================
```

The first test found a genuine `ReadWithinUncertaintyIntervalError` on
its own (not the write-write contention the test was designed to force),
and the retry loop absorbed it transparently — real evidence the retry
path handles more than one 40001 subtype, not just the one it was
written against. The second test deliberately forces contention (3
threads racing a read-then-write on one row) and confirms the retry loop
fires and every thread still succeeds.

</details>

The full test suite — 17 tests, zero mocked database access anywhere,
Bedrock mocked only in the two tests that specifically need a
deterministic tie-break — is under [`tests/`](tests/).

## Demo

- Live demo URL: _TODO — add before submission_
- Video (<3 min, YouTube/Vimeo, public): _TODO — add before submission_

## Roadmap

Per [`CONTEXT.md`](CONTEXT.md)'s cut list — deliberately deferred past
this submission, not forgotten:

1. **Multi-region** (`REGIONAL BY ROW`) — `crdb_region` can participate as
   a vector index prefix column, giving per-region data locality and
   per-region vector search isolation from the same mechanism tenant
   isolation already uses. Documented, not built.
2. **KMS-signed ledger entries** — the SHA-256 hash chain
   (`GET /ledger/verify`) is real tamper-evidence today; a KMS signature
   per entry would add non-repudiation on top of it.
3. **Step Functions orchestration for rewind** — `POST /rewind/apply`
   calls the replay logic directly and synchronously today, which is
   fine at this scale; a real production deployment doing this across a
   large blast radius would want to make that async and resumable.

## License

Apache-2.0 — see [`LICENSE`](LICENSE).
