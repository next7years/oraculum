"""
capture_subprocess.py -- reference sealed-capture adapter (capture-v0 §3.5, SWE domain).

Purpose: defeat Tier-1 self-interested reporting for the most common claim in
AI-native development -- "the tests pass." The evaluated agent's own words are, at
best, SELF_REPORTED evidence. This adapter RE-EXECUTES the check in a fresh
subprocess the agent never touched, and records raw signals at the process boundary
into a hash-chained log. The result is a CapturedEvidenceBundle stamped
capture_point=SEALED, time_source=CAPTURE_HOST.

Flow (capture-v0 §3.5):
    1. Canary event through the WHOLE channel -> sets capture_control (the positive
       control for the capture channel itself: proof it can see).
    2. Pre-snapshot: content-address every file in workdir into the store; log the
       manifest digest.
    3. subprocess.run with a scrubbed env; log subprocess_start (argv, env
       fingerprint), stream stdout/stderr as raw payload events, log exit.
    4. Post-snapshot manifest; log it.
    5. seal(); assemble Provenance(SEALED, CAPTURE_HOST, ...).

evaluate_attempt() maps one sealed run to a spine Attempt: hit is a symptom
predicate over RAW exit code / stdout; attempt_valid=False when the subprocess
could not be observed (spawn failure / timeout) -- "couldn't look", not "absent".

Pure stdlib (subprocess, hashlib, json). Zero runtime deps (capture-v0 §1).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional

from verdict_engine import Attempt
from capture_log import CaptureLog, ChainSeal, verify_chain
from capture_store import BlobStore, snapshot_workdir
from capture_provenance import (
    SPEC_VERSION, CapturePoint, TimeSource, CaptureControlStatus,
    Completeness, Provenance, CapturedEvidenceBundle,
)

# A synthetic marker the canary sends through the channel; observing it back
# end-to-end is what makes capture_control == PASS.
CANARY_MARKER = b"__oraculum_canary__"

# Environment allowlist for the scrubbed subprocess env. We do NOT inherit the full
# environment: a leaked var could let the evaluated command behave differently under
# observation than in the wild. Keep only what a hermetic re-execution needs.
_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")


@dataclass
class SealedRunResult:
    returncode: Optional[int]        # None if the process could not be observed
    stdout: str
    stderr: str
    observed: bool                   # False => spawn failure / timeout (couldn't look)
    captured_bundle_provenance: Provenance
    log_path: str
    seal: ChainSeal
    detail: str = ""


def _scrubbed_env() -> dict:
    env = {}
    for k in _ENV_ALLOWLIST:
        if k in os.environ:
            env[k] = os.environ[k]
    # Force deterministic, non-interactive child behavior.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    return env


def _env_fingerprint(env: dict) -> str:
    """A hash of the (sorted) scrubbed env -- logged instead of raw values, so the
    fingerprint is comparable across runs without leaking secrets into the log."""
    canonical = json.dumps(sorted(env.keys()), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def _run_canary(log: CaptureLog) -> CaptureControlStatus:
    """Send a known synthetic event through the channel and confirm it lands with an
    intact chain (capture-v0 §3.3, the capture-channel positive control)."""
    rec = log.append("canary", CANARY_MARKER)
    # Confirm it is exactly what we sent, and that the marker's own hash matches.
    expected_hash = hashlib.sha256(CANARY_MARKER).hexdigest()
    if rec.event_type == "canary" and rec.payload_sha256 == expected_hash:
        return CaptureControlStatus.PASS
    return CaptureControlStatus.FAIL


def sealed_run(cmd: list,
               workdir: str,
               log: CaptureLog,
               store: BlobStore,
               observer_id: str = "capture_subprocess",
               timeout: Optional[float] = 60.0) -> SealedRunResult:
    """Re-execute `cmd` in a fresh subprocess and capture raw signals at the boundary.

    The evaluated agent never touches this channel: it does not write the log, run
    the subprocess, or set the provenance. That separation is the whole point --
    the evidence is captured OUTSIDE the agent's write domain (capture_point=SEALED).
    """
    # 1. Canary: prove the channel can see, before we rely on it.
    capture_control = _run_canary(log)

    # 2. Pre-snapshot the workspace (content-addressed); log its manifest digest.
    pre_manifest = snapshot_workdir(workdir, store)
    log.append("pre_snapshot", pre_manifest.to_json().encode("utf-8"))

    env = _scrubbed_env()
    fp = _env_fingerprint(env)

    # 3. Run the command. Log start, raw stdout/stderr, and exit.
    log.append("subprocess_start",
               json.dumps({"argv": cmd, "env_fingerprint": fp, "workdir": workdir},
                          sort_keys=True, separators=(",", ":")).encode("utf-8"))

    observed = True
    returncode: Optional[int] = None
    stdout = ""
    stderr = ""
    detail = ""
    try:
        proc = subprocess.run(cmd, cwd=workdir, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=timeout)
        returncode = proc.returncode
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        # Stream raw chunks as payload events (raw signal, never a summary).
        if proc.stdout:
            log.append("stdout_chunk", proc.stdout)
        if proc.stderr:
            log.append("stderr_chunk", proc.stderr)
        log.append("exit",
                   json.dumps({"returncode": returncode,
                               "resource_note": "wall-bounded subprocess.run"},
                              sort_keys=True, separators=(",", ":")).encode("utf-8"))
    except (subprocess.TimeoutExpired, OSError) as e:
        # Could not observe the run to completion: "couldn't look", not "absent".
        observed = False
        detail = "%s:%s" % (type(e).__name__, e)
        log.append("observation_failure", detail.encode("utf-8"))

    # 4. Post-snapshot manifest; log it.
    post_manifest = snapshot_workdir(workdir, store)
    log.append("post_snapshot", post_manifest.to_json().encode("utf-8"))

    # 5. Seal + verify the chain, then assemble provenance.
    seal = log.seal()
    chain_report = verify_chain(log.path, seal)

    events_observed = seal.record_count
    provenance = Provenance(
        spec_version=SPEC_VERSION,
        capture_point=CapturePoint.SEALED,      # observer outside agent's write domain
        time_source=TimeSource.CAPTURE_HOST,    # observer's clock stamped every record
        chain_seal=seal,
        chain_report=chain_report,
        capture_control=capture_control,
        completeness=Completeness(
            events_observed=events_observed,
            events_claimed=events_observed,     # sealed channel claims exactly what it logged
            truncated=not chain_report.intact and chain_report.reason.startswith("record_count"),
            blind_windows=[],                   # a hermetic subprocess has no unwatched window
        ),
        observer_id=observer_id,
    )

    return SealedRunResult(returncode=returncode, stdout=stdout, stderr=stderr,
                           observed=observed, captured_bundle_provenance=provenance,
                           log_path=log.path, seal=seal, detail=detail)


def evaluate_attempt(attempt_id: str,
                     run: SealedRunResult,
                     symptom_predicate: Callable[[SealedRunResult], bool],
                     env: str = "sealed-subprocess") -> Attempt:
    """Map one sealed run to a spine Attempt (capture-v0 §3.5, plugin shape).

    symptom_predicate reads RAW exit code / stdout (e.g. "pytest exited non-zero").
    attempt_valid=False when the subprocess could not be observed -- that is
    "couldn't look", categorically different from "the symptom was absent".
    """
    if not run.observed:
        return Attempt(attempt_id=attempt_id, attempt_valid=False,
                       hit=False, env_fingerprint=env)
    hit = bool(symptom_predicate(run))
    return Attempt(attempt_id=attempt_id, attempt_valid=True,
                   hit=hit, env_fingerprint=env)


def nonzero_exit(run: SealedRunResult) -> bool:
    """A ready-made symptom predicate: 'the re-executed check exited non-zero.'
    This is the raw-signal answer to the agent's 'the tests pass' claim."""
    return run.returncode is not None and run.returncode != 0


def _selfcheck() -> int:
    """DoD (capture-v0 §5, M5):
      (1) a sealed run of a trivial (passing) target produces an ADMISSIBLE bundle;
      (2) killing the channel mid-run produces chain_truncated.
    Uses `sys.executable -c` as a hermetic, dependency-free stand-in for a pytest
    target (a fresh subprocess the 'agent' never touched)."""
    import sys
    import tempfile

    from capture_admissibility import judge_admissibility, Admissibility
    from verdict_engine import EvidenceBundle, PositiveControl, PCStatus

    # (1) trivial PASSING target -> exit 0 -> ADMISSIBLE bundle.
    wd = tempfile.mkdtemp(prefix="oraculum_sealed_ok_")
    with open(os.path.join(wd, "sample.py"), "w") as f:
        f.write("assert 1 + 1 == 2\n")
    store = BlobStore(os.path.join(wd, "_store"))
    log = CaptureLog(os.path.join(wd, "capture.jsonl"))
    run = sealed_run([sys.executable, os.path.join(wd, "sample.py")], wd, log, store)

    cb = CapturedEvidenceBundle(
        bundle=EvidenceBundle("sealed-ok", "tests-pass",
                              [Attempt("a0", attempt_valid=True, hit=False)],
                              PositiveControl(PCStatus.PASS)),
        provenance=run.captured_bundle_provenance,
    )
    adm = judge_admissibility(cb)
    assert run.returncode == 0, "trivial target should exit 0, got %r" % run.returncode
    assert run.observed, "trivial target must be observable"
    assert adm.status is Admissibility.ADMISSIBLE, \
        "expected ADMISSIBLE, got %s/%s" % (adm.status.value, adm.reason_code)

    # (2) kill the channel mid-run: truncate the on-disk log AFTER sealing.
    #     The seal knows the true length; a shortened chain -> chain_truncated.
    with open(run.log_path, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    assert len(lines) >= 3, "need a non-trivial chain to truncate"
    with open(run.log_path, "w", encoding="utf-8") as f:
        f.writelines(lines[:-2])   # simulate the channel dying before it flushed the tail
    killed_report = verify_chain(run.log_path, run.seal)
    prov2 = run.captured_bundle_provenance
    prov2.chain_report = killed_report
    cb2 = CapturedEvidenceBundle(bundle=cb.bundle, provenance=prov2)
    adm2 = judge_admissibility(cb2)
    assert adm2.status is Admissibility.INADMISSIBLE and adm2.reason_code == "chain_truncated", \
        "expected chain_truncated, got %s/%s" % (adm2.status.value, adm2.reason_code)

    print("capture_subprocess self-check: PASS")
    print("  (1) sealed run of a passing target -> ADMISSIBLE")
    print("  (2) channel killed mid-run          -> INADMISSIBLE / chain_truncated")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selfcheck())
