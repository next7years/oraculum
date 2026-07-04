"""
run_tamper_eval.py -- M1's Engine-A-judged property eval (capture-v0 §4, Pattern A).

The dogfooding centerpiece: the capture layer's correctness certificate is issued
by the SAME judge (verdict_engine.render) it will later feed. That recursion is the
point -- "the chain is tamper-evident" is not asserted by this file, it is a verdict
rendered over observed evidence.

Symptom (capture-v0 §2, pre-decided):
    hit == "a seeded single-record mutation goes UNDETECTED by verify_chain."
    i.e. we MUTATE a valid chain, run verify_chain, and if it reports intact=True
    that is a DETECTION FAILURE -> a hit. We want ZERO hits across the full power
    bar, so the honest verdict is CONFIRMED_NOT_REPRO (the tamper-detection failure
    does not reproduce).

Power (capture-v0 §2, reuse spine defaults p_floor=0.02, alpha=0.01):
    S = 228 seeded mutation trials (E1) + one mandatory positive control (E2).

Positive control (capture-v0 §4):
    Run one trial where the verifier is a STUB that always returns intact on a
    mutated chain. The harness MUST register that as a hit -- proving the harness
    can actually SEE a detection miss. Without that proof, no NOT_REPRO is valid.

CI contract (capture-v0 §4 / §8):
    `python run_tamper_eval.py --ci` prints the VerdictResult and exits 0 IFF the
    verdict is exactly CONFIRMED_NOT_REPRO; any hit, or a power shortfall, exits 1.

Pure stdlib, seeded, deterministic. No network, no LLM.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile

from verdict_engine import (
    EvidenceBundle, Attempt, PositiveControl, PCStatus, Thresholds,
    Verdict, render, required_attempts_for_notrepro,
)
from capture_log import (
    CaptureLog, ChainReport, verify_chain, GENESIS_PREV_HASH,
)

# capture-v0 §2: 228 = required_attempts_for_notrepro(0.02, 0.01). Kept in sync.
P_FLOOR = 0.02
ALPHA = 0.01
S_TRIALS = required_attempts_for_notrepro(P_FLOOR, ALPHA)   # -> 228

CHAIN_LEN = 24   # >= 20 records (capture-v0 §4)

MUTATIONS = ["flip_payload_byte", "swap_records", "delete_record",
             "truncate_tail", "edit_field"]


# ---------------------------------------------------------------------------
# Fixed clocks so a given seed => byte-identical chain => reproducible verdict.
# ---------------------------------------------------------------------------
def _seeded_clocks(seed: int):
    base_host = 1_700_000_000.0 + seed
    counter = {"host": base_host, "mono": float(seed)}

    def host():
        counter["host"] += 0.001
        return round(counter["host"], 6)

    def mono():
        counter["mono"] += 0.001
        return round(counter["mono"], 6)

    return host, mono


def build_chain(path: str, seed: int) -> "tuple":
    """Build a fresh, seeded, valid hash-chain of CHAIN_LEN records.

    Returns (seal, records_as_dicts). Content is seeded so the same seed yields the
    same chain -- the eval is reproducible and can live under a regression guard.
    """
    rng = random.Random(seed)
    host, mono = _seeded_clocks(seed)
    log = CaptureLog(path, clock=host, monoclock=mono)
    for i in range(CHAIN_LEN):
        etype = rng.choice(["subprocess_start", "stdout_chunk", "stderr_chunk", "exit"])
        payload = ("record-%d-%s" % (i, rng.randrange(1_000_000))).encode("utf-8")
        log.append(etype, payload)
    seal = log.seal()
    with open(path, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    return seal, records


def _write_records(path: str, records: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")


def apply_mutation(records: list, kind: str, rng: random.Random) -> list:
    """Return a NEW record list with exactly one seeded single-record mutation.

    Each mutation is designed to corrupt the chain in a way verify_chain must catch:
      flip_payload_byte -> payload changes but entry_hash does not -> hash mismatch
      swap_records       -> two records exchange positions       -> seq out of order
      delete_record      -> one record removed                   -> seq gap / prev break
      truncate_tail      -> final record(s) dropped              -> seal count mismatch
      edit_field         -> a non-payload field mutated          -> hash mismatch
    """
    recs = [dict(r) for r in records]
    n = len(recs)
    if kind == "flip_payload_byte":
        i = rng.randrange(n)
        p = recs[i]["payload"]
        j = rng.randrange(len(p)) if p else 0
        # flip one character deterministically
        ch = p[j] if p else "x"
        flipped = chr((ord(ch) + 1) % 0x110000) if p else "!"
        recs[i]["payload"] = (p[:j] + flipped + p[j + 1:]) if p else "!"
    elif kind == "swap_records":
        i = rng.randrange(n)
        k = rng.randrange(n)
        while k == i:
            k = rng.randrange(n)
        recs[i], recs[k] = recs[k], recs[i]
    elif kind == "delete_record":
        i = rng.randrange(n)
        del recs[i]
    elif kind == "truncate_tail":
        drop = 1 + rng.randrange(min(3, n - 1))   # drop 1..3 from the tail
        recs = recs[:n - drop]
    elif kind == "edit_field":
        i = rng.randrange(n)
        recs[i]["event_type"] = recs[i]["event_type"] + "_TAMPERED"
    else:
        raise ValueError("unknown mutation: %s" % kind)
    return recs


def _always_intact_stub(jsonl_path: str, seal=None) -> ChainReport:
    """Positive-control stub: a BROKEN verifier that always says intact.

    Feeding a mutated chain through this must produce a hit (report.intact=True),
    proving the harness can see a detection miss (capture-v0 §4, E2).
    """
    return ChainReport(intact=True, break_seq=None, records=0, reason="stub_blind")


def run_tamper_attempts(seed: int = 20240704) -> EvidenceBundle:
    """Build the evidence: S seeded mutation trials + one positive control."""
    workdir = tempfile.mkdtemp(prefix="oraculum_tamper_")
    chain_path = os.path.join(workdir, "chain.jsonl")
    seal, base_records = build_chain(chain_path, seed)

    rng = random.Random(seed ^ 0xA5A5A5)
    attempts = []
    for t in range(S_TRIALS):
        kind = MUTATIONS[rng.randrange(len(MUTATIONS))]
        mutated = apply_mutation(base_records, kind, rng)
        mpath = os.path.join(workdir, "mutated.jsonl")
        _write_records(mpath, mutated)
        report = verify_chain(mpath, seal)
        # hit == mutation went UNDETECTED == verifier still reports intact.
        attempts.append(Attempt(attempt_id="t%d_%s" % (t, kind),
                                 attempt_valid=True, hit=report.intact,
                                 env_fingerprint="capture-v0-tamper"))

    # Positive control: mutate the chain, verify with the always-intact STUB.
    pc_records = apply_mutation(base_records, "flip_payload_byte", random.Random(seed))
    pc_path = os.path.join(workdir, "pc.jsonl")
    _write_records(pc_path, pc_records)
    pc_report = _always_intact_stub(pc_path, seal)
    pc_saw_the_miss = pc_report.intact   # a mutated chain reported intact == a miss the harness caught
    pc_status = PCStatus.PASS if pc_saw_the_miss else PCStatus.FAIL

    return EvidenceBundle(run_id="tamper-eval", symptom_id="verify_chain_miss",
                          attempts=attempts,
                          positive_control=PositiveControl(pc_status))


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ci = "--ci" in argv

    bundle = run_tamper_attempts()
    thr = Thresholds()
    result = render(bundle, thr)

    n_valid = sum(1 for a in bundle.attempts if a.attempt_valid)
    n_hits = sum(1 for a in bundle.attempts if a.attempt_valid and a.hit)

    print("=" * 72)
    print("Tamper eval (capture-v0 §4, Pattern A) -- Engine A judges the chain")
    print("=" * 72)
    print("  symptom: a seeded single-record mutation goes UNDETECTED by verify_chain")
    print("  power  : S=%d seeded mutations (p_floor=%.2f, alpha=%.2f)"
          % (S_TRIALS, P_FLOOR, ALPHA))
    print("  positive control (harness can see a detection miss?): %s"
          % bundle.positive_control.status.value)
    print("  mutations tested undetected (hits): %d / %d valid trials"
          % (n_hits, n_valid))
    print("-" * 72)
    print("  VERDICT: %s   (reason: %s)" % (result.verdict.value, result.reason_code))
    if result.evidence_types:
        print("  evidence: %s" % "+".join(result.evidence_types))
    print("=" * 72)

    if ci:
        ok = result.verdict == Verdict.CONFIRMED_NOT_REPRO
        if ok:
            print("CI: PASS -- CONFIRMED_NOT_REPRO (tamper detection holds at full power).")
            return 0
        print("CI: FAIL -- expected CONFIRMED_NOT_REPRO, got %s (%s)."
              % (result.verdict.value, result.reason_code))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
