"""
run_demo.py -- see the Verdict Engine work.  Pure stdlib:  python run_demo.py
"""
from verdict_engine import (
    EvidenceBundle, PositiveControl, PCStatus, Thresholds,
    render, required_attempts_for_notrepro,
)
from example_exception_oracle import ExceptionSymptom, evaluate_attempt
from recall_runner import run_recall_attempts, positive_control_selfcheck
from golden_fixtures import GOLDEN

thr = Thresholds()


def part_a_end_to_end():
    print("=" * 72)
    print("A. One real case, end-to-end through the (swappable) exception oracle")
    print("=" * 72)
    symptom = ExceptionSymptom(expected_exception="NullReferenceException")
    # raw signals captured from 5 repro attempts (capturing them is the plugin's job)
    raw_runs = [{"exception_type": "NullReferenceException"} for _ in range(5)]
    attempts = [evaluate_attempt(f"a{i}", sig, symptom) for i, sig in enumerate(raw_runs)]
    bundle = EvidenceBundle("live-1", "null-deref", attempts, PositiveControl(PCStatus.PASS))
    r = render(bundle, thr)
    print(f"  raw signals -> oracle -> {len(attempts)} attempts -> render()")
    print(f"  VERDICT: {r.verdict.value} / {r.sub_state.value if r.sub_state else '-'}"
          f"   (reason: {r.reason_code})\n")


def part_a2_recall_plugin():
    print("=" * 72)
    print("A2. A SECOND, different SignalSource (recruiting recall) -- SAME spine")
    print("=" * 72)
    print(f"  positive control (harness can see an injected recall miss?): "
          f"{positive_control_selfcheck().value}")
    print("  symptom = 'golden candidate 李泽明 (SEED-0004) falls out of recall top-3'")
    print("  driven by a SEEDED, reproducible jitter sim over a static CGL snapshot\n")
    scenarios = [
        ("always recalled  (prob=1.0, S=228)", dict(s=228, base_rank_prob=1.0, seed=3)),
        ("ranking jitter    (prob=0.8, S=50)", dict(s=50, base_rank_prob=0.8, seed=2)),
        ("always missed     (prob=0.0, S=6)",  dict(s=6, base_rank_prob=0.0, seed=1)),
    ]
    for label, kw in scenarios:
        b = run_recall_attempts("future-mobility-head-of-ml", "SEED-0004", top_k=3, **kw)
        r = render(b, thr)
        sub = f"/{r.sub_state.value}" if r.sub_state else ""
        extra = ""
        if r.hit_rate is not None and r.sub_state and r.sub_state.value == "FLAKY":
            extra = f"  p̂={r.hit_rate:.2f}, fix needs {r.s_fix_required} stress runs"
        if r.evidence_types:
            extra = f"  [{'+'.join(r.evidence_types)}]"
        print(f"  {label:38} -> {r.verdict.value}{sub}{extra}")
    print()


def part_b_table():
    print("=" * 72)
    print("B. Render every golden fixture (the whole decision tree, one row each)")
    print("=" * 72)
    print(f"  {'fixture':<26}{'verdict':<22}{'sub':<8}reason")
    print("  " + "-" * 68)
    for name, bundle, _, _ in GOLDEN:
        r = render(bundle, thr)
        sub = r.sub_state.value if r.sub_state else "-"
        extra = ""
        if r.s_fix_required:
            extra = f"   [fix needs {r.s_fix_required} stress runs]"
        if r.evidence_types:
            extra = f"   [{'+'.join(r.evidence_types)}]"
        print(f"  {name:<26}{r.verdict.value:<22}{sub:<8}{r.reason_code}{extra}")
    print()


def part_c_guard():
    print("=" * 72)
    print("C. The judge's own eval seed: render(fixture) == expected?  (recursion)")
    print("=" * 72)
    ok = 0
    for name, bundle, exp_v, exp_sub in GOLDEN:
        r = render(bundle, thr)
        passed = (r.verdict == exp_v) and (exp_sub is None or r.sub_state == exp_sub)
        ok += int(passed)
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print(f"\n  {ok}/{len(GOLDEN)} golden verdicts match -- this is the first regression guard.\n")


def part_d_tangible():
    print("=" * 72)
    print("D. What the thresholds actually cost you (make the abstract concrete)")
    print("=" * 72)
    s = required_attempts_for_notrepro(thr.p_floor, thr.alpha)
    print(f"  p_floor={thr.p_floor}, alpha={thr.alpha}"
          f"  ->  you must see {s} CLEAN attempts before you may declare NOT_REPRO.")
    print(f"  'ran it once, didn't see it' is {s}x short of the bar.\n")


if __name__ == "__main__":
    part_a_end_to_end()
    part_a2_recall_plugin()
    part_b_table()
    part_c_guard()
    part_d_tangible()
