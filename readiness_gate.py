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
    HAS_REFERENCE, HAS_GOLDEN_SET, HAS_DOWNSTREAM_SIGNAL, ALLOW_FUZZY,
)

# Landis-Koch "substantial agreement". The one number you own for fuzzy: how much
# judge-vs-human agreement (Cohen's κ) you require before trusting an LLM judge.
# Tune it like p_floor; the default is the literature-standard bar.
KAPPA_MIN = 0.6


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
    fuzzy: bool = False                 # this verdict rests on an LLM judge, not a hard
                                        # predicate -- a permanent honesty stamp for downstream
    measured_kappa: float | None = None  # the judge-vs-human κ this READY/BLOCK rests on


def gate(t: Target, kappa_min: float = KAPPA_MIN) -> GateResult:
    """Apply the deterministic gating rules (order-as-rule, first match wins)."""

    def out(status, reason_code, blocked_on=None, forced_questions=None, why="",
            fuzzy=False, measured_kappa=None):
        return GateResult(target=t.target, oracle_class=t.oracle_class, status=status,
                          reason_code=reason_code, blocked_on=blocked_on or [],
                          forced_questions=forced_questions or [], why=why,
                          fuzzy=fuzzy, measured_kappa=measured_kappa)

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

    # Rule 2 -- FUZZY_JUDGE: a graded, honest path. Fuzzy is never silent; the user
    # must consciously opt in to trusting an LLM judge, AND that judge must be shown
    # (via Cohen's κ) to actually agree with human ground truth. Four sub-cases:
    if oc == OracleClass.FUZZY_JUDGE:
        # 2a -- no golden set: can't calibrate at all -> theater. Only a USER-CONFIRMED
        # golden set counts; the LLM merely claiming one exists is not enough.
        if not t.has_confirmed(HAS_GOLDEN_SET):
            return out(Status.BLOCKED, "fuzzy_judge_uncalibrated",
                       blocked_on=[HAS_GOLDEN_SET], fuzzy=True,
                       forced_questions=["Where is the human-labeled golden set to calibrate "
                                         "the judge against? What judge-vs-human agreement "
                                         "(Cohen's κ) will you require before trusting it?"],
                       why="An uncalibrated LLM judge on a fuzzy target ≈ theater: judges "
                           "carry position/verbosity/self-preference bias and must be "
                           "calibrated against a golden set before they're trusted "
                           "(Gu et al. 2024; Zheng et al. 2023).")
        # 2b -- has a golden set but the user hasn't consciously opted in: don't quietly
        # start trusting a judge on their behalf. Make them choose.
        if not t.has_confirmed(ALLOW_FUZZY):
            return out(Status.NEEDS_INPUT, "fuzzy_requires_opt_in",
                       blocked_on=[ALLOW_FUZZY], fuzzy=True,
                       forced_questions=["This target has no hard oracle — judging it means "
                                         "trusting an LLM judge. Opt in explicitly (allow_fuzzy) "
                                         "to proceed; the verdict will be permanently stamped "
                                         "as judge-based, not predicate-based."],
                       why="Fuzzy eval is never silent. Trusting a judge is a conscious "
                           "decision you make with eyes open — not a default the tool slips in.")
        # 2c-ceiling -- THE deepest fuzzy rule (surfaced by running a real judge):
        # a judge can't be more trustworthy than the ground truth it's calibrated to.
        # If the human annotators themselves don't agree (human_ceiling < the bar), then
        # there is NO trustworthy truth to calibrate against — and a judge's high agreement
        # with any *single* annotator is a mirage, not trust. Block BEFORE looking at the
        # judge's κ, because the judge's κ is meaningless once the ceiling has collapsed.
        c = t.human_ceiling
        if c is not None and c < kappa_min:
            return out(Status.BLOCKED, "no_trustworthy_ground_truth", fuzzy=True,
                       measured_kappa=t.measured_kappa,
                       blocked_on=["human_ceiling>=%.2f" % kappa_min],
                       forced_questions=[f"Your human annotators only agree at κ={c:.2f} "
                                         f"(< {kappa_min:.2f}) — they don't share a definition of "
                                         f"'correct'. A judge that matches one of them isn't "
                                         f"trustworthy; it's mimicking a coin flip. Either sharpen "
                                         f"the rubric until experts agree, or accept this is a "
                                         f"human judgment call, not an automatable eval."],
                       why=f"No trustworthy ground truth: expert-vs-expert agreement is κ={c:.2f}, "
                           f"below the κ ≥ {kappa_min:.2f} bar. You cannot calibrate a judge to a "
                           "truth that doesn't exist — a judge can't be more reliable than the "
                           "humans it's measured against (the anti-Goodhart ceiling).")

        # 2c -- opted in, but calibration not run / κ below the bar: the judge isn't
        # trustworthy yet. Still theater, just a subtler kind.
        k = t.measured_kappa
        if k is None or k < kappa_min:
            have = "not measured" if k is None else f"κ={k:.2f}"
            return out(Status.BLOCKED, "fuzzy_judge_below_kappa",
                       blocked_on=["judge_calibration>=%.2f" % kappa_min], fuzzy=True,
                       measured_kappa=k,
                       forced_questions=[f"Calibrate the judge against the golden set and reach "
                                         f"κ ≥ {kappa_min:.2f} (Landis-Koch 'substantial'). "
                                         f"Currently {have}. A judge that doesn't agree with "
                                         f"humans is a fancy random number generator."],
                       why="You opted into a judge, but it isn't calibrated to trust yet: "
                           f"judge-vs-human agreement is {have}, below the κ ≥ {kappa_min:.2f} "
                           "bar. Below-bar agreement ≈ theater with extra steps.")
        # 2d -- opted in AND the judge clears the κ bar: honestly evaluable, with a
        # permanent fuzzy stamp + the κ it rests on.
        return out(Status.READY, "fuzzy_judge_calibrated", fuzzy=True, measured_kappa=k,
                   why=f"A human-calibrated judge (κ={k:.2f} ≥ {kappa_min:.2f}, 'substantial') "
                       "may stand in for the hard oracle here — but the verdict is stamped "
                       "fuzzy: it rests on judge-human agreement, not a predicate.")

    # Rule 3 -- downstream-only correctness with no USER-CONFIRMED downstream signal: block.
    # The LLM will optimistically claim a signal exists (compliance bias); that claim does
    # not release the gate. Only a signal the user actually confirmed does.
    if oc == OracleClass.DOWNSTREAM_ONLY and not t.has_confirmed(HAS_DOWNSTREAM_SIGNAL):
        return out(Status.BLOCKED, "downstream_signal_missing",
                   blocked_on=[HAS_DOWNSTREAM_SIGNAL],
                   forced_questions=["What real-world downstream outcome tells you this "
                                     "was correct, and what is the lag before it lands? "
                                     "Without it you'd be optimizing a proxy (Goodhart)."],
                   why="Correctness is only knowable downstream; with no downstream "
                       "signal defined, any gen-time metric is an unvalidated proxy — "
                       "the exact Goodhart trap this product exists to prevent (PRD §2).")

    # Rule 4 -- checkable-with-reference but no reference: solvable, needs the ref.
    if oc == OracleClass.CHECKABLE_WITH_REFERENCE and not t.has_confirmed(HAS_REFERENCE):
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
