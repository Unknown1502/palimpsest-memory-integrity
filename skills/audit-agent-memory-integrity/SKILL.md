---
name: audit-agent-memory-integrity
description: Audit an existing CockroachDB-backed AI agent memory/belief table for missing provenance tracking, tenant-isolation gaps in vector indexes, absent contradiction handling, and missing temporal audit trails. Use when reviewing, hardening, or doing a security pass on any table that stores AI agent beliefs, memories, or retrieved facts that later influence agent decisions.
license: Apache-2.0
---

# Audit Agent Memory Integrity

## Why this exists

Agents write beliefs to long-term memory constantly and read them back
with total trust. That makes memory the softest attack surface in an
agentic stack: poison it once (a planted instruction in a ticket comment,
a document, a tool response an agent ingests) and the bad instruction is
permanent — every future retrieval launders it back in as trusted
context. Most memory tables built for agent systems were designed for
recall, not integrity. This skill finds the specific, checkable gaps that
matter and reports them with severity, not a generic "add more security"
gesture.

This skill was built alongside [Palimpsest](https://github.com/), a
CockroachDB-backed memory integrity layer, and encodes the checks that
project's own schema (`database/schema.sql`) exists to satisfy. See that
schema for a worked example of every check below passing.

## When to use this

- Reviewing a table that stores AI agent memories, retrieved facts, RAG
  chunks with provenance, or any belief an agent's decisions can cite.
- Before connecting a new ingestion path (tool output, document upload,
  ticket/ticket-comment sync, web scrape) to an existing memory table.
- As a periodic security/production-readiness pass on a memory store
  that's grown organically without an explicit integrity design.

## Inputs

- **Connection details**: a CockroachDB connection string or an already-open
  connection, with read-only access at minimum (a role with `SELECT` on
  the target table and `information_schema`/`crdb_internal` is
  sufficient — no write privilege is ever needed).
- **Table name**: the memory/belief table to audit (schema-qualified if
  not in `public`, e.g. `public.memories`).

## Safety guardrails — read this before running anything

1. **Read-only, always.** Every query in this skill is a `SELECT`,
   `SHOW`, or a read against `information_schema` /
   `crdb_internal`. Never `INSERT`, `UPDATE`, `DELETE`, `ALTER`, or
   `CREATE` against the audited database. If a check seems to require a
   write to verify (it never does, in this skill), stop and flag it
   instead of proceeding.
2. **`EXPLAIN` before any query that touches table data**, not just
   catalog metadata. Every data-touching query below is a `SELECT
   count(*) ... LIMIT` or narrower — cheap by construction — but confirm
   with `EXPLAIN` first anyway before running it against a table you
   don't control the size of. If `EXPLAIN` shows a full table scan on a
   table with an unknown or very large row count, add an explicit `LIMIT`
   or skip that check and report it as "not evaluated (table too large to
   safely scan)" rather than running it blind.
3. **No credentials in output.** Redact the connection string / password
   in any findings report you produce (see `database/migrate.py`'s
   `_redact()` in the Palimpsest repo for the exact pattern: keep scheme
   and username, mask the password, keep host/db).
4. **Never guess at schema you can't see.** If `information_schema`
   access is restricted, report that specific check as "not evaluated
   (insufficient privilege)" rather than inferring an answer.

## Checks

Run each check, record the finding, and assign severity per the rubric
below. A check that can't run (privilege, table doesn't exist, etc.) is
reported as `not_evaluated`, not silently skipped.

### 1. Missing provenance tracking (severity: critical if absent)

A memory table with no way to know WHERE a belief came from cannot
distinguish a human-confirmed fact from an untrusted ingestion — every
downstream consumer is forced to trust every row equally.

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = '<schema>' AND table_name = '<table>'
ORDER BY ordinal_position;
```

Look for a column that captures source authority — matching names like
`source_kind`, `provenance`, `origin`, `source_type`, `created_by`,
`trust_level`, `integrity_level`, or a JSONB column literally named
`provenance`/`metadata` that (spot-check a few rows) actually contains a
source field.

- **Finding if absent**: "No provenance column found. Every row is
  indistinguishable by source authority — an agent retrieving from this
  table cannot tell a human-confirmed fact from unvalidated ingested
  text." Severity: **critical**.
- **Finding if present but unconstrained**: if the column exists but has
  no `CHECK` constraint or enum-like restriction (any string accepted),
  note it as **medium**: "Provenance column exists but accepts arbitrary
  values — a bug or malicious write can invent a fake high-trust source
  string with no schema-level pushback."

### 2. Vector index tenant/quarantine isolation (severity: high if absent)

```sql
SELECT index_name, column_names
FROM information_schema.statistics  -- fallback if the query below isn't available
WHERE table_schema = '<schema>' AND table_name = '<table>';

-- Preferred: inspect the actual index definition
SHOW INDEXES FROM <schema>.<table>;

-- Or, for the CREATE TABLE form showing VECTOR INDEX clauses directly:
SHOW CREATE TABLE <schema>.<table>;
```

For each `VECTOR INDEX`, check its column list. A vector index declared
as `VECTOR INDEX (embedding)` with no prefix columns puts every row —
regardless of tenant, workspace, or trust status — into one shared
approximate-nearest-neighbor search space. CockroachDB's C-SPANN indexing
maintains a *separate tree per distinct prefix-column value*
(`cockroachlabs.com/docs/*/vector-indexes`), so a prefix column isn't
just a query filter, it's a structural isolation boundary — one tenant's
retrieval can surface another tenant's (or a quarantined/revoked
memory's) vector as a nearest-neighbor candidate if no prefix column
exists.

- **Finding if no prefix column** (index is `VECTOR INDEX (embedding)`
  alone, or prefixed only by a non-isolating column like an unrelated
  timestamp): "Vector index has no tenant/status prefix column — cross-
  tenant or cross-status vector leakage is possible at the index level,
  not just an application-filter bug." Severity: **high** (critical if
  the table is confirmed multi-tenant).
- **Finding if a status-like column exists but ISN'T in the vector index
  prefix**: same severity — the column existing elsewhere in the table
  doesn't help if retrieval queries can't cheaply filter on it via the
  index. Check specifically whether a `status`/`tenant_id`/`workspace_id`
  column is both present AND part of the vector index's own column list.

### 3. No contradiction/conflict handling (severity: high if absent)

```sql
-- Does the memory table itself support a superseded/quarantined lifecycle?
SELECT column_name, check_clause
FROM information_schema.check_constraints cc
JOIN information_schema.constraint_column_usage ccu USING (constraint_name)
WHERE ccu.table_name = '<table>';

-- Is there a sibling table that logs adjudication between conflicting claims?
SELECT table_name FROM information_schema.tables
WHERE table_schema = '<schema>'
  AND table_name ~* 'contradiction|conflict|dispute|adjudicat';
```

If two rows can assert incompatible things about the same subject (e.g.
"IP X is malicious" and "IP X is benign"), and there's no mechanism that
detects this and no table recording how it was resolved, an attacker
doesn't need to win outright — corroborating noise alone can eventually
outweigh a correct belief with no auditable adjudication ever having
happened.

- **Finding if no status lifecycle AND no adjudication table**: "No
  contradiction detection or adjudication trail found. Conflicting
  beliefs about the same subject can coexist silently, with no record of
  which one a decision relied on or why." Severity: **high**.
- **Finding if a status lifecycle exists but no adjudication table**:
  **medium** — "Rows can apparently be marked superseded/quarantined, but
  there's no table recording the adjudicator or rationale for WHY — an
  auditor can see THAT a belief was overridden, not on what basis."

### 4. No temporal audit trail (severity: medium if absent)

```sql
-- Is GC TTL configured generously enough for AS OF SYSTEM TIME to be
-- practically useful for forensic replay, or is it left at a short default?
SHOW ZONE CONFIGURATION FOR TABLE <schema>.<table>;

-- Is there an append-only ledger/audit-log table as a fallback/primary
-- audit trail independent of MVCC history?
SELECT table_name FROM information_schema.tables
WHERE table_schema = '<schema>'
  AND table_name ~* 'ledger|audit_log|event_log|change_log';
```

Without either a generous `gc.ttlseconds` (so `AS OF SYSTEM TIME` can
reconstruct genuinely historical belief state) or an explicit audit
ledger table, there is no way to answer "what did the agent believe when
it made decision X" after the fact — which means there's no way to find
every decision a since-corrected belief touched, and no way to prove to
an auditor what happened.

- **Finding if GC TTL is at cluster default AND no ledger table exists**:
  "No temporal audit trail: GC TTL is too short for meaningful historical
  reads, and there's no append-only ledger as a fallback. A poisoned
  belief that's later corrected leaves no way to find what it affected."
  Severity: **medium** (raise to **high** if the table is confirmed to
  drive any suppressive or actuating decisions, not purely informational
  ones).
- **Finding if a ledger table exists**: check whether it's genuinely
  append-only and hash-chained (look for `prev_hash`/`entry_hash`-style
  columns) versus just a plain insert-only log with no tamper-evidence.
  Report the distinction — an insert-only log without hash-chaining can
  still be edited in place by anyone with write access; note this as
  **low** if no hash-chaining is found, not a full pass.

## Output format

Report findings as a list, most severe first:

```json
[
  {
    "check": "vector_index_isolation",
    "severity": "high",
    "finding": "VECTOR INDEX memories_vec_idx declared as (embedding) with no prefix column.",
    "evidence": "SHOW CREATE TABLE output: VECTOR INDEX memories_vec_idx (embedding)",
    "recommendation": "Redeclare with a tenant/status prefix, e.g. VECTOR INDEX (workspace_id, status, embedding). Requires recreating the table or the index inline at CREATE TABLE time — CREATE VECTOR INDEX against a populated table blocks mutations on the vector column until backfill completes, so plan a migration window."
  }
]
```

Include a `not_evaluated` entry (with reason) for any check that couldn't
run, rather than omitting it — a silent omission reads as "passed" to
whoever consumes the report.

## Non-goals

This skill audits schema-level integrity structure. It does not:
- Evaluate the QUALITY of an existing adjudication/provenance
  implementation's application logic (only that the schema-level
  scaffolding for one exists).
- Perform any write, migration, or fix — it reports findings only. Fixing
  a finding (e.g. adding a provenance column to a live table with data)
  is a schema migration with its own blast radius and deserves its own
  reviewed change, not an action this skill takes autonomously.
- Replace a full security review of the application code that reads from
  and writes to the audited table.
