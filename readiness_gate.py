"""
readiness_gate.py -- Engine B's deterministic gate (PRD/TDD section 4.2).

This is the MOAT: given a classified Target, decide READY / NEEDS_INPUT / BLOCKED
by versioned, inspectable rules -- NOT by a model's whim. Same act-vs-judge
separation as Engine A: the (future) LLM adapter proposes a Target; THIS gate judges.

The gate's whole job is to CONFRONT: refuse to green-light an eval where a
prerequisite (ground truth, calibration set, downstream signal, any observable
signal) is missing. Blocking is the product, not a failure mode.

No LLM here. Pure function: gate(Target) -> GateResult.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from oracle_taxonomy import (
    Target, OracleClass,
    HAS_REFERENCE, HAS_GOLDEN_SET, HAS_DOWNSTREAM_SIGNAL,
)


class Status(str, Enum):
    READY = "READY"              # an honest eval is possible now
    NEEDS_INPUT = "NEEDS_INPUT"  # possible, but a named prerequisite is missing
    BLOCKED = "BLOCKED"          # do not generate; the eval would be theater


@dataclass
class GateResult:
    target: str
    oracle_class: OracleClass
    status: Status
    reason_code: str = ""               # which rule fired (audit; mirrors Engine A)
    blocked_on: list = field(default_factory=list)      # missing prerequisites
    forced_questions: list = field(default_factory=list)  # the questions you must answer
    why: str = ""                       # human explanation, WITH a literature anchor


def gate(t: Target) -> GateResult:
    """Apply the deterministic gating rules (order-as-rule, first match wins)."""

    def out(status, reason_code, blocked_on=None, forced_questions=None, why=""):
        return GateResult(target=t.target, oracle_class=t.oracle_class, status=status,
                          reason_code=reason_code, blocked_on=blocked_on or [],
                          forced_questions=forced_questions or [], why=why)

    oc = t.oracle_class

    # Rule 1 -- no signal reaches the harness at all: block, redesign the feature.
    if oc == OracleClass.UNOBSERVABLE:
        return out(Status.BLOCKED, "unobservable_no_signal",
                   blocked_on=["any observable signal"],
                   forced_questions=["What signal could the feature emit that a harness "
                                     "could read? If none, the claim is untestable by "
                                     "construction — redesign it to emit one."],
                   why="No signal reaches the harness, so no oracle — sound or "
                       "otherwise — can exist (Barr et al. 2015 frame the oracle as a "
                       "function over observed behavior; with nothing observed there is "
                       "no function to define).")

    # Rule 2 -- fuzzy correctness with no golden set: an uncalibrated judge is theater.
    if oc == OracleClass.FUZZY_JUDGE and not t.has(HAS_GOLDEN_SET):
        return out(Status.BLOCKED, "fuzzy_judge_uncalibrated",
                   blocked_on=[HAS_GOLDEN_SET],
                   forced_questions=["Where is the human-labeled golden set to calibrate "
                                     "the judge against? What judge-vs-human agreement "
                                     "(Cohen's κ) will you require before trusting it?"],
                   why="An uncalibrated LLM judge on a fuzzy target ≈ theater: judges "
                       "carry position/verbosity/self-preference bias and must be "
                       "calibrated against a golden set before they're trusted "
                       "(Gu et al. 2024; Zheng et al. 2023).")

    # Rule 3 -- downstream-only correctness with no downstream signal defined: block.
    if oc == OracleClass.DOWNSTREAM_ONLY and not t.has(HAS_DOWNSTREAM_SIGNAL):
        return out(Status.BLOCKED, "downstream_signal_missing",
                   blocked_on=[HAS_DOWNSTREAM_SIGNAL],
                   forced_questions=["What real-world downstream outcome tells you this "
                                     "was correct, and what is the lag before it lands? "
                                     "Without it you'd be optimizing a proxy (Goodhart)."],
                   why="Correctness is only knowable downstream; with no downstream "
                       "signal defined, any gen-time metric is an unvalidated proxy — "
                       "the exact Goodhart trap this product exists to prevent (PRD §2).")

    # Rule 4 -- checkable-with-reference but no reference: solvable, needs the ref.
    if oc == OracleClass.CHECKABLE_WITH_REFERENCE and not t.has(HAS_REFERENCE):
        return out(Status.NEEDS_INPUT, "reference_missing",
                   blocked_on=[HAS_REFERENCE],
                   forced_questions=["What is the reference / ground-truth source (e.g. a "
                                     "golden set of known-good answers)? Provide it and "
                                     "this target becomes READY; withhold it and it "
                                     "degrades to a fuzzy judgment."],
                   why="The predicate is machine-checkable, but only against a reference "
                       "you haven't supplied yet — name the reference and it's READY.")

    # Rule 5 (else) -- an honest, checkable eval is possible now.
    return out(Status.READY, "checkable_ready",
               why="A machine-checkable predicate exists (with its reference, if any), so "
                   "render() can judge it without any model self-assessment.")
