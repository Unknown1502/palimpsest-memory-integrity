# Architecture

Rendered diagrams (system flow, integrity lattice, the `admit()`
transaction, rewind, and the deployed AWS shape) are in the root
[`README.md`](../README.md#architecture). This file is the prose
walkthrough: what each component does and why it's shaped that way.

The same structure as a text tree, kept here because it survives
`grep`, `git diff`, and a terminal with no Mermaid renderer:

```
[1] UNTRUSTED CONTENT SOURCES
    ticket comments · tool output · operator statements
        |
        v
[2] agent/ingest.py
    3 provenance-tiered paths: ingest_ticket_comment / ingest_tool_output /
    ingest_operator_statement. Capability is requested by the CALLER;
    the LLM extraction step has no field for it at all.
        |
        |  Claim + Provenance
        v
====================================================================
[3] memory/gate.py — MemoryGate
    THE ONLY WRITE PATH INTO `memories` / `memory_ledger`

      admit()             lattice check (memory/lattice.py) BEFORE any
                           DB connection opens; contradiction detection +
                           adjudication + hash-chained ledger write, all
                           inside ONE SERIALIZABLE transaction with a
                           real 40001 retry loop (_with_retry)
      retrieve()           filters on capability_ceiling AND
                           integrity_level, independently (defense in depth)
      revoke()             status='revoked' + blast_radius() + ledger entry
      blast_radius()       every decision that cited a memory_id
      belief_state_at()    AS OF SYSTEM TIME reconstruction
====================================================================
        |
        v
[4] CockroachDB — database/schema.sql
      memories                the belief store, VECTOR INDEX prefixed
                               (workspace_id, status)
      contradictions          every adjudication run, win or lose
      approvals               human-in-the-loop queue
      decisions +
      decision_memory_refs    what an agent decided, citing exactly
                               which memories at what scores
      memory_ledger           hash-chained audit trail, one row per
                               state change, same transaction as the write
      rewinds                 belief diffs + replay results
        |
        +-------------------------+-------------------------+
        v                         v                         v
[5a] agent/triage.py      [5b] api/main.py           [5c] memory/ledger_replay.py
     TriageAgent                FastAPI service              AS OF SYSTEM TIME fallback:
     observe/decide/act         decisions / memories /        reconstructs belief state
     calls gate.retrieve()      approvals / rewind /           purely from memory_ledger
     through the gate,          ledger routes                   when GC TTL / cluster tier
     never around it                                              don't allow the real thing
        |                         |
        v                         v
[6] agent/llm.py             [7] console/ (Next.js)
    provider switch:               /timeline  /memories  /rewind + SQL pane
    PALIMPSEST_LLM_PROVIDER
    selects chat()/adjudicate()
    from either:
      agent/bedrock_client.py    Titan embed() always here regardless
        (default) via the         of provider; Claude via the Bedrock
        Bedrock Messages API      Messages API
      agent/anthropic_client.py
        (anthropic_api) direct
        console.anthropic.com
        Messages API

[8] infrastructure/ (AWS CDK)
    GateHandler Lambda        api/main.py via Mangum, behind a Function URL
    LedgerExportHandler Lambda 5-min EventBridge schedule -> S3 bucket
                               with Object Lock (governance mode)
    Secrets Manager            PALIMPSEST_DSN, injected via a CloudFormation
                               dynamic reference, never a plaintext CDK value
```

## The one rule that matters more than the tree

`memory/gate.py` is the only module permitted to `INSERT INTO memories`,
`UPDATE memories`, or write to `memory_ledger`. Every other component —
`agent/`, `api/`, `console/` — calls through `MemoryGate.admit()`,
`.retrieve()`, `.revoke()`, `.blast_radius()`, or `.belief_state_at()`.
This isn't a style preference; the entire security argument this project
makes depends on there being exactly one, auditable place beliefs enter
and change status.

## Components, by file

### `memory/` — the belief store and its gate

- **`memory/lattice.py`** — `Integrity` (1-4) and `Capability`
  (informational/suppressive/actuating) enums, the
  `CAPABILITY_MIN_INTEGRITY` mapping, and `check_capability_allowed()`,
  which raises `IntegrityViolation` before any database connection opens.
  Mirrors `database/schema.sql`'s two `CHECK` constraints exactly, so the
  same invariant is enforced in Python (fast reject, zero DB round-trip)
  and in SQL (defense in depth if some future caller bypasses this
  module).
- **`memory/gate.py`** — `MemoryGate`. `admit()` runs contradiction
  detection (same subject_key+predicate, different claim) and
  adjudication (integrity dominance, integrity subordinate, or an
  injected `adjudicate_fn` on a tie) inside one `SERIALIZABLE`
  transaction, with `_with_retry()` catching CockroachDB's `40001`
  (`SerializationFailure`) and retrying with backoff — verified live to
  actually fire, not just present as unreached code (see
  `tests/test_concurrent_admission.py`). `retrieve()` filters candidates
  on both `capability_ceiling` and `integrity_level` independently.
  `belief_state_at()` uses a dedicated `autocommit=True` connection,
  because a table-level `AS OF SYSTEM TIME` clause must be the first
  statement of a transaction with no timestamp already established — a
  real constraint found by hitting it live.
- **`memory/ledger_replay.py`** — `replay_state_at()`, the fallback
  belief-state reconstruction when a cluster tier doesn't support the GC
  TTL zone config `belief_state_at()` needs. Returns the same field shape
  as `belief_state_at()` (verified in
  `tests/test_ledger_integrity.py::test_ledger_replay_matches_belief_state_at_shape`)
  so `api/routes/rewind.py` never needs to know which path served a
  request.

### `agent/` — the thing that actually uses the gate

- **`agent/llm.py`** — the provider switch. Reads
  `PALIMPSEST_LLM_PROVIDER` (`bedrock`, the default, or `anthropic_api`)
  and re-exports `chat()`/`adjudicate()` from whichever module backs it;
  `embed()` always comes from `agent/bedrock_client.py` regardless of the
  switch. Callers that need chat/adjudicate (`api/deps.py`,
  `api/routes/rewind.py`, `demo/attack_scenario.py`) import from here, not
  from either provider module directly, so switching providers is a
  one-line env var change, not a per-call-site edit.
- **`agent/adjudication.py`** — the equal-integrity tie-break prompt and
  JSON-response parsing, shared by both provider modules' `adjudicate()`
  so the prompt only needs to be right in one place.
- **`agent/bedrock_client.py`** — the only module that imports `boto3`.
  `embed()` (Titan Text Embeddings V2, 1024 dims, hard-fails on a
  dimension mismatch against `memory.gate.EMBED_DIMS`), `chat()` (Claude
  via the Messages API on Bedrock — note the model ID is a cross-region
  inference profile, `us.anthropic.claude-haiku-4-5-...` by default, not
  the bare model ID; newer Claude models on Bedrock reject on-demand
  invocation by bare ID. Haiku 4.5 rather than a larger model: cheaper,
  well-suited to this project's structured extraction/classification
  tasks, and — found the hard way — every third-party model has its own
  separate AWS Marketplace subscription, so a model that failed once
  before a payment method was on file can stay stuck even after fixing
  billing, while a never-before-attempted model subscribes cleanly),
  `adjudicate()` (the tie-break `AdjudicateFn` `memory/gate.py`
  accepts as a plain injected callable, keeping `memory/` itself free of
  any AWS dependency).
- **`agent/anthropic_client.py`** — the direct console.anthropic.com
  path (`PALIMPSEST_LLM_PROVIDER=anthropic_api`), used when
  Claude-via-Bedrock is unavailable — e.g. the Marketplace subscription
  issue described above. Same `chat(system, messages, max_tokens)`
  signature as `agent/bedrock_client.py`'s, so `agent/triage.py` and
  `agent/ingest.py` — which take `chat_fn` as an injected callable —
  need zero changes to run against either provider. `adjudicate()`
  reuses `agent/adjudication.py`'s prompt/parsing, not a second copy.
  Model IDs on this path are bare first-party strings (`claude-haiku-4-5`)
  with no `us.anthropic.` prefix or `:0` suffix — that naming is
  Bedrock-specific.
- **`agent/ingest.py`** — three functions matching the three provenance
  tiers, each hardcoding its own `source_kind` and accepting a
  caller-specified `capability` — the LLM extraction step
  (`_extract_claim`) has no field for capability at all, so there's no
  code path by which ingested text could talk its way into a higher
  capability than its channel allows.
- **`agent/triage.py`** — `TriageAgent.decide()` retrieves through the
  gate, builds a prompt that explicitly labels each candidate memory's
  integrity level (so the model never treats a low-integrity memory as
  ground truth just because it was retrieved), writes `decisions` +
  `decision_memory_refs` with the scores exactly as `gate.retrieve()`
  returned them, and routes to `approvals` instead of acting when a
  verdict's required capability exceeds `workspace.autonomy_ceiling`. That
  integrity-level labeling is itself gated on `gate.gate_enabled` — found
  necessary by running the attack demo against a real, live Claude call:
  with only the retrieval filter bypassed and labels still shown, the
  model reliably refused to trust the injected memory anyway (real
  defense-in-depth, but it meant "gate disabled" didn't actually simulate
  an agent without Palimpsest). See `tests/test_triage_naive_baseline.py`.

### `api/` — the FastAPI service

`api/main.py` wires five routers, all scoped by `workspace_id`. The one
non-obvious piece: `UnhandledExceptionMiddleware`, a raw ASGI middleware
registered *before* `CORSMiddleware` — found necessary live, when a real
Bedrock error surfaced as a 500 with no CORS header, which a browser's
`fetch()` reports as an opaque `TypeError: Failed to fetch` instead of a
readable error. `api/routes/rewind.py`'s belief diff runs the historical
and live `SELECT`s as two separate top-level statements, not one combined
query — CockroachDB rejects a table-level `AS OF SYSTEM TIME` nested in a
subquery alongside a live-read sibling (`"AS OF SYSTEM TIME must be
provided on a top-level statement"`), confirmed by hitting the actual
`SyntaxError`.

### `console/` — the SOC forensic console (Next.js)

Three views, all client components polling/fetching against `api/`
through `console/app/api-client.ts`. `/timeline` shows verdicts with
influencing-memory integrity badges; `/memories` is the belief store with
a Blast Radius lookup and the quarantine-check SQL pane; `/rewind` is the
whole point — pick a past decision, preview the belief diff and blast
radius, Apply Replay, and `verdict_flips` is the number the entire demo
builds to.

### `infrastructure/` — AWS CDK

`GateHandler` wraps the *same* `api/main.py` FastAPI app via Mangum,
behind a Function URL — one HTTP surface, two ways to run it.
`LedgerExportHandler` runs on a 5-minute EventBridge schedule, writing to
an S3 bucket with Object Lock enabled (governance mode — settable only at
bucket creation, never after). `PALIMPSEST_DSN` is injected via a
CloudFormation dynamic reference (`secret.secret_value.unsafe_unwrap()`),
never a plaintext CDK value. IAM is scoped to `bedrock:InvokeModel` on the
exact three model/inference-profile ARNs this project uses.

### `demo/` — the attack scenario, scripted

`demo/seed.py` seeds one workspace, one agent, one `verified_tool`
memory. `demo/attack_scenario.py` runs all four phases end-to-end:
baseline suppress, the injection (two defense layers shown explicitly —
admission-time capability cap, then retrieval-time filter), the gate
on/off toggle on the *identical* attack, and rewind (3-decision blast
radius, `AS OF SYSTEM TIME` confirming the poisoned belief was trusted at
decision time, full replay, `verdict_flips`).

## Data flow for one admit()

1. Caller (`agent/ingest.py` or `demo/seed.py`) builds a `Claim` +
   `Provenance` and calls `gate.admit(...)`.
2. `memory/lattice.py`'s `check_capability_allowed()` runs — **before any
   database connection opens**. A rejected admission leaves zero trace,
   not even a failed-attempt row (verified in
   `tests/test_integrity_lattice.py::test_untrusted_ingest_cannot_admit_suppressive`).
3. Inside one `SERIALIZABLE` transaction (`_admit_tx`): lock the existing
   active memory for this `(workspace_id, subject_key, predicate)` via
   `SELECT ... FOR UPDATE`. No incumbent → plain insert. Same claim →
   corroboration (counter bump, no new row). Different claim →
   contradiction: integrity dominance/subordinate resolves by rule; equal
   integrity calls the injected `adjudicate_fn`.
4. Every state change gets a hash-chained `memory_ledger` row
   (`_append_ledger`, same transaction — commits or aborts together with
   the state change it records) — `seq` computed as
   `MAX(seq WHERE workspace_id) + 1` inside the same transaction, keeping
   the chain gapless per workspace without a distributed sequence.
5. If `_with_retry` catches a `40001`, it retries with backoff and logs a
   warning — real, not decorative (`tests/test_concurrent_admission.py`
   forces genuine contention and asserts the retry actually fires).
