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

from agent.bedrock_client import embed  # always Bedrock/Titan -- see module docstring

__all__ = ["chat", "adjudicate", "embed"]
