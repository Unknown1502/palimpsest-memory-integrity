"""api/deps.py — shared FastAPI dependencies."""

from __future__ import annotations

import os
from functools import lru_cache

from agent.bedrock_client import adjudicate
from memory.gate import MemoryGate


def get_dsn() -> str:
    return os.environ["PALIMPSEST_DSN"]


@lru_cache(maxsize=1)
def get_gate() -> MemoryGate:
    return MemoryGate(dsn=get_dsn(), adjudicate_fn=adjudicate)
