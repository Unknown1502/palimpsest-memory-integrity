"""
agent/bedrock_client.py — the only module in this repo that talks to
Bedrock. memory/gate.py has zero AWS dependency by design (see its module
docstring); this module supplies the embed_fn/adjudicate_fn callables that
agent/ingest.py and agent/triage.py wire into MemoryGate calls.

Run as a script for a live connectivity smoke test:

    python -m agent.bedrock_client

Confirms credentials/region/model access for both models this project
needs (Titan Text Embeddings V2, Claude via the Messages API) before
anything else gets built on top of them, and hard-fails loudly if the
embedding dimension doesn't match memory.gate.EMBED_DIMS — a silent
mismatch there would corrupt every vector index write.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import boto3

from agent.adjudication import run_adjudication
from memory.gate import EMBED_DIMS

DEFAULT_REGION = "us-east-1"
DEFAULT_EMBED_MODEL = "amazon.titan-embed-text-v2:0"
DEFAULT_CLAUDE_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
# Newer Claude models on Bedrock reject on-demand invocation by bare model
# ID — confirmed empirically (ValidationException: "Invocation of model ID
# ... with on-demand throughput isn't supported"). They must be invoked via
# a cross-region inference profile ID instead (the "us." prefix above),
# discoverable via `aws bedrock list-inference-profiles`.
#
# Haiku 4.5, not Sonnet 4.5: every third-party model on Bedrock has its OWN
# separate AWS Marketplace subscription. Sonnet 4.5's subscription attempt
# failed once with INVALID_PAYMENT_INSTRUMENT before a payment method was
# added to the account, and kept failing on retry afterward too — that
# specific model's subscription got stuck, not the account's ability to
# subscribe to anything. Haiku 4.5 (never previously attempted) subscribed
# cleanly on the first real call once the card was on file. It's also
# well-suited to this project's actual tasks (structured JSON extraction,
# verdict classification, adjudication — not open-ended reasoning) and
# meaningfully cheaper, which matters against a capped, expiring credit
# balance. Sonnet 4.5 can be re-tried later by overriding
# PALIMPSEST_ADJUDICATOR_MODEL once/if its own subscription clears.

_client = None


def get_client():
    global _client
    if _client is None:
        region = os.environ.get("AWS_REGION", DEFAULT_REGION)
        _client = boto3.client("bedrock-runtime", region_name=region)
    return _client


def embed(text: str) -> list[float]:
    """Embed one string via Titan Text Embeddings V2. Returns EMBED_DIMS floats."""
    model_id = os.environ.get("PALIMPSEST_EMBED_MODEL", DEFAULT_EMBED_MODEL)
    response = get_client().invoke_model(
        modelId=model_id,
        body=json.dumps({"inputText": text, "dimensions": EMBED_DIMS, "normalize": True}),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())
    vector = payload["embedding"]
    if len(vector) != EMBED_DIMS:
        # Hard-fail loudly, per CONTEXT.md: a silent dimension mismatch
        # would corrupt every vector index write from this point on.
        raise RuntimeError(
            f"Titan returned {len(vector)}-dim embedding, memory.gate.EMBED_DIMS expects {EMBED_DIMS}. "
            f"Check PALIMPSEST_EMBED_MODEL ({model_id}) matches the dimension configured in database/schema.sql."
        )
    return vector


def chat(system: str, messages: list[dict], max_tokens: int = 1024) -> str:
    """
    Call Claude via Bedrock's invoke_model using the Anthropic Messages API
    body format. `messages` is the standard Messages API list of
    {"role": "user"|"assistant", "content": str} dicts.
    """
    model_id = os.environ.get("PALIMPSEST_ADJUDICATOR_MODEL", DEFAULT_CLAUDE_MODEL)
    response = get_client().invoke_model(
        modelId=model_id,
        body=json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages,
            }
        ),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())
    return "".join(block["text"] for block in payload["content"] if block["type"] == "text")


def adjudicate(incumbent: dict, challenger: dict) -> dict:
    """
    Bedrock-backed tie-break adjudicator, matching memory.gate.AdjudicateFn's
    signature exactly: incumbent/challenger are the small dicts memory/gate.py
    builds (memory_id, object_value, polarity, integrity_level). Both sides
    are equal integrity — the model is the arbiter, not a formula. Prompt
    and response parsing live in agent/adjudication.py, shared with
    agent/anthropic_client.py's adjudicate() so both providers stay in sync.
    """
    model_id = os.environ.get("PALIMPSEST_ADJUDICATOR_MODEL", DEFAULT_CLAUDE_MODEL)
    return run_adjudication(chat, incumbent, challenger, adjudicator_label=f"bedrock:{model_id}")


def smoke_test() -> None:
    region = os.environ.get("AWS_REGION", DEFAULT_REGION)
    embed_model = os.environ.get("PALIMPSEST_EMBED_MODEL", DEFAULT_EMBED_MODEL)
    claude_model = os.environ.get("PALIMPSEST_ADJUDICATOR_MODEL", DEFAULT_CLAUDE_MODEL)

    print(f"Region: {region}")
    print(f"Embed model: {embed_model}")
    print(f"Claude model: {claude_model}")
    print()

    print("Embedding a test string via Titan Text Embeddings V2 ...")
    vector = embed("10.0.0.7 is the internal vulnerability scanner")
    print(f"  Got {len(vector)}-dim vector. First 5 values: {[round(v, 5) for v in vector[:5]]}")
    if len(vector) != EMBED_DIMS:
        raise SystemExit(f"FAIL: dimension mismatch ({len(vector)} != {EMBED_DIMS})")
    print(f"  OK — matches memory.gate.EMBED_DIMS ({EMBED_DIMS}).")
    print()

    print("Calling Claude via the Messages API with a trivial prompt ...")
    reply = chat(
        system="Reply with exactly one word.",
        messages=[{"role": "user", "content": "Say 'ok' and nothing else."}],
        max_tokens=16,
    )
    print(f"  Claude replied: {reply.strip()!r}")
    print()
    print("Bedrock smoke test PASSED.")


if __name__ == "__main__":
    smoke_test()
