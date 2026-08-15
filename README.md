# Palimpsest

A memory integrity layer for AI agents: gates every belief through an
integrity lattice, adjudicates contradictions atomically inside a
CockroachDB `SERIALIZABLE` transaction, and can rewind an agent's memory to
any past decision via `AS OF SYSTEM TIME` to find and correct everything a
poisoned belief touched.

Built for the [CockroachDB × AWS Hackathon](https://cockroachdb-ai.devpost.com/) —
"Build with Agentic Memory."

## Setup

See [`database/README.md`](database/README.md) to stand up a CockroachDB
cluster (Cloud or local) and apply the schema. `CONTEXT.md` is the project
bible — architecture, non-negotiables, and the technical constraints every
file in this repo respects.

This README is a placeholder; the full judge-facing version (architecture
diagram, CockroachDB/AWS tool breakdown, setup, demo links) lands in the
final build pass.
