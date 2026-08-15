"""
agent/adjudication.py — the equal-integrity tie-break prompt and response
parsing, shared by agent/bedrock_client.py and agent/anthropic_client.py so
the adjudication prompt and JSON-parsing logic live in exactly one place
regardless of which provider's chat() actually calls the model.
"""

from __future__ import annotations

import json
import re
from typing import Callable

ChatFn = Callable[..., str]  # chat_fn(system=..., messages=..., max_tokens=...) -> str

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM_PROMPT = (
    "You are adjudicating a contradiction between two equal-authority beliefs "
    "in a security agent's memory store. Both claims are about the same subject "
    "and predicate but assert different, incompatible values. Decide which one "
    "should remain the trusted belief. Respond with ONLY a JSON object, no other "
    'text: {"winner": "incumbent" or "challenger", "rationale": "<one sentence>"}'
)


def run_adjudication(chat_fn: ChatFn, incumbent: dict, challenger: dict, adjudicator_label: str) -> dict:
    """
    Runs the equal-integrity tie-break adjudication via the given chat_fn and
    returns a dict matching memory.gate.AdjudicateFn's contract exactly:
    incumbent/challenger are the small dicts memory/gate.py builds (memory_id,
    object_value, polarity, integrity_level).
    """
    user_prompt = (
        f"Incumbent belief (currently active): {json.dumps(incumbent)}\n"
        f"Challenger belief (just submitted): {json.dumps(challenger)}\n\n"
        "Which should hold? Consider plausibility and specificity, not just recency."
    )
    raw = chat_fn(system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": user_prompt}], max_tokens=256)

    match = _JSON_OBJECT_RE.search(raw)
    if not match:
        raise RuntimeError(f"adjudicator did not return a parseable JSON object: {raw!r}")
    decision = json.loads(match.group(0))
    if decision.get("winner") not in ("incumbent", "challenger"):
        raise RuntimeError(f"adjudicator returned an invalid winner: {decision!r}")

    return {
        "winner": decision["winner"],
        "rationale": decision.get("rationale", "(no rationale returned)"),
        "adjudicator": adjudicator_label,
    }
