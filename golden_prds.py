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
    HAS_REFERENCE, HAS_GOLDEN_SET, HAS_DOWNSTREAM_SIGNAL,
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

# 5. fuzzy judgment WITH a calibration golden set -> READY (calibrated judge is ok)
_add("tone_fuzzy_with_golden",
     Target("summary is faithful to the source (calibrated judge)",
            OracleClass.FUZZY_JUDGE,
            detected_prerequisites={HAS_GOLDEN_SET: True},
            rationale="fuzzy, but a human-labeled golden set exists to calibrate κ"),
     Status.READY)

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
