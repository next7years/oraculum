"""
oracle_taxonomy.py -- Engine B's fixed oracle taxonomy (PRD section 4.1 / TDD 4.1).

Engine B turns a PRD/feature spec into an Eval Readiness Report -- NOT eval code.
Its shape mirrors Engine A: an LLM adapter may PROPOSE (text -> structured targets),
but a deterministic, versioned gate JUDGES. This module holds the schema that gate
judges over. The taxonomy is FIXED (the maintainer's IP); the LLM does not invent
classes -- it only classifies into these five.

Nothing here calls an LLM. This is pure, inspectable structure. The LLM adapter
(TDD step 6) is deliberately NOT built yet: the gate is the moat, the adapter is
replaceable, so the gate is grounded and tested first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OracleClass(str, Enum):
    """Can we build an HONEST eval for this target -- and at what cost?"""
    CHECKABLE = "CHECKABLE"
    # a machine-checkable predicate exists (exit code, exact match, a threshold).
    CHECKABLE_WITH_REFERENCE = "CHECKABLE_WITH_REFERENCE"
    # checkable IF a reference / ground-truth source exists (recall@k needs a golden set).
    FUZZY_JUDGE = "FUZZY_JUDGE"
    # correctness is expert judgment; only honest via a *calibrated* judge + golden set.
    DOWNSTREAM_ONLY = "DOWNSTREAM_ONLY"
    # correctness only knowable from a real-world downstream outcome (needs signal + lag).
    UNOBSERVABLE = "UNOBSERVABLE"
    # no signal reaches the harness at all -- block or redesign the feature.


# Prerequisite keys the gate inspects. Kept as plain strings so the (future) LLM
# adapter and any dev-flow integration share one vocabulary (TDD section 2).
HAS_REFERENCE = "has_reference"            # a ground-truth / reference source is available
HAS_GOLDEN_SET = "has_golden_set"          # a labeled, calibration golden set exists
HAS_DOWNSTREAM_SIGNAL = "has_downstream_signal"  # a downstream outcome signal is defined


@dataclass
class Target:
    """One eval target extracted from a PRD (an LLM would PROPOSE these; here we
    hand-write them for the deterministic golden tests). The gate consumes this."""
    target: str                              # human name of the thing being evaluated
    oracle_class: OracleClass
    detected_prerequisites: dict = field(default_factory=dict)  # {HAS_*: bool}
    rationale: str = ""                      # why this class (audit trail)

    def has(self, prereq: str) -> bool:
        return bool(self.detected_prerequisites.get(prereq, False))
