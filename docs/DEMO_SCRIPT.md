# Demo Script

Shot-by-shot for the <3 minute submission video. Every command below is
real and runs against this repo as built — nothing here is aspirational.

## Before filming

```bash
# 1. Cluster reachable, schema applied (see database/README.md)
export PALIMPSEST_DSN="postgresql://...@...:26257/palimpsest?sslmode=verify-full"
python database/migrate.py

# 2. AWS credentials + Bedrock model access confirmed
export AWS_REGION=us-east-1
python -m agent.bedrock_client   # smoke test — must print "Bedrock smoke test PASSED."

# 3. API running
uvicorn api.main:app --reload &

# 4. Console running
cd console && npm run dev &

# 5. Fresh demo state
bash demo/reset.sh
```

Keep a second terminal open on `python -m demo.attack_scenario` — that
single command produces the entire narrative below, in order, with clear
`PHASE N` banners already printed for you to read from or cut to.

## Shot list

**[0:00–0:15] Cold open — the pitch**
On camera or voiceover, no screen yet: "Agents write beliefs to memory
constantly and read them back with total trust. Poison it once — plant
an instruction in a ticket comment an agent ingests — and it's permanent.
Palimpsest gates every belief through an integrity lattice, adjudicates
contradictions atomically, and can rewind an agent's memory to find and
fix everything a poisoned belief touched."

**[0:15–0:35] Baseline — screen: terminal running `demo/attack_scenario.py`**
Let PHASE 1 print. Point out: alert from `10.0.0.7`, verdict `SUPPRESS`,
citing the `verified_tool` memory with `influence=1.00`. This is the
control case — a real internal scanner, correctly suppressed.

**[0:35–1:00] The injection — screen: terminal, then switch to `/memories` in the console**
Let PHASE 2 print. Read the injected ticket-comment text on screen. Show
the terminal output: Layer 1 (admission-time cap) rejects the attacker's
stated intent (`SUPPRESSIVE`) outright — *before any database write* —
then admits the same content at `informational`, because refusing to
store an untrusted comment at all isn't realistic; what matters is
whether it can ever justify an action. Cut to the console's `/memories`
view, filter to the new memory, point out its badges:
`untrusted_ingest` (red) and `informational` capability.

**[1:00–1:35] The toggle — screen: terminal, split or cut**
This is the non-negotiable "same attack twice" beat. Let PHASE 3A print:
gate disabled, the exact same planted memory gets retrieved for a REAL
exploit alert from `185.220.101.44`, verdict flips to `SUPPRESS`. Say it
plainly: "breach." Let PHASE 3B print immediately after: gate re-enabled,
fresh workspace, same injection, same real attack alert — verdict
correctly `ESCALATE`. Same attack, opposite outcome, only the filter
toggled.

**[1:35–2:20] Rewind — screen: terminal, then `/rewind` in the console**
Let PHASE 4 print through the 3 blast-radius decisions (all wrongly
suppressed) and the revoke. Then switch to the console: `/rewind`, select
one of those decisions from the dropdown, paste the poisoned
`memory_id`, click **Preview Rewind** — point out the belief diff
(`active → revoked`) and blast radius count. Click **Apply Replay**.
Let the big number land on screen: `verdict_flips`. This is the climax —
hold on it for a beat.

**[2:20–2:40] The SQL pane — proof it's not a black box**
On `/memories`, click **Run** on the quarantine-check query — real SQL,
real result, in the console the judges are already looking at. Same on
`/rewind`'s `AS OF SYSTEM TIME` query pane if time allows.

**[2:40–3:00] Close**
"Every piece of this ran against a real CockroachDB cluster and real AWS
Bedrock — nothing in this demo is mocked." One-line recap of the four
CockroachDB tools and Bedrock, cut to the README on screen with the repo
URL.

## If Day 3 goes badly

Per `CONTEXT.md`'s own guidance: `demo/attack_scenario.py`'s terminal
output alone, without the console, is still a complete, compelling demo.
If the console isn't ready or breaks under filming pressure, film the CLI
output directly — a working CLI demo beats a half-built UI on screen.

## Known gotcha, worth avoiding on camera

`AS OF SYSTEM TIME` reads are bounded by `gc.ttlseconds` — see
`database/README.md` step 4. If the cluster tier rejects the zone config,
`memory/ledger_replay.py` serves the same rewind output from the ledger
instead; the console and API don't need to know which path served the
request, but if you're filming live and see an `AS OF SYSTEM TIME`-related
error, that's the tell — don't panic, the fallback is already wired in,
just confirm `gc.ttlseconds` was actually applied per the setup steps
before filming.
