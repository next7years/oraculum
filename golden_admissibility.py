"""
golden_admissibility.py -- golden fixtures for the admissibility gate
(capture-v0 §4, Pattern B; same judge-above-judge pattern as golden_fixtures.py).

The gate (capture_admissibility.judge_admissibility) sits above the pipeline; this
set sits above the gate -- because the gate itself can regress. Each fixture is a
(name, CapturedEvidenceBundle, expected_status, expected_reason) tuple, and CI fails
non-zero on any mismatch.

Honesty note: the chain_reports for the tamper fixtures (#2,#3,#4) are NOT
hand-written. We build a REAL hash-chain, apply a REAL mutation, and run the REAL
verify_chain to obtain the report -- so these fixtures exercise the actual detector,
not a mock of it. The provenance-only fixtures (#5..#11) start from a clean, truly
intact chain and vary a single provenance field.

Pure stdlib, seeded, deterministic. No network, no LLM.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile

from verdict_engine import EvidenceBundle, Attempt, PositiveControl, PCStatus
from capture_log import CaptureLog, ChainSeal, verify_chain
from capture_provenance import (
    SPEC_VERSION, CapturePoint, TimeSource, CaptureControlStatus,
    Completeness, Provenance, CapturedEvidenceBundle,
)
from capture_admissibility import (
    Admissibility, AdmissibilityThresholds, judge_admissibility,
)

_CHAIN_LEN = 8


def _seeded_clocks(seed):
    c = {"h": 1_700_000_000.0 + seed, "m": float(seed)}

    def h():
        c["h"] += 0.001
        return round(c["h"], 6)

    def m():
        c["m"] += 0.001
        return round(c["m"], 6)

    return h, m


def _build_chain(path, seed=7):
    rng = random.Random(seed)
    h, m = _seeded_clocks(seed)
    log = CaptureLog(path, clock=h, monoclock=m)
    for i in range(_CHAIN_LEN):
        log.append("stdout_chunk", ("line-%d-%d" % (i, rng.randrange(1000))).encode())
    seal = log.seal()
    with open(path, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    return seal, records


def _write(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")


def _sample_attempts():
    """A trivial valid attempt series -- the bundle content is irrelevant to the
    gate (the gate judges custody, not the verdict), but it must be well-formed."""
    return [Attempt("a%d" % i, attempt_valid=True, hit=False) for i in range(6)]


def _provenance(chain_report, seal,
                capture_point=CapturePoint.SEALED,
                time_source=TimeSource.CAPTURE_HOST,
                capture_control=CaptureControlStatus.PASS,
                events_observed=8, events_claimed=8,
                truncated=False, blind_windows=None,
                spec_version=SPEC_VERSION):
    return Provenance(
        spec_version=spec_version,
        capture_point=capture_point,
        time_source=time_source,
        chain_seal=seal,
        chain_report=chain_report,
        capture_control=capture_control,
        completeness=Completeness(
            events_observed=events_observed,
            events_claimed=events_claimed,
            truncated=truncated,
            blind_windows=blind_windows or [],
        ),
        observer_id="golden-observer",
    )


def _cb(prov):
    return CapturedEvidenceBundle(
        bundle=EvidenceBundle("golden-run", "golden-symptom", _sample_attempts(),
                              PositiveControl(PCStatus.PASS)),
        provenance=prov,
    )


def build_golden(workdir):
    """Return the GOLDEN list: (name, cb, expected_status, expected_reason)."""
    clean_path = os.path.join(workdir, "clean.jsonl")
    seal, base = _build_chain(clean_path)
    clean_report = verify_chain(clean_path, seal)   # REAL intact report
    assert clean_report.intact, "sanity: freshly built chain must verify intact"

    golden = []

    # 1. clean sealed run -> ADMISSIBLE / ok
    golden.append(("clean_sealed_run",
                   _cb(_provenance(clean_report, seal)),
                   Admissibility.ADMISSIBLE, "ok"))

    # 2. one payload byte flipped mid-chain -> chain_broken
    flipped = [dict(r) for r in base]
    victim = flipped[3]
    victim["payload"] = victim["payload"] + "X"   # content changes, entry_hash won't match
    fpath = os.path.join(workdir, "flip.jsonl")
    _write(fpath, flipped)
    flip_report = verify_chain(fpath, seal)
    golden.append(("payload_byte_flipped",
                   _cb(_provenance(flip_report, seal)),
                   Admissibility.INADMISSIBLE, "chain_broken"))

    # 3. two records swapped -> chain_broken
    swapped = [dict(r) for r in base]
    swapped[2], swapped[5] = swapped[5], swapped[2]
    spath = os.path.join(workdir, "swap.jsonl")
    _write(spath, swapped)
    swap_report = verify_chain(spath, seal)
    golden.append(("two_records_swapped",
                   _cb(_provenance(swap_report, seal)),
                   Admissibility.INADMISSIBLE, "chain_broken"))

    # 4. tail truncated (seal mismatch) -> chain_truncated
    truncated_recs = [dict(r) for r in base][:-2]
    tpath = os.path.join(workdir, "trunc.jsonl")
    _write(tpath, truncated_recs)
    trunc_report = verify_chain(tpath, seal)   # internally intact, but count < seal
    golden.append(("tail_truncated",
                   _cb(_provenance(trunc_report, seal)),
                   Admissibility.INADMISSIBLE, "chain_truncated"))

    # 5. canary FAIL -> capture_control_failed
    golden.append(("canary_fail",
                   _cb(_provenance(clean_report, seal,
                                   capture_control=CaptureControlStatus.FAIL)),
                   Admissibility.INADMISSIBLE, "capture_control_failed"))

    # 6. canary NOT_RUN -> capture_control_not_run
    golden.append(("canary_not_run",
                   _cb(_provenance(clean_report, seal,
                                   capture_control=CaptureControlStatus.NOT_RUN)),
                   Admissibility.INADMISSIBLE, "capture_control_not_run"))

    # 7. capture_point = SELF_REPORTED -> capture_point_below_min
    golden.append(("capture_point_self_reported",
                   _cb(_provenance(clean_report, seal,
                                   capture_point=CapturePoint.SELF_REPORTED)),
                   Admissibility.INADMISSIBLE, "capture_point_below_min"))

    # 8. events_claimed 100, observed 97 -> event_drop_exceeded
    golden.append(("event_drop",
                   _cb(_provenance(clean_report, seal,
                                   events_observed=97, events_claimed=100)),
                   Admissibility.INADMISSIBLE, "event_drop_exceeded"))

    # 9. time_source = AGENT_REPORTED -> time_source_insufficient
    golden.append(("time_source_agent_reported",
                   _cb(_provenance(clean_report, seal,
                                   time_source=TimeSource.AGENT_REPORTED)),
                   Admissibility.INADMISSIBLE, "time_source_insufficient"))

    # 10. blind window overlapping observation -> blind_window_overlap
    golden.append(("blind_window_overlap",
                   _cb(_provenance(clean_report, seal,
                                   blind_windows=[(1.0, 3.0)])),
                   Admissibility.INADMISSIBLE, "blind_window_overlap"))

    # 11. fixture 7 re-judged with min_capture_point=SELF_REPORTED -> ADMISSIBLE
    #     (threshold sensitivity: proves the gate READS its thresholds).
    golden.append(("self_reported_but_threshold_lowered",
                   _cb(_provenance(clean_report, seal,
                                   capture_point=CapturePoint.SELF_REPORTED)),
                   Admissibility.ADMISSIBLE, "ok",
                   AdmissibilityThresholds(min_capture_point=CapturePoint.SELF_REPORTED)))

    return golden


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    workdir = tempfile.mkdtemp(prefix="oraculum_golden_adm_")
    golden = build_golden(workdir)

    print("=" * 78)
    print("Golden admissibility fixtures (capture-v0 §4, Pattern B) -- gate-above-gate")
    print("=" * 78)
    print("  %-38s%-14s%s" % ("fixture", "expected", "got"))
    print("  " + "-" * 74)

    ok = 0
    for entry in golden:
        name, cb, exp_status, exp_reason = entry[0], entry[1], entry[2], entry[3]
        thr = entry[4] if len(entry) > 4 else AdmissibilityThresholds()
        res = judge_admissibility(cb, thr)
        passed = (res.status == exp_status) and (res.reason_code == exp_reason)
        ok += int(passed)
        flag = "PASS" if passed else "FAIL"
        got = "%s/%s" % (res.status.value, res.reason_code)
        exp = "%s/%s" % (exp_status.value, exp_reason)
        print("  [%s] %-34s%-14s%s" % (flag, name, exp.split("/")[0][:12], got))

    print("  " + "-" * 74)
    print("  %d/%d golden admissibility verdicts match." % (ok, len(golden)))

    if ok != len(golden):
        print("FAIL: admissibility gate regressed.")
        return 1
    print("PASS: all admissibility fixtures hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
