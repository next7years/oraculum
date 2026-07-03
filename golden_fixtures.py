"""
golden_fixtures.py -- hand-labeled EvidenceBundles with KNOWN-correct verdicts.

These are not merely unit tests. They are the Verdict Engine's OWN eval seed
(spec section 5): running render(fixture) == expected_verdict is the judge's
first regression guard. The judge sits above the pipeline; this set sits above
the judge -- because the judge itself can regress.
"""
from verdict_engine import (
    EvidenceBundle, Attempt, PositiveControl, PCStatus, Verdict, SubState,
)


def make_attempts(n_valid: int, n_hits: int, n_invalid: int = 0, env: str = "default"):
    atts = []
    for i in range(n_hits):
        atts.append(Attempt(f"v{i}", attempt_valid=True, hit=True, env_fingerprint=env))
    for i in range(n_valid - n_hits):
        atts.append(Attempt(f"m{i}", attempt_valid=True, hit=False, env_fingerprint=env))
    for i in range(n_invalid):
        atts.append(Attempt(f"x{i}", attempt_valid=False, hit=False, env_fingerprint=env))
    return atts


# each entry: (name, bundle, expected_verdict, expected_sub_state)
GOLDEN = []


def _add(name, bundle, verdict, sub=None):
    GOLDEN.append((name, bundle, verdict, sub))


# 1. deterministic hit -> STABLE repro
_add("stable_repro",
     EvidenceBundle("r1", "s", make_attempts(6, 6)),
     Verdict.CONFIRMED_REPRO, SubState.STABLE)

# 2. intermittent -> FLAKY (accepted via stress-run math)
_add("flaky_repro",
     EvidenceBundle("r2", "s", make_attempts(50, 10), PositiveControl(PCStatus.PASS)),
     Verdict.CONFIRMED_REPRO, SubState.FLAKY)

# 3. harness blind -> INCONCLUSIVE (cannot trust absence)
_add("blind_harness",
     EvidenceBundle("r3", "s", make_attempts(6, 0), PositiveControl(PCStatus.FAIL)),
     Verdict.INCONCLUSIVE)

# 4. environment drift -> INCONCLUSIVE (even though every attempt hit)
_add("env_drift",
     EvidenceBundle("r4", "s", make_attempts(3, 3, env="A") + make_attempts(3, 3, env="B")),
     Verdict.INCONCLUSIVE)

# 5. too few valid attempts -> INCONCLUSIVE
_add("too_few",
     EvidenceBundle("r5", "s", make_attempts(3, 3)),
     Verdict.INCONCLUSIVE)

# 6. exhausted clean stress + positive control -> NOT_REPRO (Route A, statistical)
_add("not_repro_statistical",
     EvidenceBundle("r6", "s", make_attempts(228, 0), PositiveControl(PCStatus.PASS)),
     Verdict.CONFIRMED_NOT_REPRO)

# 7. clean but NOT enough -> INCONCLUSIVE (the "30 clean runs still isn't proof" lesson)
_add("not_repro_burden_unmet",
     EvidenceBundle("r7", "s", make_attempts(30, 0), PositiveControl(PCStatus.PASS)),
     Verdict.INCONCLUSIVE)

# 8. structural proof the trigger is gone + control -> NOT_REPRO (Route B, few attempts)
_add("not_repro_structural",
     EvidenceBundle("r8", "s", make_attempts(10, 0), PositiveControl(PCStatus.PASS),
                    structural_resolution=True),
     Verdict.CONFIRMED_NOT_REPRO)


# ---------------------------------------------------------------------------
# Domain plugin #2 (recruiting recall) -- SAME spine, a different SignalSource.
# These bundles come from the SEEDED, reproducible recall simulator
# (recall_runner.py) over a static CGL data snapshot. Same seed => same series =>
# the recall symptom path is under a deterministic regression guard too, with NO
# real LLM in the test path. See recall_oracle.py for the symptom framing.
# ---------------------------------------------------------------------------
from recall_runner import run_recall_attempts   # noqa: E402  (stdlib-only, seeded)

_GOLDEN_ID = "SEED-0004"   # 李泽明, a golden candidate for future-mobility-head-of-ml

# 9. golden candidate ALWAYS falls out of top-k -> symptom always repros -> STABLE
_add("recall_stable_repro",
     run_recall_attempts("future-mobility-head-of-ml", _GOLDEN_ID,
                         s=6, top_k=3, base_rank_prob=0.0, seed=1),
     Verdict.CONFIRMED_REPRO, SubState.STABLE)

# 10. golden candidate intermittently missed (ranking jitter) -> FLAKY (accepted)
_add("recall_flaky_repro",
     run_recall_attempts("future-mobility-head-of-ml", _GOLDEN_ID,
                         s=50, top_k=3, base_rank_prob=0.8, seed=2),
     Verdict.CONFIRMED_REPRO, SubState.FLAKY)

# 11. golden candidate ALWAYS recalled over the full power-bar stress run + control
#     -> the miss does not reproduce -> NOT_REPRO (Route A, statistical)
_add("recall_not_repro",
     run_recall_attempts("future-mobility-head-of-ml", _GOLDEN_ID,
                         s=228, top_k=3, base_rank_prob=1.0, seed=3),
     Verdict.CONFIRMED_NOT_REPRO)
