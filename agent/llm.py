"""
agent/llm.py — selects which chat()/adjudicate() provider is live. Callers
that need chat/adjudicate (demo/attack_scenario.py, api/deps.py,
api/routes/rewind.py) import from here instead of hardcoding
agent.bedrock_client, so the provider is a one-line env-var switch instead
of a per-call-site edit.

    PALIMPSEST_LLM_PROVIDER=bedrock        (default) Claude via AWS Bedrock
    PALIMPSEST_LLM_PROVIDER=anthropic_api  direct Anthropic API (console.anthropic.com)

embed() always comes from agent.bedrock_client (Titan Text Embeddings V2
via Bedrock) regardless of this setting -- embeddings were never blocked,
so there's nothing to swap there. See agent/anthropic_client.py's module
docstring for why this switch exists.
"""

from __future__ import annotations

import os

_provider = os.environ.get("PALIMPSEST_LLM_PROVIDER", "bedrock").strip().lower()

if _provider == "anthropic_api":
    from agent.anthropic_client import adjudicate, chat
elif _provider == "bedrock":
    from agent.bedrock_client import adjudicate, chat
else:
    raise RuntimeError(
        f"unknown PALIMPSEST_LLM_PROVIDER={_provider!r}; expected 'bedrock' or 'anthropic_api'"
    )

# Embeddings used to be hard-wired to Bedrock here, on the reasoning that
# Titan had never been the thing that broke. That held until the AWS account
# behind this project was suspended outright (2026-08-18), at which point
# embed() failing took admit(), retrieve(), the attack scenario, the
# benchmark and the demo down with it -- while chat(), which HAD a provider
# switch, carried on untouched against the direct Anthropic API.
#
# The lesson is the switch, not the vendor. Embeddings now have one too:
#
#     PALIMPSEST_EMBED_PROVIDER=bedrock  (default) Titan Text Embeddings V2
#     PALIMPSEST_EMBED_PROVIDER=local    agent/local_embeddings.py
#
# The local provider is lexical, not semantic -- read its module docstring
# before relying on it, because paraphrase matching genuinely degrades. It
# is a way to keep the system demonstrable without any cloud account, not a
# claim of parity with Titan.
_embed_provider = os.environ.get("PALIMPSEST_EMBED_PROVIDER", "bedrock").strip().lower()

if _embed_provider == "local":
    from agent.local_embeddings import embed
elif _embed_provider == "bedrock":
    from agent.bedrock_client import embed
else:
    raise RuntimeError(
        f"unknown PALIMPSEST_EMBED_PROVIDER={_embed_provider!r}; expected 'bedrock' or 'local'"
    )

__all__ = ["chat", "adjudicate", "embed"]
