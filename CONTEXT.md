# PALIMPSEST — Master Context

Read this before touching any file. Every build prompt in `BUILD_PROMPTS.md`
assumes this document is in context. If you (the coding agent) are ever
unsure what to do, re-read this file before guessing.

## What this is

Palimpsest is a memory integrity layer for AI agents. Agents write beliefs
to long-term memory constantly and read them back with total trust — which
makes memory the softest attack surface in the stack. Poison it once
(e.g. plant an instruction in a ticket comment an agent ingests) and the bad
instruction is permanent: every future retrieval launders it back in as
trusted context.

Palimpsest gates every belief through:
1. **An integrity lattice** (Biba "no write-up" applied to agent cognition) —
   a belief's source authority caps what kind of decision it may influence.
2. **Atomic contradiction adjudication** — conflict detection, arbitration,
   and write happen inside one CockroachDB `SERIALIZABLE` transaction.
3. **Rewind** — `AS OF SYSTEM TIME` reconstructs exactly what an agent
   believed at any past decision, finds every decision a poisoned belief
   touched (blast radius), and replays them against corrected memory.

Built for: **CockroachDB × AWS Hackathon — Build with Agentic Memory**
(https://cockroachdb-ai.devpost.com/). Deadline **Aug 18, 2026, 5:00pm EDT**.

## Non-negotiables (never cut these, ever)

- The integrity lattice (`Integrity` × `Capability`, enforced in SQL CHECK
  *and* in the retrieval filter).
- Serializable, atomic contradiction adjudication with a real `40001` retry
  loop — not a mocked one.
- `AS OF SYSTEM TIME` rewind with a real blast-radius replay.
- The on/off toggle in the demo — same attack, twice, opposite outcomes.
- Public repo, Apache-2.0 LICENSE **visible in the GitHub About section**,
  live demo URL, <3 min public YouTube/Vimeo video.

## Cut list, in order, if time runs out

1. Multi-region (`REGIONAL BY ROW`) — document as roadmap only.
2. KMS-signed ledger entries — keep the SHA-256 hash chain, drop signing.
3. Step Functions orchestration — call `replay()` directly from the API.
4. `ccloud` backup automation — script it and document the command; doesn't
   need to run inside the demo flow.
5. EventBridge — direct function calls are fine for the MVP.
6. Belief-graph visualization — the timeline + SQL pane carry the demo
   without it.

**Never cut:** items in "Non-negotiables" above.

## Ground truth about the hackathon (verified, not assumed)

- Must use **≥2** of: CockroachDB Cloud Managed MCP Server, Distributed
  Vector Indexing, `ccloud` CLI, Agent Skills Repo. Palimpsest uses **all
  four** — see `docs/COCKROACH_NOTES.md` for exactly how and why each one
  is load-bearing, not decorative.
- Must use **≥1** AWS service. Palimpsest uses Bedrock (Claude for triage/
  adjudication, Titan Text Embeddings V2 @ 1024 dims), Lambda, S3 with
  Object Lock, and optionally Step Functions/EventBridge (see cut list).
- Judging is five criteria, equally weighted: Agentic Memory Design,
  Technical Implementation, Real-World Impact, **Production Readiness**,
  Creativity & Originality. Production Readiness is the gap almost no
  4-day submission scores — Palimpsest's entire thesis is "what happens
  when memory goes wrong," so it scores this criterion by existing.
- CockroachDB Cloud free tier is fully eligible. No credit card required.

## Technical facts that constrain every implementation decision

- **`CREATE VECTOR INDEX` on an already-populated table blocks mutations on
  the vector column until backfill completes.** Always declare vector
  indexes inline in `CREATE TABLE`. Never run it as an `ALTER` against live
  data during the hackathon.
- **Do not add column families to any table with a vector index.** Vector
  index reads can return incorrect results across multiple column families
  (cockroachdb/cockroach#146046). Keep `memories` as a single column family
  (the default — don't manually split it).
- **Vector index filter acceleration only works on prefix columns.** Our
  prefix is `(workspace_id, status)`. Never filter retrieval on a non-prefix
  column and expect the vector index to accelerate it.
- **Embeddings are L2-normalised in application code before insert.** This
  makes the L2 operator (`<->`), which the vector index actually accelerates,
  rank-equivalent to cosine distance. Never skip normalization — it's the
  reason index metric and query metric provably agree.
- **`AS OF SYSTEM TIME` can only reach back as far as the GC TTL.** Default
  is short. On Day 1, run:
  `ALTER TABLE memories CONFIGURE ZONE USING gc.ttlseconds = 172800;`
  If the cluster tier rejects zone configs, fall back to replaying from
  `memory_ledger` instead — implement this fallback path regardless, don't
  discover you need it on the last night.
- **`cluster_logical_timestamp()`** is what gets stored on every `decisions`
  row as `decided_hlc`. This is the literal value passed to
  `AS OF SYSTEM TIME` during rewind. Do not substitute `now()` — HLC, not
  wall clock.
- CockroachDB is PostgreSQL wire-compatible. Use `psycopg` (Python) or `pg`
  (Node) exactly as you would against Postgres, with one exception: always
  wrap writes in explicit retry loops for `SerializationFailure` (`40001`).
  This is expected, routine behavior under concurrent agent writes, not a
  bug to work around by lowering isolation.

## Repository ground truth

See `FILE_STRUCTURE.md` for the full tree. Key rule: **the gate
(`memory/gate.py`) is the only write path into `memories`.** No other module
may `INSERT INTO memories` directly. If a build prompt asks you to write to
memory anywhere outside the gate, stop and flag it — that's a violation of
the entire security argument the project makes.

## Voice for all generated docs, READMEs, and demo copy

Direct, technical, no hedging, no marketing fluff. Say what the system does
and why CockroachDB is structurally required for it — never "leverage" or
"seamlessly" or "empower." Every claim about CockroachDB behavior must be
something actually verifiable in the docs referenced in
`docs/COCKROACH_NOTES.md`, not invented.
