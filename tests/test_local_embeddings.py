"""
tests/test_local_embeddings.py — the offline embedding fallback.

This provider exists so the system stays demonstrable when the cloud
account behind it is gone (which is not hypothetical -- see the module
docstring). What it has to guarantee is narrow but load-bearing: vectors of
the right shape, deterministic, and ranking the RIGHT belief first for the
demo corpus. It does not have to be good at paraphrase, and a test
asserting it were would be asserting a property it does not have.
"""

from __future__ import annotations

import math

import pytest

from agent.local_embeddings import embed
from memory.gate import EMBED_DIMS

SEED_CLAIM = "ip:10.0.0.7 classification internal_vuln_scanner"
PLANTED_CLAIM = "ip:203.0.113.9 classification approved security scanner"
ATTACK_ALERT = (
    "Alert INC-4821: source_ip=203.0.113.9 dest_host=internal-db-01 "
    "signature=CVE-2024-XXXXX remote code execution attempt"
)
BENIGN_ALERT = (
    "Alert AL-7781: source_ip=10.0.0.7 dest_host=internal-db-01 signature=generic port-scan"
)


def _cos(a: list[float], b: list[float]) -> float:
    """Both vectors are L2-normalized, so the dot product IS cosine."""
    return sum(x * y for x, y in zip(a, b))


def test_shape_matches_what_the_gate_requires():
    """A dimension mismatch would corrupt every vector index write."""
    v = embed("anything at all")
    assert len(v) == EMBED_DIMS


def test_vectors_are_unit_length():
    for text in (SEED_CLAIM, PLANTED_CLAIM, ATTACK_ALERT, "x", "a b c d e f g"):
        assert math.isclose(math.sqrt(sum(x * x for x in embed(text))), 1.0, rel_tol=1e-9)


def test_deterministic_across_calls():
    """No model file, no network, no randomness — same text, same vector."""
    assert embed(ATTACK_ALERT) == embed(ATTACK_ALERT)


def test_degenerate_input_does_not_produce_a_zero_vector():
    """
    memory/gate.py raises on a zero vector. Empty or punctuation-only text
    must fail here in an obvious way, or not at all — never deep inside
    admit() with an error about normalization.
    """
    for text in ("", "   ", "!!!", "\n\t"):
        v = embed(text)
        assert len(v) == EMBED_DIMS
        assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)


def test_rejects_non_string_input():
    with pytest.raises(TypeError):
        embed(None)  # type: ignore[arg-type]


def test_the_right_belief_wins_each_demo_query():
    """
    The whole attack scenario turns on this: an alert from 203.0.113.9 must
    rank the belief ABOUT 203.0.113.9 above the unrelated seeded belief, and
    vice versa. If this inverts, the demo silently stops demonstrating
    anything.
    """
    seed, planted = embed(SEED_CLAIM), embed(PLANTED_CLAIM)

    attack = embed(ATTACK_ALERT)
    assert _cos(planted, attack) > _cos(seed, attack)

    benign = embed(BENIGN_ALERT)
    assert _cos(seed, benign) > _cos(planted, benign)


def test_shared_identifiers_beat_unrelated_text_by_a_clear_margin():
    """
    A thin margin ranks correctly on one machine and may not on the next
    phrasing. Require real separation, not a coin flip.
    """
    claim = embed(PLANTED_CLAIM)
    related = _cos(claim, embed(ATTACK_ALERT))
    unrelated = _cos(claim, embed("quarterly revenue projections for the northeast region"))

    assert related > unrelated
    assert related - unrelated > 0.05, f"margin too thin: {related:.4f} vs {unrelated:.4f}"


def test_ip_matches_even_when_written_without_the_subject_prefix():
    """
    Claims store `ip:203.0.113.9`; alerts write `source_ip=203.0.113.9`. The
    piecewise tokenization is what bridges those, so this is the specific
    behavior that makes retrieval work on real inputs.
    """
    prefixed = embed("ip:203.0.113.9 classification scanner")
    bare = embed("traffic observed from 203.0.113.9 today")
    other = embed("traffic observed from 198.51.100.22 today")

    assert _cos(prefixed, bare) > _cos(prefixed, other)


def test_provider_switch_selects_this_module(monkeypatch):
    """PALIMPSEST_EMBED_PROVIDER=local must actually route here, with no AWS import path."""
    import importlib

    monkeypatch.setenv("PALIMPSEST_EMBED_PROVIDER", "local")
    import agent.llm

    importlib.reload(agent.llm)
    try:
        assert agent.llm.embed is embed
    finally:
        monkeypatch.setenv("PALIMPSEST_EMBED_PROVIDER", "bedrock")
        importlib.reload(agent.llm)


def test_unknown_provider_fails_loudly(monkeypatch):
    """A typo must not silently fall back to a provider the operator didn't choose."""
    import importlib

    monkeypatch.setenv("PALIMPSEST_EMBED_PROVIDER", "titan-ish")
    import agent.llm

    with pytest.raises(RuntimeError, match="unknown PALIMPSEST_EMBED_PROVIDER"):
        importlib.reload(agent.llm)

    monkeypatch.setenv("PALIMPSEST_EMBED_PROVIDER", "bedrock")
    importlib.reload(agent.llm)
