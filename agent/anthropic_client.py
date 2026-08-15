"""
agent/anthropic_client.py — direct Anthropic API (console.anthropic.com)
chat()/adjudicate(), used in place of agent/bedrock_client.py's
Bedrock-backed versions when PALIMPSEST_LLM_PROVIDER=anthropic_api (see
agent/llm.py). Exists because Claude-via-Bedrock is blocked on this AWS
account by a Marketplace billing issue (see agent/bedrock_client.py's
module docstring for the full history) — this is the pragmatic swap that
keeps the project's chat/adjudication calls real and live while that gets
sorted out separately.

embed() is UNTOUCHED by this swap and still comes from
agent/bedrock_client.py — Titan Text Embeddings V2 via Bedrock was never
blocked, and keeping it means the "at least one AWS service" hackathon
requirement stays satisfied (Titan + Lambda + S3 Object Lock) independent
of which provider serves chat().

Requires ANTHROPIC_API_KEY in the environment (never committed — see
.env.example). The `anthropic` SDK reads it automatically; nothing here
ever touches the key directly.

Run as a script for a live connectivity smoke test:

    python -m agent.anthropic_client
"""

from __future__ import annotations

import os

import anthropic

from agent.adjudication import run_adjudication

DEFAULT_MODEL = "claude-haiku-4-5"
# Bare first-party model ID (no "us.anthropic." prefix, no ":0" suffix --
# that naming is Bedrock-specific). Haiku 4.5 for the same reason
# agent/bedrock_client.py defaults to it: this project's tasks are
# structured extraction/classification, not open-ended reasoning, and it's
# the cheapest tier against a capped, expiring credit balance.

_client = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _client


def chat(system: str, messages: list[dict], max_tokens: int = 1024) -> str:
    """
    Call Claude via the direct Anthropic Messages API. `messages` is the
    standard Messages API list of {"role": "user"|"assistant", "content": str}
    dicts -- same shape agent/bedrock_client.py's chat() takes, so callers
    (agent/triage.py, agent/ingest.py) need zero changes to switch providers.
    """
    model = os.environ.get("PALIMPSEST_ADJUDICATOR_MODEL", DEFAULT_MODEL)
    response = get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    return "".join(block.text for block in response.content if block.type == "text")


def adjudicate(incumbent: dict, challenger: dict) -> dict:
    """
    Direct-Anthropic-API-backed tie-break adjudicator, matching
    memory.gate.AdjudicateFn's signature exactly -- same contract as
    agent/bedrock_client.py's adjudicate().
    """
    model = os.environ.get("PALIMPSEST_ADJUDICATOR_MODEL", DEFAULT_MODEL)
    return run_adjudication(chat, incumbent, challenger, adjudicator_label=f"anthropic-api:{model}")


def smoke_test() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "FAIL: ANTHROPIC_API_KEY is not set. Set it in your shell (never commit it) "
            "and re-run: python -m agent.anthropic_client"
        )

    model = os.environ.get("PALIMPSEST_ADJUDICATOR_MODEL", DEFAULT_MODEL)
    print(f"Claude model (direct Anthropic API): {model}")
    print()

    print("Calling Claude via the direct Anthropic Messages API with a trivial prompt ...")
    reply = chat(
        system="Reply with exactly one word.",
        messages=[{"role": "user", "content": "Say 'ok' and nothing else."}],
        max_tokens=16,
    )
    print(f"  Claude replied: {reply.strip()!r}")
    print()

    print("Running a real adjudication call ...")
    decision = adjudicate(
        incumbent={
            "memory_id": "test-incumbent",
            "object_value": "internal_vuln_scanner",
            "polarity": "positive",
            "integrity_level": 3,
        },
        challenger={
            "memory_id": "test-challenger",
            "object_value": "decommissioned_scanner",
            "polarity": "positive",
            "integrity_level": 3,
        },
    )
    print(f"  winner={decision['winner']!r} adjudicator={decision['adjudicator']!r}")
    print(f"  rationale: {decision['rationale']}")
    print()
    print("Anthropic direct-API smoke test PASSED.")


if __name__ == "__main__":
    smoke_test()
