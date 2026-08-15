-- PALIMPSEST — schema.sql
-- Single source of truth for the belief store. Read CONTEXT.md before
-- editing this file — several constraints below exist because of specific
-- CockroachDB behavior documented in docs/COCKROACH_NOTES.md, not style
-- preference:
--
--   * Vector indexes are declared INLINE at CREATE TABLE time. Running
--     CREATE VECTOR INDEX against an already-populated table blocks
--     mutations on the vector column until backfill completes.
--   * `memories` is kept to a SINGLE column family (the default -- no
--     FAMILY clauses anywhere in this file). Multiple column families on a
--     vector-indexed table can return incorrect vector search results
--     (cockroachdb/cockroach#146046).
--   * The vector index prefix is (workspace_id, status) -- this is the
--     mechanism that gives us both tenant isolation and quarantine
--     isolation (CockroachDB maintains a separate k-means tree per
--     distinct prefix value; quarantining a memory moves its vector into
--     a different tree, it does not just hide a row).
--   * Every row this schema will ever want to time-travel into stores
--     cluster_logical_timestamp(), never now() (see decisions.decided_hlc).
--     AS OF SYSTEM TIME rewind depends on this.
--
-- Apply with `python database/migrate.py`. Every statement is idempotent
-- (CREATE ... IF NOT EXISTS) so re-running this file is always safe.

-- =============================================================================
-- 1. workspaces -- tenant boundary. Also the vector index prefix root.
-- =============================================================================
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name STRING NOT NULL,
    autonomy_ceiling STRING NOT NULL DEFAULT 'suppressive'
        CHECK (autonomy_ceiling IN ('informational', 'suppressive', 'actuating')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =============================================================================
-- 2. agents -- the identities that call MemoryGate.
-- =============================================================================
CREATE TABLE IF NOT EXISTS agents (
    agent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    role STRING NOT NULL,
    model_id STRING NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS agents_workspace_idx ON agents (workspace_id);

-- =============================================================================
-- 3. memories -- THE belief store. memory/gate.py is the only writer.
--
-- Integrity lattice (Biba "no write-up"), enforced HERE in SQL, not just in
-- application code, per CONTEXT.md's non-negotiable #1:
--
--   source_kind        integrity_level   may hold capability_ceiling up to
--   ------------------ ---------------   -----------------------------------
--   untrusted_ingest    1                informational only
--   agent_inferred      2                informational only
--   verified_tool       3                informational, suppressive
--   human_confirmed     4                informational, suppressive, actuating
--
-- A row that violates either mapping is rejected by CockroachDB itself,
-- independent of whatever memory/gate.py does -- this is the "SQL CHECK
-- *and* retrieval filter" enforcement CONTEXT.md requires.
-- =============================================================================
CREATE TABLE IF NOT EXISTS memories (
    memory_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,

    status STRING NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'quarantined', 'revoked')),

    -- claim identity: (subject_key, predicate) is what contradiction
    -- detection matches on; object_value/polarity is what's being claimed.
    subject_key STRING NOT NULL,
    predicate STRING NOT NULL,
    object_value STRING NOT NULL,
    polarity STRING NOT NULL DEFAULT 'assert'
        CHECK (polarity IN ('assert', 'negate')),
    claim STRING NOT NULL, -- human-readable rendering, set by the gate

    -- provenance / integrity lattice
    source_kind STRING NOT NULL
        CHECK (source_kind IN ('human_confirmed', 'verified_tool', 'agent_inferred', 'untrusted_ingest')),
    integrity_level INT2 NOT NULL CHECK (integrity_level BETWEEN 1 AND 4),
    capability_ceiling STRING NOT NULL
        CHECK (capability_ceiling IN ('informational', 'suppressive', 'actuating')),
    provenance JSONB NOT NULL DEFAULT '{}',

    -- source_kind <-> integrity_level must always agree (defense in depth
    -- alongside memory/lattice.py's INTEGRITY_BY_SOURCE mapping)
    CONSTRAINT source_integrity_consistent CHECK (
        (source_kind = 'human_confirmed' AND integrity_level = 4) OR
        (source_kind = 'verified_tool'    AND integrity_level = 3) OR
        (source_kind = 'agent_inferred'   AND integrity_level = 2) OR
        (source_kind = 'untrusted_ingest' AND integrity_level = 1)
    ),

    -- Biba no-write-up: a belief's integrity caps the capability class it
    -- may ever influence. This is the lattice, enforced at the DDL level.
    CONSTRAINT capability_requires_integrity CHECK (
        (capability_ceiling = 'informational') OR
        (capability_ceiling = 'suppressive' AND integrity_level >= 3) OR
        (capability_ceiling = 'actuating'   AND integrity_level >= 4)
    ),

    confidence FLOAT8 NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    eff_confidence FLOAT8 NOT NULL CHECK (eff_confidence BETWEEN 0 AND 1),

    corroborations INT8 NOT NULL DEFAULT 0,
    refutations INT8 NOT NULL DEFAULT 0,

    -- Titan Text Embeddings V2 @ 1024 dims, L2-normalised in application
    -- code before insert (memory/gate.py) so <-> is cosine-rank-equivalent.
    embedding VECTOR(1024),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Prefix = (workspace_id, status): tenant isolation + quarantine
    -- isolation from the SAME mechanism. Never filter retrieval on a
    -- non-prefix column and expect this index to accelerate it.
    VECTOR INDEX memories_vec_idx (workspace_id, status, embedding)
);

-- Contradiction detection looks up the current active belief for a given
-- (workspace, subject, predicate) before every admit() -- this index makes
-- that lookup cheap without touching the vector index at all.
CREATE INDEX IF NOT EXISTS memories_subject_predicate_idx
    ON memories (workspace_id, subject_key, predicate, status);

CREATE INDEX IF NOT EXISTS memories_workspace_status_idx
    ON memories (workspace_id, status, created_at DESC);

-- =============================================================================
-- 4. contradictions -- every adjudication the gate ever ran, win or lose.
-- =============================================================================
CREATE TABLE IF NOT EXISTS contradictions (
    contradiction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    incumbent_memory_id UUID NOT NULL REFERENCES memories(memory_id),
    challenger_memory_id UUID NOT NULL REFERENCES memories(memory_id),
    verdict STRING NOT NULL
        CHECK (verdict IN ('supersede', 'quarantine', 'llm_adjudicated')),
    -- 'rule:integrity_dominance' | 'rule:integrity_subordinate' | 'bedrock:<model-id>'
    adjudicator STRING NOT NULL,
    rationale STRING NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS contradictions_workspace_idx ON contradictions (workspace_id, created_at DESC);

-- =============================================================================
-- 5. approvals -- human-in-the-loop queue: quarantined memories AND
--    decisions above workspace.autonomy_ceiling both land here.
-- =============================================================================
CREATE TABLE IF NOT EXISTS approvals (
    approval_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    subject_type STRING NOT NULL CHECK (subject_type IN ('memory', 'decision')),
    subject_id UUID NOT NULL,
    reason STRING NOT NULL,
    actor STRING,
    status STRING NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS approvals_workspace_status_idx ON approvals (workspace_id, status);

-- =============================================================================
-- 6. decisions -- every verdict an agent produced, and the exact HLC it
--    produced it at. decided_hlc is what rewind's AS OF SYSTEM TIME uses.
-- =============================================================================
CREATE TABLE IF NOT EXISTS decisions (
    decision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES agents(agent_id),
    alert_ref STRING NOT NULL,
    -- The full alert dict TriageAgent.decide() was given (source_ip,
    -- dest_host, signature, raw_log, ...). Required for rewind/apply to
    -- actually replay a decision later -- alert_ref alone isn't enough to
    -- reconstruct what triage.decide() was called with.
    alert_payload JSONB NOT NULL DEFAULT '{}',
    verdict STRING NOT NULL CHECK (verdict IN ('suppress', 'escalate', 'allow')),
    rationale STRING NOT NULL,
    -- cluster_logical_timestamp() at decision time -- NEVER now(). This is
    -- the literal value passed to AS OF SYSTEM TIME during rewind.
    decided_hlc DECIMAL NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS decisions_workspace_created_idx ON decisions (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS decisions_alert_ref_idx ON decisions (workspace_id, alert_ref);

-- =============================================================================
-- 7. decision_memory_refs -- exactly which memories influenced which
--    decision, and how much. Scores are written verbatim from
--    gate.retrieve()'s return value -- never recomputed downstream.
-- =============================================================================
CREATE TABLE IF NOT EXISTS decision_memory_refs (
    decision_id UUID NOT NULL REFERENCES decisions(decision_id) ON DELETE CASCADE,
    memory_id UUID NOT NULL REFERENCES memories(memory_id),
    rank INT2 NOT NULL,
    semantic_score FLOAT8 NOT NULL,
    eff_confidence FLOAT8 NOT NULL,
    integrity_level INT2 NOT NULL,
    total_score FLOAT8 NOT NULL,
    influence FLOAT8 NOT NULL,
    PRIMARY KEY (decision_id, memory_id)
);

CREATE INDEX IF NOT EXISTS decision_memory_refs_by_memory_idx ON decision_memory_refs (memory_id);

-- =============================================================================
-- 8. memory_ledger -- append-only, hash-chained audit trail. Every admit /
--    supersede / quarantine / revoke / decision the gate performs gets one
--    row here, inside the SAME transaction as the state change it records.
--
--    `seq` is assigned by the application (memory/gate.py) as
--    MAX(seq WHERE workspace_id = $1) + 1, inside the same SERIALIZABLE
--    transaction as the write it's logging -- this keeps the chain gapless
--    and contiguous PER WORKSPACE without relying on a distributed
--    sequence. entry_hash = sha256(prev_hash || canonical_json(payload));
--    prev_hash of seq=0 is the fixed genesis constant '0' * 64
--    (see memory/gate.py GENESIS_HASH).
-- =============================================================================
CREATE TABLE IF NOT EXISTS memory_ledger (
    seq INT8 NOT NULL,
    workspace_id UUID NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    event_type STRING NOT NULL
        CHECK (event_type IN ('admit', 'supersede', 'quarantine', 'revoke', 'decision')),
    payload JSONB NOT NULL,
    prev_hash STRING NOT NULL,
    entry_hash STRING NOT NULL,
    exported_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, seq)
);

-- Prompt 6's ledger_export Lambda scans exactly this partial index.
CREATE INDEX IF NOT EXISTS ledger_unexported
    ON memory_ledger (workspace_id, seq)
    WHERE exported_at IS NULL;

-- =============================================================================
-- 9. rewinds -- one row per rewind request: the diff it computed, the
--    blast radius, and (once applied) how many verdicts flipped.
-- =============================================================================
CREATE TABLE IF NOT EXISTS rewinds (
    rewind_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    target_hlc DECIMAL NOT NULL,
    trigger_memory UUID REFERENCES memories(memory_id),
    belief_diff JSONB,
    decisions_in_blast_radius INT8,
    verdict_flips INT8,
    state STRING NOT NULL DEFAULT 'awaiting_approval'
        CHECK (state IN ('awaiting_approval', 'applied')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS rewinds_workspace_idx ON rewinds (workspace_id, created_at DESC);

-- =============================================================================
-- 10. Manual, cluster-tier-dependent operational step (NOT run by
--     migrate.py -- see database/README.md for why and the fallback path).
-- =============================================================================
-- ALTER TABLE memories CONFIGURE ZONE USING gc.ttlseconds = 172800;

-- =============================================================================
-- 10b. Reference query patterns (not executed by migrate.py -- copied into
--      api/routes/rewind.py). Documented here so the SQL a rewind actually
--      runs lives next to the schema it reads.
-- =============================================================================
-- Belief state at a past HLC (preferred path, requires AS OF SYSTEM TIME
-- to still be inside gc.ttlseconds):
--
--   SELECT memory_id, status, claim, integrity_level, capability_ceiling
--   FROM memories AS OF SYSTEM TIME <decided_hlc>
--   WHERE workspace_id = $1;
--
-- Belief diff for rewind (then vs now): NOT a single combined query.
-- Confirmed empirically while building api/routes/rewind.py — CockroachDB
-- rejects a table-level AS OF SYSTEM TIME clause nested in a subquery
-- alongside a live-read sibling in the same statement
-- ("AS OF SYSTEM TIME must be provided on a top-level statement"). Run as
-- two separate top-level statements instead, and diff them in application
-- code (see api/routes/rewind.py's create_rewind):
--
--   -- statement 1 (historical):
--   SELECT memory_id, status, claim FROM memories AS OF SYSTEM TIME <target_hlc>
--   WHERE workspace_id = $1;
--
--   -- statement 2 (current):
--   SELECT memory_id, status, claim FROM memories WHERE workspace_id = $1;
--
-- Same output shape both sides so the API can serve either side from
-- memory/ledger_replay.py's fallback interchangeably when AS OF SYSTEM
-- TIME isn't available (GC TTL exceeded or cluster tier restriction).
