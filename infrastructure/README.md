# Infrastructure (AWS CDK)

This stack is genuinely minimal on purpose — see CONTEXT.md's cut list.
**It does not provision CockroachDB.** CockroachDB Cloud is managed
separately; see [`../database/README.md`](../database/README.md) to
create the cluster and get its connection string first.

## What this deploys

- A Lambda (`GateHandler`) running the exact same FastAPI app as
  `api/main.py`, via [Mangum](https://mangum.io/), behind a **Function
  URL** (not API Gateway — see the tradeoff note in
  `infrastructure/stacks/palimpsest_stack.py`'s docstring).
- A Lambda (`LedgerExportHandler`) that exports unexported
  `memory_ledger` rows to S3, on a 5-minute EventBridge schedule.
- An S3 bucket with **Object Lock enabled** (governance mode, ~7 year
  default retention) for that export — Object Lock can only be turned on
  at bucket creation, which is why it's set in the CDK bucket
  constructor and can't be retrofitted later.
- Two Secrets Manager secret containers — `PALIMPSEST_DSN` and
  `PALIMPSEST_ANTHROPIC_API_KEY` (CDK creates the secrets, not their
  values — you set those after deploy, see step 2 below).
- IAM scoped to `bedrock:InvokeModel` on the specific Titan/Claude
  model and inference-profile ARNs this project actually calls — never
  `bedrock:*`.
- CloudWatch log groups on both Lambdas with explicit 7-day retention.

## Prerequisites

- Docker running locally (CDK bundles each Lambda's Python dependencies
  inside a container matching the Lambda runtime — this is standard CDK
  asset bundling, not something specific to this stack).
- AWS credentials configured (`aws sts get-caller-identity` should work).
- Node.js + the CDK CLI: `npm install -g aws-cdk`.
- If this AWS account/region has never used CDK before:
  `cdk bootstrap` (one-time per account/region).

## Deploy steps

```bash
cd infrastructure
python -m venv .venv
.venv/Scripts/activate   # or: source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

cdk synth                # sanity check — see it output a template with no errors
cdk deploy                # creates real AWS resources; review the diff it shows first
```

### Choosing the chat/adjudicate provider

`PALIMPSEST_LLM_PROVIDER` (see [`../agent/llm.py`](../agent/llm.py))
decides whether `chat()`/`adjudicate()` go to Bedrock or to the direct
Anthropic API. It defaults to `bedrock`; override at deploy time:

```bash
cdk deploy -c llm_provider=anthropic_api
```

The value is validated at synth time — a typo fails the synth rather than
producing a deployed Lambda that 500s on its first adjudication. The
adjudicator model ID is derived from it automatically (a Bedrock
cross-region inference profile ID vs. a bare first-party ID), because
sending the wrong shape is a hard failure at call time.

**On this account, deploy with `-c llm_provider=anthropic_api`.** Claude
on Bedrock is blocked here — see "Why Titan works but Claude doesn't"
below. Embeddings are unaffected and stay on Bedrock either way.

After deploy, `cdk deploy`'s output includes the `GateHandler` Function
URL and the `LedgerExportBucket` name — note both.

### Step 2 — populate the DSN secret

CDK creates the Secrets Manager secret *container* but not its value
(never put a real connection string in CDK code or a `--context` flag).
Set it after deploy:

```bash
aws secretsmanager put-secret-value \
  --secret-id <PalimpsestDsn-secret-arn-from-deploy-output> \
  --secret-string "postgresql://<user>:<password>@<host>:26257/<database>?sslmode=verify-full"
```

If deploying with `-c llm_provider=anthropic_api`, also set the Anthropic
API key secret (get a key from console.anthropic.com):

```bash
aws secretsmanager put-secret-value \
  --secret-id <PalimpsestAnthropicApiKey-secret-arn-from-deploy-output> \
  --secret-string "sk-ant-..."
```

Then update the two Lambdas to pick up the new value (env vars are
resolved from the secret at **deploy** time via a CloudFormation dynamic
reference, not fetched fresh per-invocation — so after changing a
secret's value you need to redeploy for the Lambdas to see it):

```bash
cdk deploy -c llm_provider=anthropic_api
```

## Why Titan works but Claude doesn't (on this AWS account)

Bedrock is not uniformly blocked here — this distinction is load-bearing
and worth stating precisely, because "Bedrock is broken" is wrong:

- **Amazon Titan Text Embeddings V2 — works.** Verified live. Amazon's
  own models are **not sold through AWS Marketplace** and have no product
  ID, so they need no Marketplace subscription at all.
- **Anthropic Claude on Bedrock — blocked.** Third-party models *are*
  sold through AWS Marketplace, so invoking one auto-initiates a
  Marketplace subscription. On this account that subscription cannot
  complete: `AccessDeniedException ... INVALID_PAYMENT_INSTRUMENT`.

Diagnosed down to the exact failing step with the Bedrock model-access
API (not just the console, which gave no usable error):

```bash
aws bedrock get-foundation-model-availability \
  --model-id anthropic.claude-haiku-4-5-20251001-v1:0 --region us-east-1
# authorizationStatus:      AUTHORIZED   <- IAM is fine
# entitlementAvailability:  AVAILABLE    <- entitlement is fine
# regionAvailability:       AVAILABLE    <- region is fine
# agreementAvailability:    NOT_AVAILABLE  <- the only blocker
```

`create-foundation-model-agreement` (with an `offerToken` from
`list-foundation-model-agreement-offers`) does move it
`NOT_AVAILABLE -> PENDING`, further than the console ever got — but it
never reaches `AVAILABLE`, and `InvokeModel` still returns
`INVALID_PAYMENT_INSTRUMENT`. The blocker is AWS Marketplace payment-
instrument validation at the account level, not IAM, not the region, and
not the API path used to request access. Nothing in this repo can fix it.

Hence the split: embeddings on Bedrock (real AWS, no Marketplace
dependency), chat/adjudication on the direct Anthropic API. The AWS
footprint is still Bedrock + Lambda + S3 Object Lock + Secrets Manager +
EventBridge + CloudWatch Logs.

## What this stack does NOT cover, and why (per CONTEXT.md's cut list)

- **Step Functions orchestration for rewind** — not included. `POST
  /rewind/apply` calls the replay logic directly and synchronously from
  the API, per the cut list.
- **`ccloud` backup automation** — scripted and documented in
  `database/README.md`, not wired into this stack or triggered by it.
- **EventBridge for anything beyond the ledger export schedule** — the
  only scheduled/event-driven piece here is `LedgerExportSchedule`.
  Nothing else in this system is event-driven at the infrastructure
  level; `GateHandler` is a synchronous request/response Lambda.
- **Multi-region** — this stack deploys to a single AWS region (whatever
  your CDK environment / `AWS_REGION` resolves to). `REGIONAL BY ROW` on
  the CockroachDB side is documented as roadmap-only in CONTEXT.md, and
  nothing here assumes multi-region.
- **KMS-signed ledger entries** — the ledger's SHA-256 hash chain
  (verified via `GET /ledger/verify`) is the tamper-evidence mechanism;
  no additional KMS signing layer is deployed.
- **API Gateway / custom domain / WAF** — the Function URL is the entire
  HTTP surface for `GateHandler`. Adding API Gateway in front of it would
  be the natural next step for a custom domain, request throttling, or
  WAF rules, but isn't needed for the hackathon deployment.

## Cleanup

```bash
cdk destroy
```

Note: the `LedgerExportBucket` has `RemovalPolicy.RETAIN` (an audit trail
shouldn't auto-delete with the rest of the stack) — after `cdk destroy`,
delete it manually via the console or `aws s3 rb --force` once you've
confirmed you don't need the exported ledger data.
