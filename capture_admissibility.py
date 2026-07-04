"""
capture_admissibility.py -- the admissibility gate (capture-v0 §3.4).

A new deterministic gate that runs BEFORE render(): chain of custody precedes
trial. Inadmissible evidence never reaches the judge. Same shape as render() -- a
pure, ordered, short-circuit decision tree where the FIRST failing step wins and
names exactly one reason_code.

The thresholds in AdmissibilityThresholds are the owner's pre-decided calls
(capture-v0 §2): SEALED capture point, CAPTURE_HOST time, canary required, zero
tolerated drop. They are defaults, not magic constants -- the gate reads them, so
loosening a threshold (e.g. min_capture_point=SELF_REPORTED) demonstrably changes
the verdict (golden fixture #11).

render_with_admissibility wraps the spine WITHOUT editing it (capture-v0 §1): the
verdict engine is simply never consulted for inadmissible evidence.

Pure stdlib, deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from verdict_engine import Thresholds, render
from capture_provenance import (
    SPEC_VERSION, CapturePoint, TimeSource, CaptureControlStatus,
    CapturedEvidenceBundle, CAPTURE_POINT_RANK, TIME_SOURCE_RANK,
)

# verify_chain reasons that mean "the tail was dropped" as opposed to "a record's
# content/order was corrupted". Step 3 (chain_truncated) owns the former; step 2
# (chain_broken) owns the latter. Keeping them distinct lets the gate name the
# specific failure the owner cares about (capture-v0 §3.4 table).
_TRUNCATION_REASON_PREFIXES = ("record_count_mismatch", "final_hash_mismatch")


class Admissibility(str, Enum):
    ADMISSIBLE = "ADMISSIBLE"
    INADMISSIBLE = "INADMISSIBLE"


@dataclass
class AdmissibilityThresholds:
    min_capture_point: CapturePoint = CapturePoint.SEALED
    min_time_source: TimeSource = TimeSource.CAPTURE_HOST
    require_capture_control: bool = True
    max_drop_rate: float = 0.0


@dataclass
class AdmissibilityResult:
    status: Admissibility
    reason_code: str        # exactly one; first failing step wins
    spec_version: str


def _is_truncation_reason(reason: str) -> bool:
    return any(reason.startswith(p) for p in _TRUNCATION_REASON_PREFIXES)


def judge_admissibility(cb: CapturedEvidenceBundle,
                        thr: AdmissibilityThresholds = AdmissibilityThresholds()
                        ) -> AdmissibilityResult:
    """Ordered short-circuit gate (capture-v0 §3.4). First failure wins.

    Step order (order matters, same discipline as render()):
      1 spec_version match     -> spec_version_mismatch
      2 chain intact           -> chain_broken        (content/order corruption)
      3 seal count == records  -> chain_truncated     (dropped tail)
      4 capture_control == PASS -> capture_control_failed / capture_control_not_run
      5 capture_point >= min   -> capture_point_below_min
      6 time_source  >= min    -> time_source_insufficient
      7 not truncated          -> completeness_truncated
      8 drop_rate <= max       -> event_drop_exceeded
      9 no blind-window overlap -> blind_window_overlap
      -  all pass              -> ADMISSIBLE / ok
    """
    p = cb.provenance

    def result(status, reason):
        return AdmissibilityResult(status=status, reason_code=reason,
                                   spec_version=SPEC_VERSION)

    # 1. Spec version must match -- a bundle from an unknown capture spec is not
    #    something this gate is entitled to judge.
    if p.spec_version != SPEC_VERSION:
        return result(Admissibility.INADMISSIBLE, "spec_version_mismatch")

    # 2. Chain integrity: any content/order corruption is fatal. Truncation is
    #    handled by step 3 so it can be named distinctly.
    cr = p.chain_report
    if not cr.intact and not _is_truncation_reason(cr.reason):
        return result(Admissibility.INADMISSIBLE, "chain_broken")

    # 3. Truncation: seal (taken at capture time) knows the true length; a shorter
    #    on-disk chain, or a broken seal match, means the tail was dropped.
    if _is_truncation_reason(cr.reason) or p.chain_seal.record_count != cr.records:
        return result(Admissibility.INADMISSIBLE, "chain_truncated")

    # 4. Capture-channel positive control (the canary): proof the channel can see.
    if thr.require_capture_control:
        if p.capture_control == CaptureControlStatus.FAIL:
            return result(Admissibility.INADMISSIBLE, "capture_control_failed")
        if p.capture_control == CaptureControlStatus.NOT_RUN:
            return result(Admissibility.INADMISSIBLE, "capture_control_not_run")

    # 5. Capture point must be at least the minimum (SEALED > SHARED > SELF_REPORTED).
    if CAPTURE_POINT_RANK[p.capture_point] < CAPTURE_POINT_RANK[thr.min_capture_point]:
        return result(Admissibility.INADMISSIBLE, "capture_point_below_min")

    # 6. Time source must be at least the minimum (HOST > MONOTONIC > AGENT).
    if TIME_SOURCE_RANK[p.time_source] < TIME_SOURCE_RANK[thr.min_time_source]:
        return result(Admissibility.INADMISSIBLE, "time_source_insufficient")

    # 7. Completeness: a self-declared truncation is inadmissible.
    if p.completeness.truncated:
        return result(Admissibility.INADMISSIBLE, "completeness_truncated")

    # 8. Event drop rate (only meaningful when the source makes a claim to check).
    comp = p.completeness
    if comp.events_claimed is not None and comp.events_claimed > 0:
        dropped = comp.events_claimed - comp.events_observed
        drop_rate = dropped / comp.events_claimed if dropped > 0 else 0.0
        if drop_rate > thr.max_drop_rate:
            return result(Admissibility.INADMISSIBLE, "event_drop_exceeded")

    # 9. A blind window overlapping the observation span means we were not looking
    #    for part of the run -- absence of evidence, masquerading as evidence of
    #    absence. The observation span is [0, chain_report.records) in event terms;
    #    blind_windows are [start,end) in ts_mono. Any non-empty window recorded
    #    against a run with observed events is treated as an overlap.
    if comp.blind_windows and comp.events_observed > 0:
        for (start, end) in comp.blind_windows:
            if end > start:   # a real, non-degenerate blind interval
                return result(Admissibility.INADMISSIBLE, "blind_window_overlap")

    return result(Admissibility.ADMISSIBLE, "ok")


def render_with_admissibility(cb: CapturedEvidenceBundle,
                              adm_thr: AdmissibilityThresholds = AdmissibilityThresholds(),
                              thr: Thresholds = Thresholds(),
                              _render=render):
    """Chain of custody, then trial (capture-v0 §3.4).

    If the evidence is inadmissible, the verdict engine is NEVER consulted -- the
    AdmissibilityResult is returned in its place. The spine is untouched; this is a
    strict pre-gate. `_render` is injectable ONLY so the M4 DoD can assert the
    short-circuit via a sentinel (it defaults to the real render).
    """
    adm = judge_admissibility(cb, adm_thr)
    if adm.status is Admissibility.INADMISSIBLE:
        return adm                       # verdict engine is never consulted
    return _render(cb.bundle, thr)       # normal Engine A path
