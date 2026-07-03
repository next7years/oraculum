"""
verdict_engine.py -- the app-agnostic spine (spec section 3).

Pure, deterministic verdict rendering for a probabilistic repro/regression oracle:

    render(EvidenceBundle, Thresholds) -> VerdictResult      # no LLM, no agent self-assessment

Implements the decision tree in section 2 of verdict-engine-v0-spec.md.
NOTHING in this file is domain-specific. Domain logic lives in plugins
(see example_exception_oracle.py). This file is the reusable core.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import math


# ---------------------------------------------------------------------------
# Contracts (spec section 1) -- app-agnostic schema.  [reusable spine]
# ---------------------------------------------------------------------------

class PCStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


@dataclass
class PositiveControl:
    """Did we prove the harness CAN see this symptom class? (section 2.0)
    PASS = harness caught an injected known instance -> absence is real, not blindness.
    """
    status: PCStatus = PCStatus.NOT_RUN


@dataclass
class Attempt:
    attempt_id: str
    attempt_valid: bool          # could we even observe? crash/timeout before the
                                 # observation point => False ("couldn't look", not "absent")
    hit: bool                    # all REQUIRED checks passed on this attempt
    env_fingerprint: str = "default"


@dataclass
class EvidenceBundle:
    run_id: str
    symptom_id: str
    attempts: list[Attempt]
    positive_control: PositiveControl = field(default_factory=PositiveControl)
    structural_resolution: bool = False   # E3/E4: trigger provably gone (section 2.4)


@dataclass
class Thresholds:
    k_min: int = 5                  # min valid attempts before we say anything (step 3)
    max_distinct_env: int = 1       # environment determinism guard (step 2)
    theta_high: float = 0.95        # hit-rate >= this counts as STABLE (section 2.3)
    p_floor: float = 0.02           # [OPEN ITEM #2] smallest flaky rate we insist on
                                    # ruling out. TUNE to your VR flaky magnitude.
    alpha: float = 0.01             # tolerated probability of a false NOT_REPRO (section 2.4)
    target_detection: float = 0.99  # fix stage must catch a flaky bug w/ this prob (section 2.3)
    max_s_fix: int = 1000           # if detecting the flake needs more stress than this,
                                    # it's too rare to be a reliable oracle -> INCONCLUSIVE


class Verdict(str, Enum):
    CONFIRMED_REPRO = "CONFIRMED_REPRO"
    CONFIRMED_NOT_REPRO = "CONFIRMED_NOT_REPRO"
    INCONCLUSIVE = "INCONCLUSIVE"


class SubState(str, Enum):
    STABLE = "STABLE"
    FLAKY = "FLAKY"


@dataclass
class VerdictResult:
    verdict: Verdict
    sub_state: Optional[SubState] = None
    hit_rate: Optional[float] = None
    hit_rate_ci: Optional[tuple] = None
    n_valid: int = 0
    n_invalid: int = 0
    evidence_types: list = field(default_factory=list)
    positive_control_status: str = PCStatus.NOT_RUN.value
    s_fix_required: Optional[int] = None
    reason_code: str = ""


# ---------------------------------------------------------------------------
# Statistics (spec sections 2.3 / 2.4) -- the "how hard must you look" math.
# ---------------------------------------------------------------------------

def required_attempts_for_notrepro(p_floor: float, alpha: float) -> int:
    """Min clean attempts to rule out flakiness above p_floor at significance alpha.
    Solve (1 - p_floor)^S <= alpha  ->  S >= ln(alpha) / ln(1 - p_floor).   (E1)
    This is why "ran it once, didn't see it" is not a valid NOT_REPRO.
    """
    return math.ceil(math.log(alpha) / math.log(1 - p_floor))


def required_s_fix(p: float, target: float) -> Optional[int]:
    """Stress iterations so the fix stage detects a flaky bug of rate p with prob >= target.
    Solve 1 - (1 - p)^S >= target  ->  S >= ln(1 - target) / ln(1 - p).     (section 2.3)
    """
    if p <= 0:
        return None
    return math.ceil(math.log(1 - target) / math.log(1 - p))


def wilson_ci(hits: int, n: int, z: float = 1.96) -> tuple:
    """Wilson score interval for a binomial rate. Used to report a flaky hit-rate CI
    and to take a conservative (lower-bound) view when accepting a flaky oracle.
    """
    if n == 0:
        return (0.0, 0.0)
    phat = hits / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


# ---------------------------------------------------------------------------
# The Verdict Engine (spec section 2) -- pure deterministic decision tree.
# [crown jewel: order matters; each step short-circuits]
# ---------------------------------------------------------------------------

def render(bundle: EvidenceBundle, thr: Thresholds = Thresholds()) -> VerdictResult:
    valid = [a for a in bundle.attempts if a.attempt_valid]
    n_valid = len(valid)
    n_invalid = len(bundle.attempts) - n_valid
    H = sum(1 for a in valid if a.hit)
    pc = bundle.positive_control.status

    def out(v, **kw):
        return VerdictResult(verdict=v, n_valid=n_valid, n_invalid=n_invalid,
                             positive_control_status=pc.value, **kw)

    # Step 1 -- harness blind? Then we cannot trust ANY "absent". (highest priority)
    if pc == PCStatus.FAIL:
        return out(Verdict.INCONCLUSIVE, reason_code="positive_control_failed")

    # Step 2 -- environment non-determinism breaks cross-attempt comparability.
    distinct_env = len({a.env_fingerprint for a in valid})
    if distinct_env > thr.max_distinct_env:
        return out(Verdict.INCONCLUSIVE, reason_code="env_nondeterminism")

    # Step 3 -- not enough valid observation to conclude anything.
    if n_valid < thr.k_min:
        return out(Verdict.INCONCLUSIVE, reason_code="insufficient_valid_attempts")

    # Step 4 -- deterministic hit -> STABLE repro (the golden case: repro == regression test).
    if H == n_valid:
        return out(Verdict.CONFIRMED_REPRO, sub_state=SubState.STABLE,
                   hit_rate=1.0, reason_code="deterministic_hit")

    # Step 5 -- some hits (1 <= H < n_valid) -> flaky assessment via stress-run acceptance.
    if H >= 1:
        p_hat = H / n_valid
        if p_hat >= thr.theta_high:            # was just under-sampled; treat as STABLE
            return out(Verdict.CONFIRMED_REPRO, sub_state=SubState.STABLE,
                       hit_rate=p_hat, reason_code="high_rate_undersampled")
        lo, hi = wilson_ci(H, n_valid)
        s_fix = required_s_fix(lo, thr.target_detection)   # conservative: use CI lower bound
        if s_fix is not None and s_fix <= thr.max_s_fix:
            return out(Verdict.CONFIRMED_REPRO, sub_state=SubState.FLAKY,
                       hit_rate=p_hat, hit_rate_ci=(round(lo, 4), round(hi, 4)),
                       s_fix_required=s_fix, reason_code="flaky_accepted")
        return out(Verdict.INCONCLUSIVE, reason_code="hit_too_rare_for_reliable_oracle")

    # Step 6 -- H == 0 -> the burden of proof for NOT_REPRO (section 2.4).
    s_required = required_attempts_for_notrepro(thr.p_floor, thr.alpha)
    e1_power = n_valid >= s_required            # E1: exhausted clean stress run
    e2_control = pc == PCStatus.PASS            # E2: harness proven non-blind (mandatory)
    e_structural = bundle.structural_resolution  # E3/E4: trigger provably gone

    if e2_control and (e1_power or e_structural):
        etypes = []
        if e1_power:
            etypes.append("E1_exhausted_stress")
        etypes.append("E2_positive_control")
        if e_structural:
            etypes.append("E3E4_structural_resolution")
        return out(Verdict.CONFIRMED_NOT_REPRO, evidence_types=etypes,
                   reason_code="not_repro_burden_met")

    # Otherwise: the honest fallback -> routes to human / a bigger stress campaign.
    missing = []
    if not e2_control:
        missing.append("positive_control!=PASS")
    if not (e1_power or e_structural):
        missing.append(f"need>={s_required}_clean_attempts_or_structural_proof(have_{n_valid})")
    return out(Verdict.INCONCLUSIVE, reason_code="not_repro_burden_unmet:" + ",".join(missing))
