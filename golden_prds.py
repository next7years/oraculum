"""
golden_prds.py -- Engine B's own eval seed (TDD section 6).

Same discipline as golden_fixtures.py is for Engine A: hand-labeled targets with a
KNOWN-correct (oracle_class, status). Running gate(target).status == expected is
Engine B's regression guard -- the gating rules can regress too, so a labeled set
sits above them.

Each entry pins one gating branch. One is the recruiting-recall target from the
Phase-1 example (Step 3) -- so the taxonomy is grounded by a case we actually ran,
not a model prior (PRD P3).
"""
from oracle_taxonomy import (
    Target, OracleClass,
    HAS_REFERENCE, HAS_GOLDEN_SET, HAS_DOWNSTREAM_SIGNAL, ALLOW_FUZZY,
)
from readiness_gate import Status


# each entry: (name, target, expected_status)
GOLDEN_PRDS = []


def _add(name, target, status):
    GOLDEN_PRDS.append((name, target, status))


# 1. plain checkable predicate -> READY
_add("checkable_exit_code",
     Target("CLI returns exit code 0 on valid input", OracleClass.CHECKABLE,
            rationale="exact, machine-checkable predicate"),
     Status.READY)

# 2. the recruiting recall target from the Phase-1 example. It's
#    CHECKABLE_WITH_REFERENCE, and the reference (CGL golden set) IS available
#    -> READY. This is the grounded case: the taxonomy is validated by a real run.
_add("recall_at_k_with_golden",
     Target("golden candidate appears in recall top-k (future-mobility-head-of-ml)",
            OracleClass.CHECKABLE_WITH_REFERENCE,
            detected_prerequisites={HAS_REFERENCE: True, HAS_GOLDEN_SET: True},
            rationale="recall@k against the copied CGL golden set (must_surface labels)"),
     Status.READY)

# 3. checkable-with-reference but NO reference supplied -> NEEDS_INPUT
_add("recall_at_k_no_reference",
     Target("retrieval returns the 'right' documents for a query",
            OracleClass.CHECKABLE_WITH_REFERENCE,
            detected_prerequisites={HAS_REFERENCE: False},
            rationale="checkable only against a ground-truth set that wasn't provided"),
     Status.NEEDS_INPUT)

# 4. fuzzy judgment, no golden set -> BLOCKED (uncalibrated judge ≈ theater)
_add("tone_fuzzy_no_golden",
     Target("assistant replies in a 'warm, professional' tone",
            OracleClass.FUZZY_JUDGE,
            detected_prerequisites={HAS_GOLDEN_SET: False},
            rationale="tone correctness is expert judgment; no calibration set"),
     Status.BLOCKED)

# 5. fuzzy + golden but NO conscious opt-in -> NEEDS_INPUT (never trust a judge silently)
_add("fuzzy_golden_no_optin",
     Target("summary is faithful to the source",
            OracleClass.FUZZY_JUDGE,
            detected_prerequisites={HAS_GOLDEN_SET: True},
            rationale="has a golden set, but the user hasn't opted into judge-based eval"),
     Status.NEEDS_INPUT)

# 5b. fuzzy + golden + opted in but judge NOT calibrated to the bar -> BLOCKED
_add("fuzzy_optin_below_kappa",
     Target("summary is faithful to the source (judge κ=0.42)",
            OracleClass.FUZZY_JUDGE,
            detected_prerequisites={HAS_GOLDEN_SET: True, ALLOW_FUZZY: True},
            measured_kappa=0.42,
            rationale="opted in, but judge-human agreement is below the κ≥0.6 bar"),
     Status.BLOCKED)

# 5c. fuzzy + golden + opted in + judge clears the κ bar -> READY (with fuzzy stamp)
_add("fuzzy_optin_calibrated",
     Target("summary is faithful to the source (judge κ=0.74)",
            OracleClass.FUZZY_JUDGE,
            detected_prerequisites={HAS_GOLDEN_SET: True, ALLOW_FUZZY: True},
            measured_kappa=0.74,
            rationale="calibrated judge (κ=0.74) stands in for the hard oracle; stamped fuzzy"),
     Status.READY)

# 5d. THE ceiling-collapse case (a real Haiku run surfaced this): the judge agrees
# with one annotator (κ=0.72) but the annotators themselves don't agree (κ=-0.17).
# High judge-vs-one-human κ is a mirage when there's no shared human truth -> BLOCKED.
_add("fuzzy_human_ceiling_collapsed",
     Target("infer the candidate's unspoken real need (judge κ=0.72, experts disagree)",
            OracleClass.FUZZY_JUDGE,
            detected_prerequisites={HAS_GOLDEN_SET: True, ALLOW_FUZZY: True},
            measured_kappa=0.72, human_ceiling=-0.17,
            rationale="judge matches one expert, but expert-vs-expert κ is below the bar: "
                      "no trustworthy ground truth, so the judge's agreement is a mirage"),
     Status.BLOCKED)

# 6. downstream-only correctness, no downstream signal -> BLOCKED
_add("recommendation_downstream_no_signal",
     Target("recommended candidate eventually gets hired",
            OracleClass.DOWNSTREAM_ONLY,
            detected_prerequisites={HAS_DOWNSTREAM_SIGNAL: False},
            rationale="only a real-world hire outcome confirms it; none defined"),
     Status.BLOCKED)

# 7. no signal reaches the harness -> BLOCKED (redesign to emit a signal)
_add("internal_state_unobservable",
     Target("the model 'understands' the user's intent",
            OracleClass.UNOBSERVABLE,
            rationale="no observable signal corresponds to this claim"),
     Status.BLOCKED)
