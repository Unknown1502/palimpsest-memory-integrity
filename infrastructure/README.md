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
- A Secrets Manager secret container for `PALIMPSEST_DSN` (CDK creates
  the secret, not its value — you set that after deploy, see step 2
  below).
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

Then update the two Lambdas to pick up the new value (env vars are
resolved from the secret at **deploy** time via a CloudFormation dynamic
reference, not fetched fresh per-invocation — so after changing the
secret's value you need to redeploy for the Lambdas to see it):

```bash
cdk deploy
```

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
