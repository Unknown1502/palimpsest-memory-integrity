"""
agent/local_embeddings.py — offline embeddings, for when Bedrock isn't there.

Why this exists, stated plainly: on 2026-08-18 the AWS account backing this
project was suspended mid-build. agent/llm.py had already made *chat*
swappable after an earlier Bedrock billing failure, but embed() was still
hard-wired to Titan with a comment reasoning that embeddings "were never
affected". They were affected the moment the account went down, and that
took the entire demo with it: no embeddings means no admit(), no
retrieve(), and therefore no attack scenario, no benchmark, and no video.

A memory-integrity layer whose memory stops working when one vendor's
billing hiccups is not a memory-integrity layer. So embeddings now have the
same provider switch chat does:

    PALIMPSEST_EMBED_PROVIDER=bedrock   (default) Titan Text Embeddings V2
    PALIMPSEST_EMBED_PROVIDER=local     this module -- no network, no keys

WHAT THIS IS, precisely
-----------------------
A signed hashing vectorizer (the "hashing trick") over word tokens and
character 4-grams, with sublinear term frequency, projected into
EMBED_DIMS buckets and L2-normalized. Cosine similarity between two vectors
is therefore a weighted LEXICAL overlap score.

WHAT THIS IS NOT
----------------
It is not a neural embedding and it does not capture meaning. "the host is
benign" and "the machine is harmless" are near-identical to Titan and
nearly orthogonal here. Anything relying on paraphrase matching will be
worse with this provider, and that is a real loss, not a footnote.

Why it is still the right fallback for THIS system: retrieval here is
dominated by shared concrete identifiers -- `ip:203.0.113.9`,
`subject_key`, `predicate` -- which lexical overlap captures exactly. The
attack scenario turns on a belief about an IP being retrieved for an alert
about that same IP. That is a token-overlap question, not a paraphrase
question.

Deterministic: the same text always produces the same vector, on any
machine, with no model file and no network. That also makes it safe for
tests, though tests keep using conftest's own stub so this module's
behavior is never load-bearing for them.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable

from memory.gate import EMBED_DIMS

# Keeps dotted/colon-separated identifiers whole: an IPv4 address, a
# `ip:203.0.113.9` subject key, or a CVE id must survive tokenization as one
# token, because those are precisely the tokens retrieval depends on.
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9._:\-/]*")

_CHAR_NGRAM = 4

# Char n-grams are useful (they catch `203.0.113.` inside a longer string)
# but there are far more of them than words, so they would otherwise
# dominate the vector. Down-weighted rather than dropped.
#
# 0.1 was chosen by measurement, not taste. Scoring the demo corpus -- the
# seeded 10.0.0.7 belief and the planted 203.0.113.9 belief, against alerts
# from each address -- the correct memory wins at every weight tried, but
# the margin on the attack query widens as this falls: at 0.35 the planted
# belief leads the seed belief by only 1.10x, at 0.12 by 1.35x. A thin
# margin is a demo that ranks correctly on this machine and might not on
# the next paraphrase, so prefer the wider one.
_CHAR_WEIGHT = 0.1


def _tokens(text: str) -> Iterable[tuple[str, float]]:
    normalized = text.lower().strip()

    for word in _WORD_RE.findall(normalized):
        yield f"w:{word}", 1.0
        # An IP or dotted key is also emitted piecewise, so a claim about
        # `ip:203.0.113.9` still overlaps an alert that writes the address
        # bare, without the two having to be byte-identical.
        if "." in word or ":" in word:
            for part in re.split(r"[.:/]", word):
                if part:
                    yield f"p:{part}", 0.5

    squashed = re.sub(r"\s+", " ", normalized)
    for i in range(len(squashed) - _CHAR_NGRAM + 1):
        yield f"c:{squashed[i : i + _CHAR_NGRAM]}", _CHAR_WEIGHT


def _bucket_and_sign(token: str) -> tuple[int, float]:
    """
    Signed hashing: the sign bit keeps unrelated tokens from only ever
    adding, which would make every vector point into the same orthant and
    compress the useful range of cosine similarity.
    """
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") % EMBED_DIMS
    sign = 1.0 if digest[4] & 1 else -1.0
    return bucket, sign


def embed(text: str) -> list[float]:
    """Embed `text` into an L2-normalized EMBED_DIMS vector. No network."""
    if not isinstance(text, str):
        raise TypeError(f"embed() expects str, got {type(text).__name__}")

    counts: dict[str, float] = {}
    for token, weight in _tokens(text):
        counts[token] = counts.get(token, 0.0) + weight

    vector = [0.0] * EMBED_DIMS
    for token, count in counts.items():
        bucket, sign = _bucket_and_sign(token)
        # Sublinear tf: a term repeated ten times is more important than one
        # repeated once, but not ten times more.
        vector[bucket] += sign * (1.0 + math.log(count))

    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        # Empty or punctuation-only input. memory/gate.py rejects zero
        # vectors outright, so return a fixed unit vector instead of
        # something that would fail deep inside admit() with a confusing
        # error about normalization.
        vector[0] = 1.0
        return vector

    return [x / norm for x in vector]


def main() -> int:
    """Smoke test mirroring agent/bedrock_client.py's, minus the network."""
    claim = "ip:203.0.113.9 classification approved security scanner"
    matching = "Alert INC-4821: source_ip=203.0.113.9 signature=CVE-2024 RCE attempt"
    unrelated = "quarterly revenue projections for the northeast sales region"

    v_claim, v_match, v_other = embed(claim), embed(matching), embed(unrelated)

    def cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))

    print(f"dims: {len(v_claim)} (expected {EMBED_DIMS})")
    print(f"claim <-> matching alert : {cos(v_claim, v_match):+.4f}")
    print(f"claim <-> unrelated text : {cos(v_claim, v_other):+.4f}")
    print(f"determinism              : {embed(claim) == v_claim}")

    ok = len(v_claim) == EMBED_DIMS and cos(v_claim, v_match) > cos(v_claim, v_other)
    print("RESULT:", "OK" if ok else "FAILED -- matching text did not score above unrelated text")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
