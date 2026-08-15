# PALIMPSEST — File Structure

```
palimpsest/
├── LICENSE                          Apache-2.0 (must render in GitHub About)
├── README.md                        Judge-facing entry point (Prompt 10)
├── CONTEXT.md                       Project bible — read first, always
├── FILE_STRUCTURE.md                This file
├── BUILD_PROMPTS.md                 The 10 numbered build prompts
├── requirements.txt
├── .env.example
├── .gitignore
│
├── database/
│   ├── schema.sql                   Full DDL — the single source of truth
│   ├── migrate.py                   Idempotent schema apply
│   └── README.md                    Cluster setup, GC TTL config
│
├── memory/                          THE ONLY WRITE PATH INTO `memories`
│   ├── gate.py                      MemoryGate: admit / retrieve / revoke
│   ├── lattice.py                   Integrity, Capability enums
│   ├── ledger_replay.py             AS OF SYSTEM TIME fallback (Prompt 6)
│   └── __init__.py
│
├── agent/
│   ├── bedrock_client.py            Bedrock smoke test + shared client
│   ├── triage.py                    TriageAgent — observe/decide/act
│   ├── ingest.py                    Three provenance-tiered ingest paths
│   └── __init__.py
│
├── api/
│   ├── main.py                      FastAPI app entrypoint
│   └── routes/
│       ├── decisions.py
│       ├── memories.py
│       ├── rewind.py
│       └── ledger.py
│
├── console/                         Next.js — the SOC forensic console
│   └── app/
│       ├── timeline/
│       ├── memories/
│       ├── rewind/
│       ├── sql-pane/                 Fixed labeled queries + results
│       └── api-client.ts
│
├── infrastructure/                  AWS CDK (Python)
│   ├── app.py
│   ├── stacks/
│   │   └── palimpsest_stack.py
│   ├── lambda/
│   │   ├── gate_handler/
│   │   └── ledger_export/
│   │       └── handler.py
│   └── README.md
│
├── skills/                          Upstream contribution (Prompt 9)
│   └── audit-agent-memory-integrity/
│       └── SKILL.md
│
├── tests/
│   ├── conftest.py
│   ├── test_integrity_lattice.py
│   ├── test_concurrent_admission.py
│   ├── test_ingest_capability_cap.py
│   ├── test_api_rewind.py
│   └── test_ledger_integrity.py
│
├── demo/
│   ├── seed.py                      Baseline workspace + trusted memory
│   ├── attack_scenario.py           The exact 4-phase demo, scripted
│   └── reset.sh                     Wipe + reseed for repeat filming
│
└── docs/
    ├── ARCHITECTURE.md
    ├── THREAT_MODEL.md
    ├── DEMO_SCRIPT.md
    ├── COCKROACH_NOTES.md            Verified technical facts + citations
    └── SKILLS_PR.md                  How to open the upstream PR
```

## The one rule that matters more than the tree

`memory/gate.py` is the **only** module permitted to `INSERT INTO memories`,
`UPDATE memories`, or write to `memory_ledger`. Every other module —
`agent/`, `api/`, `console/` — calls through `MemoryGate.admit()`,
`.retrieve()`, or `.revoke()`. This is not a style preference; it's the
actual security property the entire project claims to have. A build prompt
that routes around the gate has broken the thesis, not just the code style.
