# Setup — from nothing to a running Palimpsest

Every command here was run on a real machine while building this repo.
Versions listed are the ones actually verified, not minimums invented
after the fact. Where something bit us, the fix is written down next to
it rather than left for you to rediscover.

Two paths:

- **[Quick path](#quick-path-local-docker-~10-minutes)** — local Docker
  CockroachDB, no cloud accounts beyond an LLM key. Enough to run the
  full test suite and the 4-phase demo.
- **[Full path](#full-path-cockroachdb-cloud--aws)** — CockroachDB Cloud
  plus the deployed AWS stack. What the hackathon submission actually
  runs on.

---

## Prerequisites

| Tool | Verified version | Needed for | Notes |
|---|---|---|---|
| Python | 3.11.1 | everything | 3.11+ required (`tomllib`, modern typing) |
| Node.js | 22.17.1 | console, AWS CDK | 20+ works; Next.js 16 needs 18.18+ |
| npm | 11.5.2 | console, CDK CLI | ships with Node |
| Docker | 29.5.3 | local CockroachDB, CDK Lambda bundling | must be *running*, not just installed |
| AWS CLI | 2.35.8 | Bedrock, deploy | v2 required; `aws bedrock` model-access subcommands need ≥ 2.27.42 |
| AWS CDK CLI | 2.1136.0 | deploy only | `npm install -g aws-cdk` |

Optional:

| Tool | Needed for |
|---|---|
| `ccloud` CLI | on-demand CockroachDB Cloud backups ([`../database/README.md`](../database/README.md) §6) |
| `gh` CLI | pushing the repo / releases |

Check everything at once:

```bash
python --version && node --version && docker --version && aws --version
```

---

## 1. Clone and create the Python environment

```bash
git clone https://github.com/Unknown1502/palimpsest-memory-integrity.git
cd palimpsest-memory-integrity

python -m venv .venv
```

Activate it:

```bash
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Windows (Git Bash)
source .venv/Scripts/activate

# macOS / Linux
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

That installs `psycopg[binary]` 3.3.4, `boto3`, `anthropic` 0.122.0,
`fastapi`, `uvicorn`, `pydantic`, `pytest`, `httpx`, and `mangum`.

> **Gotcha — the wrong interpreter.** If `pip install` succeeds but
> `import psycopg` fails, you installed into your *system* Python, not
> the venv. On Windows especially, `python` can resolve to a Microsoft
> Store shim. Verify with `python -c "import sys; print(sys.prefix)"` —
> it must point inside `.venv`. When in doubt call the venv's
> interpreter by full path: `./.venv/Scripts/python.exe`.

---

## 2. Configure environment variables

```bash
cp .env.example .env
```

Then edit `.env`. Full reference:

| Variable | Required | What it does |
|---|---|---|
| `PALIMPSEST_DSN` | **yes** | CockroachDB connection string |
| `AWS_REGION` | **yes** | Region for Bedrock (Titan embeddings) |
| `PALIMPSEST_EMBED_MODEL` | no | Defaults to `amazon.titan-embed-text-v2:0` |
| `PALIMPSEST_LLM_PROVIDER` | no | `bedrock` (default) or `anthropic_api` — see [§5](#5-configure-an-llm-provider) |
| `PALIMPSEST_ADJUDICATOR_MODEL` | no | Model ID; **shape depends on the provider** |
| `ANTHROPIC_API_KEY` | only if `anthropic_api` | Key from console.anthropic.com |
| `PALIMPSEST_GATE_ENABLED` | no | Defaults `true`. Only ever set `false` inside the controlled demo |
| `NEXT_PUBLIC_API_BASE_URL` | no | Console → API URL. Defaults `http://localhost:8000` |

> **Gotcha — quote values containing `&`.** A CockroachDB Cloud DSN often
> carries `?sslmode=verify-full&options=...`. Unquoted, `source .env` in
> bash treats `&` as a background-job operator and the variable silently
> ends up unset. Always quote:
> ```
> PALIMPSEST_DSN="postgresql://user:pass@host:26257/db?sslmode=verify-full"
> ```

> **`.env` is gitignored and must stay that way.** It holds a live
> database password and an API key. Confirm with `git check-ignore -v .env`.

---

## 3. Set up CockroachDB

### Quick path (local Docker)

Vector indexes need v25.2+, and `:latest` is not guaranteed to resolve to
a version that has them — pin the tag:

```bash
docker run -d --name palimpsest-crdb -p 26257:26257 -p 8080:8080 \
  cockroachdb/cockroach:latest-v25.2 start-single-node --insecure

docker exec palimpsest-crdb ./cockroach sql --insecure \
  -e "CREATE DATABASE IF NOT EXISTS palimpsest; SET CLUSTER SETTING feature.vector_index.enabled = true;"
```

Set in `.env`:
```
PALIMPSEST_DSN="postgresql://root@localhost:26257/palimpsest?sslmode=disable"
```

DB Console: <http://localhost:8080>.

### Full path (CockroachDB Cloud)

See [`../database/README.md`](../database/README.md) for the full walkthrough.
The condensed version:

1. Create a free cluster at <https://cockroachlabs.cloud/>.
2. **Connect** → *General connection string* → put it in `.env`.
3. Enable vector indexes (**before** applying the schema):
   ```sql
   SET CLUSTER SETTING feature.vector_index.enabled = true;
   ```
4. Set the GC TTL so `AS OF SYSTEM TIME` rewind can reach back:
   ```sql
   ALTER TABLE memories CONFIGURE ZONE USING gc.ttlseconds = 172800;
   ```
   Run this *after* the schema exists. Some tiers reject `CONFIGURE ZONE`;
   that's expected and Palimpsest falls back to `memory/ledger_replay.py`.

> **Gotcha — `root certificate file ... does not exist` on Windows.**
> `sslmode=verify-full` makes libpq look for a CA cert at a fixed path
> that doesn't exist by default. Download the cluster CA and put it
> exactly there:
> ```bash
> curl --create-dirs -o ~/.postgresql/root.crt \
>   "https://cockroachlabs.cloud/clusters/<CLUSTER-ID>/cert"
> ```
> On Windows, libpq reads `%APPDATA%\postgresql\root.crt` — copy it there too:
> ```bash
> mkdir -p "$APPDATA/postgresql" && cp ~/.postgresql/root.crt "$APPDATA/postgresql/root.crt"
> ```
> Do **not** "fix" this by downgrading to `sslmode=require` — that
> disables server-certificate verification.

### Apply the schema (both paths)

```bash
python database/migrate.py
```

Every statement is `CREATE ... IF NOT EXISTS`, so re-running is safe. It
prints a table/row-count summary so you can see the apply landed.

> Enable the vector-index cluster setting **before** this. Otherwise it
> fails with `FeatureNotSupported: vector indexes are not enabled`.

---

## 4. Configure AWS (embeddings)

Palimpsest uses **Amazon Titan Text Embeddings V2** for all embeddings.
This is required — there is no non-AWS embedding path in the repo.

```bash
aws configure          # or export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
aws sts get-caller-identity   # must succeed
```

Verify Bedrock reachability end-to-end:

```bash
python -m agent.bedrock_client
```

Expect a 1024-dim vector. A dimension mismatch is a hard failure by
design — it would silently corrupt every vector-index write.

> Titan needs **no** AWS Marketplace subscription (Amazon's own models
> aren't sold through Marketplace). Third-party models on Bedrock —
> including Anthropic's — do. See
> [`../infrastructure/README.md`](../infrastructure/README.md) →
> *"Why Titan works but Claude doesn't"*.

---

## 5. Configure an LLM provider

`chat()` and `adjudicate()` (triage verdicts, claim extraction,
contradiction tie-breaks) need a Claude model. Two supported providers,
selected by `PALIMPSEST_LLM_PROVIDER` — see [`../agent/llm.py`](../agent/llm.py).
Embeddings always stay on Bedrock either way.

### Option A — Bedrock (default)

```
PALIMPSEST_LLM_PROVIDER=bedrock
PALIMPSEST_ADJUDICATOR_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0
```

Note the `us.` prefix: newer Claude models on Bedrock reject on-demand
invocation by bare model ID and must be called through a cross-region
**inference profile**. List available ones:

```bash
aws bedrock list-inference-profiles --region us-east-1
```

### Option B — direct Anthropic API

```
PALIMPSEST_LLM_PROVIDER=anthropic_api
ANTHROPIC_API_KEY="sk-ant-..."
PALIMPSEST_ADJUDICATOR_MODEL=claude-haiku-4-5
```

Model IDs here are **bare first-party strings** — no `us.anthropic.`
prefix, no `:0` suffix. That naming is Bedrock-only and is a hard 404
against the direct API.

Verify:

```bash
python -m agent.anthropic_client
```

Requires a non-zero credit balance on the Anthropic account —
API billing is separate from any Claude.ai subscription, and an empty
balance returns `400 invalid_request_error` (not a 401), so a failure
here does *not* mean your key is wrong.

---

## 6. Run it

### Tests

```bash
pytest -q
```

19 tests, all against a **real** CockroachDB connection — the suite never
mocks the database. Expect ~5s locally, ~60s against Cloud (real network
round-trips).

### The demo

```bash
python -m demo.seed              # prints a workspace_id
python -m demo.attack_scenario   # the full 4-phase demo
```

See [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) for what each phase proves.

### API

```bash
uvicorn api.main:app --reload --port 8000
```

Sanity check: `curl http://127.0.0.1:8000/health`

### Console

```bash
cd console
npm install
npm run dev
```

Open <http://localhost:3000>, then **paste a `workspace_id` into the
field in the top-right corner**. Until you do, every view shows
"No workspace selected" — that's the expected empty state, not an error.
Get one from `python -m demo.seed`, or query:

```sql
SELECT workspace_id FROM workspaces ORDER BY created_at DESC LIMIT 1;
```

> If the API isn't on port 8000, point the console at it:
> ```bash
> NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8099 npm run dev
> ```

---

## 7. Deploy to AWS (optional)

Full walkthrough: [`../infrastructure/README.md`](../infrastructure/README.md).

```bash
cd infrastructure
python -m venv .venv && source .venv/Scripts/activate
pip install -r requirements.txt

cdk bootstrap        # one-time per account/region
cdk synth
cdk deploy -c llm_provider=anthropic_api
```

Then populate both Secrets Manager secrets and redeploy so the Lambdas
pick up the values (env vars resolve at deploy time, not per-invocation).

> Docker must be running — CDK bundles Lambda dependencies in a
> container matching the Lambda runtime.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: psycopg` | installed into system Python | activate the venv, or call `./.venv/Scripts/python.exe` |
| `KeyError: 'PALIMPSEST_DSN'` after `source .env` | unquoted `&` in the DSN | quote the whole value |
| `root certificate file ... does not exist` | libpq CA path unset | see the `root.crt` gotcha in [§3](#full-path-cockroachdb-cloud) |
| `SSL error: certificate verify failed` | wrong/stale CA cert | re-download `root.crt` for *your* cluster ID |
| `FeatureNotSupported: vector indexes are not enabled` | cluster setting off | `SET CLUSTER SETTING feature.vector_index.enabled = true;` then re-run migrate |
| `AS OF SYSTEM TIME` errors on rewind | GC TTL too short, or tier restricts `CONFIGURE ZONE` | raise `gc.ttlseconds`, or rely on the ledger-replay fallback |
| `AccessDeniedException ... INVALID_PAYMENT_INSTRUMENT` | Bedrock third-party Marketplace subscription | switch to `PALIMPSEST_LLM_PROVIDER=anthropic_api` |
| `400 ... credit balance is too low` | Anthropic API billing, not auth | add credits at console.anthropic.com |
| `ValidationException` on a Claude Bedrock call | bare model ID instead of inference profile | use the `us.`-prefixed profile ID |
| Console shows "No workspace selected" | no `workspace_id` entered | paste one into the top-right field |
| Console loads but no data | API not reachable at its base URL | set `NEXT_PUBLIC_API_BASE_URL` |
| `The token '&&' is not a valid statement separator` | PowerShell 5.1 | run the commands on separate lines; `&&` is bash-only |
| CDK `Another CLI is currently synthing to cdk.out` | concurrent synth | wait for it, or pass `--output` a different dir |
