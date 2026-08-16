"""
infrastructure/stacks/palimpsest_stack.py — the whole AWS side, kept
genuinely minimal per CONTEXT.md: this is Production Readiness scoring,
not a chance to over-engineer. CockroachDB itself is NOT provisioned
here — it's CockroachDB Cloud, managed separately per database/README.md.

Resources:
  - Secrets Manager secret for PALIMPSEST_DSN, injected into both Lambdas
    as an env var via a CloudFormation dynamic reference (secret.secret_value
    .unsafe_unwrap()) — CloudFormation resolves this at deploy time; the
    plaintext DSN never appears in CDK code or a --context value.
  - GateHandler Lambda: wraps api/main.py's FastAPI app via Mangum, exposed
    through a Function URL. Function URL chosen over API Gateway because
    it's faster to wire for a hackathon MVP and needs no separate resource
    to configure routes/stages for — API Gateway would be the natural next
    step for custom domains, request throttling, or WAF integration.
  - LedgerExportHandler Lambda: infrastructure/lambda/ledger_export/handler.py,
    on a 5-minute EventBridge schedule (see CONTEXT.md's cut list —
    EventBridge direct-call is fine for the MVP, no Step Functions).
  - S3 bucket with Object Lock enabled (governance mode) for the ledger
    export target. Object Lock can only be enabled at bucket CREATION
    time, never added after — that's why it's set here, in the bucket
    constructor, not as a later bucket-policy change.
  - IAM: Bedrock InvokeModel scoped to the specific model/inference-profile
    ARNs this project actually uses, never "bedrock:*".
  - CloudWatch log groups with explicit 7-day retention on both Lambdas,
    so this doesn't silently accumulate unbounded logs.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import BundlingOptions, CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]

EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
# Must match agent/bedrock_client.py's DEFAULT_CLAUDE_MODEL (or whatever
# PALIMPSEST_ADJUDICATOR_MODEL is set to at deploy time) -- every
# third-party model on Bedrock has its own separate AWS Marketplace
# subscription and its own IAM resource ARN, so this stack's IAM policy
# only grants InvokeModel on the specific model actually configured here.
# Deploying with a different PALIMPSEST_ADJUDICATOR_MODEL than this
# constant will get AccessDenied from IAM, not just from Marketplace.
CLAUDE_INFERENCE_PROFILE_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
CLAUDE_MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"

# The same model on the direct Anthropic API, used when
# PALIMPSEST_LLM_PROVIDER=anthropic_api. Bare first-party ID -- no
# "us.anthropic." prefix and no ":0" suffix; that naming is Bedrock-only,
# and sending it to the direct API is a hard 404.
ANTHROPIC_API_MODEL_ID = "claude-haiku-4-5"

VALID_LLM_PROVIDERS = ("bedrock", "anthropic_api")


def _ctx_bool(value, *, default: bool) -> bool:
    """
    CDK `-c key=value` context always arrives as a STRING, so a plain
    truthiness check would make `-c readonly=false` evaluate to True --
    exactly backwards, and silently. Parse explicitly and reject anything
    ambiguous rather than guessing.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    raise ValueError(f"expected a boolean-ish context value, got {value!r}")


class PalimpsestStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Which chat/adjudicate provider the deployed Lambda uses.
        #   cdk deploy                                  -> bedrock (default)
        #   cdk deploy -c llm_provider=anthropic_api    -> direct Anthropic API
        # Validated here rather than at Lambda cold start: a typo should fail
        # the synth, not produce a deployed function that 500s on first call.
        llm_provider = self.node.try_get_context("llm_provider") or "bedrock"
        if llm_provider not in VALID_LLM_PROVIDERS:
            raise ValueError(
                f"llm_provider context value {llm_provider!r} is not one of {VALID_LLM_PROVIDERS}"
            )

        # `-c public_url=true`  -> Function URL auth NONE (publicly clickable)
        # `-c readonly=true`    -> api/main.py blocks destructive/metered routes
        public_url = _ctx_bool(self.node.try_get_context("public_url"), default=False)
        readonly = _ctx_bool(self.node.try_get_context("readonly"), default=False)

        # Safety interlock. A publicly-reachable Function URL in front of an API
        # with no authentication of its own means anyone who finds the URL can
        # revoke beliefs and drive real, metered LLM calls. Making that
        # combination impossible to deploy by accident is worth more than the
        # flexibility of allowing it -- fail the synth, not production.
        if public_url and not readonly:
            raise ValueError(
                "Refusing to synth: public_url=true without readonly=true would expose an "
                "unauthenticated API that can destroy belief state and spend real LLM credit. "
                "Deploy with `-c public_url=true -c readonly=true`, or keep the default "
                "AWS_IAM-authenticated URL."
            )

        # ---------------------------------------------------------------
        # Secret: PALIMPSEST_DSN
        # ---------------------------------------------------------------
        dsn_secret = secretsmanager.Secret(
            self,
            "PalimpsestDsn",
            description=(
                "CockroachDB connection string for Palimpsest. Populate the secret VALUE "
                "manually after `cdk deploy` — see infrastructure/README.md step 2. "
                "CDK creates the secret container, not its value."
            ),
        )

        # ---------------------------------------------------------------
        # Secret: ANTHROPIC_API_KEY
        #
        # Only read when PALIMPSEST_LLM_PROVIDER=anthropic_api (see
        # agent/llm.py). It exists unconditionally because the Lambda's
        # env vars are resolved at DEPLOY time via a CloudFormation
        # dynamic reference -- having the container already present means
        # switching providers later is a value-set plus redeploy, not a
        # stack change. Same rule as the DSN secret: CDK creates the
        # container, never the value.
        # ---------------------------------------------------------------
        anthropic_key_secret = secretsmanager.Secret(
            self,
            "PalimpsestAnthropicApiKey",
            description=(
                "Anthropic API key (console.anthropic.com), used only when "
                "PALIMPSEST_LLM_PROVIDER=anthropic_api. Populate the secret VALUE manually "
                "after `cdk deploy` — see infrastructure/README.md. CDK creates the secret "
                "container, not its value."
            ),
        )

        # ---------------------------------------------------------------
        # S3 bucket for tamper-evident ledger export
        # ---------------------------------------------------------------
        ledger_bucket = s3.Bucket(
            self,
            "LedgerExportBucket",
            object_lock_enabled=True,  # only settable at creation — see module docstring
            object_lock_default_retention=s3.ObjectLockRetention.governance(Duration.days(2555)),
            versioned=True,  # required by S3 for Object Lock
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,  # never auto-delete an audit trail
        )

        # ---------------------------------------------------------------
        # IAM: Bedrock access scoped to exactly the models this project uses
        # ---------------------------------------------------------------
        bedrock_policy = iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=[
                f"arn:aws:bedrock:{self.region}::foundation-model/{EMBED_MODEL_ID}",
                f"arn:aws:bedrock:{self.region}::foundation-model/{CLAUDE_MODEL_ID}",
                f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{CLAUDE_INFERENCE_PROFILE_ID}",
            ],
        )

        # ---------------------------------------------------------------
        # GateHandler: api/main.py's FastAPI app, via Mangum, behind a
        # Function URL. See infrastructure/lambda/gate_handler/handler.py.
        # ---------------------------------------------------------------
        gate_log_group = logs.LogGroup(
            self,
            "GateHandlerLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        gate_handler = _lambda.Function(
            self,
            "GateHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            architecture=_lambda.Architecture.X86_64,
            handler="handler.handler",
            code=_lambda.Code.from_asset(
                str(REPO_ROOT),
                bundling=BundlingOptions(
                    image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        " && ".join(
                            [
                                "pip install --no-cache-dir -r requirements.txt --target /asset-output",
                                "cp -r memory agent api /asset-output/",
                                "cp infrastructure/lambda/gate_handler/handler.py /asset-output/handler.py",
                            ]
                        ),
                    ],
                ),
            ),
            timeout=Duration.seconds(30),
            memory_size=512,
            environment={
                "PALIMPSEST_DSN": dsn_secret.secret_value.unsafe_unwrap(),
                "PALIMPSEST_EMBED_MODEL": EMBED_MODEL_ID,
                # Which provider serves chat()/adjudicate() -- see agent/llm.py.
                # Set explicitly rather than relying on agent/llm.py's default, so
                # the deployed Lambda's provider is visible in the template instead
                # of being implied by code. Override at deploy time with
                # `cdk deploy -c llm_provider=anthropic_api`.
                "PALIMPSEST_LLM_PROVIDER": llm_provider,
                # The adjudicator model ID is provider-specific: a Bedrock
                # cross-region inference profile ID for provider=bedrock, or a bare
                # first-party ID (e.g. claude-haiku-4-5) for provider=anthropic_api.
                # Sending the wrong shape is a hard failure at call time, not a
                # warning, so it's derived from the provider rather than hardcoded.
                "PALIMPSEST_ADJUDICATOR_MODEL": (
                    CLAUDE_INFERENCE_PROFILE_ID if llm_provider == "bedrock" else ANTHROPIC_API_MODEL_ID
                ),
                "ANTHROPIC_API_KEY": anthropic_key_secret.secret_value.unsafe_unwrap(),
                # See api/main.py's ReadOnlyMiddleware. The Function URL below
                # has no authentication when public_url=true, and this API has
                # no auth layer of its own, so the public deployment disables
                # the routes that destroy belief state or spend real LLM credit.
                "PALIMPSEST_READONLY": "true" if readonly else "false",
            },
            log_group=gate_log_group,
        )
        gate_handler.add_to_role_policy(bedrock_policy)

        # AWS_IAM by default: every request must be SigV4-signed, so an
        # unauthenticated visitor gets 403. `-c public_url=true` opens it for a
        # publicly-clickable demo link -- which is only safe together with
        # readonly (enforced below), because nothing else authenticates this API.
        gate_url = gate_handler.add_function_url(
            auth_type=(
                _lambda.FunctionUrlAuthType.NONE if public_url else _lambda.FunctionUrlAuthType.AWS_IAM
            ),
            cors=_lambda.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[_lambda.HttpMethod.ALL],
                allowed_headers=["*"],
            ),
        )

        # ---------------------------------------------------------------
        # LedgerExportHandler: on a 5-minute EventBridge schedule
        # ---------------------------------------------------------------
        export_log_group = logs.LogGroup(
            self,
            "LedgerExportLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        ledger_export_handler = _lambda.Function(
            self,
            "LedgerExportHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            architecture=_lambda.Architecture.X86_64,
            handler="handler.handler",
            code=_lambda.Code.from_asset(
                str(REPO_ROOT / "infrastructure" / "lambda" / "ledger_export"),
                bundling=BundlingOptions(
                    image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install --no-cache-dir 'psycopg[binary]>=3.1,<4' --target /asset-output "
                        "&& cp handler.py /asset-output/handler.py",
                    ],
                ),
            ),
            timeout=Duration.minutes(5),
            memory_size=256,
            environment={
                "PALIMPSEST_DSN": dsn_secret.secret_value.unsafe_unwrap(),
                "PALIMPSEST_LEDGER_BUCKET": ledger_bucket.bucket_name,
            },
            log_group=export_log_group,
        )
        ledger_bucket.grant_write(ledger_export_handler)

        events.Rule(
            self,
            "LedgerExportSchedule",
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[targets.LambdaFunction(ledger_export_handler)],
        )

        self.gate_function_url = gate_url.url
        self.ledger_bucket_name = ledger_bucket.bucket_name
        self.dsn_secret_arn = dsn_secret.secret_arn

        # ---------------------------------------------------------------
        # Stack outputs
        #
        # These are what `cdk deploy --outputs-file` writes and what the
        # console needs (NEXT_PUBLIC_API_BASE_URL). Without explicit
        # CfnOutputs the outputs file is just `{}` -- the Python attributes
        # above are only visible inside the CDK app, never in CloudFormation.
        # ---------------------------------------------------------------
        CfnOutput(self, "GateFunctionUrl", value=gate_url.url, description="GateHandler HTTP endpoint")
        CfnOutput(
            self,
            "GateFunctionUrlAuth",
            value="NONE (public)" if public_url else "AWS_IAM (SigV4 required)",
            description="Auth mode on the Function URL",
        )
        CfnOutput(
            self,
            "ApiWriteMode",
            value="READ-ONLY (destructive + metered routes blocked)" if readonly else "READ-WRITE",
            description="Whether api/main.py's ReadOnlyMiddleware is active",
        )
        CfnOutput(self, "LlmProvider", value=llm_provider, description="Backs chat()/adjudicate()")
        # NB: not "LedgerExportBucket" -- CfnOutput IDs share the construct-ID
        # namespace with every other construct in the stack, and the S3 bucket
        # already owns that name.
        CfnOutput(
            self,
            "LedgerExportBucketName",
            value=ledger_bucket.bucket_name,
            description="Object Lock ledger export target",
        )
        CfnOutput(self, "DsnSecretArn", value=dsn_secret.secret_arn, description="Set this secret's VALUE after deploy")
        CfnOutput(
            self,
            "AnthropicApiKeySecretArn",
            value=anthropic_key_secret.secret_arn,
            description="Set this secret's VALUE after deploy (only used when llm_provider=anthropic_api)",
        )
