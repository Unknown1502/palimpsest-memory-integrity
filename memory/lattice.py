"""
memory/lattice.py — the integrity lattice, in Python.

This mirrors database/schema.sql's CHECK constraints exactly
(`source_integrity_consistent` and `capability_requires_integrity`). Both
copies must agree: the SQL constraint is the enforcement of last resort if
some future caller bypasses memory/gate.py; this module is what gate.py
checks *first*, before ever opening a database connection, so a rejected
admission never touches the database at all.

Biba "no write-up", applied to agent cognition: a belief's source integrity
caps the class of decision it is allowed to influence. A belief from an
untrusted source may inform ("informational"); it may never suppress an
alert or trigger an action on its own authority.
"""

from __future__ import annotations

from enum import IntEnum


class Integrity(IntEnum):
    """Source authority, ascending. Values match memories.integrity_level."""

    UNTRUSTED_INGEST = 1
    AGENT_INFERRED = 2
    VERIFIED_TOOL = 3
    HUMAN_CONFIRMED = 4


class Capability(IntEnum):
    """
    Decision-impact class a belief may influence, ascending. Values match
    memories.capability_ceiling / workspaces.autonomy_ceiling as strings via
    CAPABILITY_TO_STR below.
    """

    INFORMATIONAL = 1
    SUPPRESSIVE = 2
    ACTUATING = 3


# source_kind (the string stored in memories.source_kind and Provenance)
# <-> Integrity. Must match schema.sql's source_integrity_consistent CHECK.
INTEGRITY_BY_SOURCE: dict[str, Integrity] = {
    "untrusted_ingest": Integrity.UNTRUSTED_INGEST,
    "agent_inferred": Integrity.AGENT_INFERRED,
    "verified_tool": Integrity.VERIFIED_TOOL,
    "human_confirmed": Integrity.HUMAN_CONFIRMED,
}

CAPABILITY_TO_STR: dict[Capability, str] = {
    Capability.INFORMATIONAL: "informational",
    Capability.SUPPRESSIVE: "suppressive",
    Capability.ACTUATING: "actuating",
}
STR_TO_CAPABILITY: dict[str, Capability] = {v: k for k, v in CAPABILITY_TO_STR.items()}

# The lattice itself. Must match schema.sql's capability_requires_integrity
# CHECK: a belief may hold a given capability_ceiling only if its integrity
# is >= this minimum.
CAPABILITY_MIN_INTEGRITY: dict[Capability, Integrity] = {
    Capability.INFORMATIONAL: Integrity.UNTRUSTED_INGEST,  # any source
    Capability.SUPPRESSIVE: Integrity.VERIFIED_TOOL,  # verified_tool or human_confirmed
    Capability.ACTUATING: Integrity.HUMAN_CONFIRMED,  # human_confirmed only
}


class IntegrityViolation(Exception):
    """
    Raised by MemoryGate.admit() when a source's integrity is too low for
    the capability it is trying to assert. Raised BEFORE any database
    connection is opened — a rejected admission leaves zero trace in
    `memories` or `memory_ledger`.
    """


def check_capability_allowed(integrity: Integrity, capability: Capability) -> None:
    """Raise IntegrityViolation if `integrity` may not hold `capability`."""
    required = CAPABILITY_MIN_INTEGRITY[capability]
    if integrity < required:
        raise IntegrityViolation(
            f"integrity {int(integrity)} ({integrity.name.lower()}) cannot assert a "
            f"{CAPABILITY_TO_STR[capability]} belief (requires >= {int(required)})"
        )
