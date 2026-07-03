"""
run_engine_b_demo.py -- see Engine B (interrogation gate + thin LLM adapter) work.

    python run_engine_b_demo.py

Shows:
  A. A PRD paragraph -> adapter PROPOSES targets -> gate JUDGES -> readiness report
     (some READY, some BLOCKED with a cited reason + forced questions, scaffold
     withheld). The adapter here is a deterministic stub (no live model, so it's
     reproducible); pass a real proposer to classify arbitrary PRD text.
  B. Engine B's own eval seed: gate(golden_prd).status == expected (the recursion).
  C. The adapter fails LOUD on malformed model output (never silently accepts text).

Pure stdlib. No network. No LLM required.
"""
from oracle_taxonomy import OracleClass, HAS_GOLDEN_SET, HAS_REFERENCE, HAS_DOWNSTREAM_SIGNAL
from readiness_gate import gate
from report import render_report
from golden_prds import GOLDEN_PRDS
from llm_adapter import FakeProposer, AdapterError
from engine_b import run_readiness


# A realistic PRD paragraph an engineer might paste in.
SAMPLE_PRD = """\
Recruiting Copilot v1. For a hiring brief, the system must:
(1) recall the right shortlist -- a known-good candidate must appear in the top-k;
(2) rewrite the job description in a compelling, on-brand voice;
(3) predict which sourced candidate will ultimately accept an offer.
We have a labeled golden set of past shortlists for (1)."""


# The DETERMINISTIC STUB standing in for the LLM adapter (TDD 6: mock the model with
# recorded input -> schema-valid output). A real AnthropicProposer would produce this
# same shape from SAMPLE_PRD; here we hard-wire it so the demo is reproducible.
def _stub_handler(prd_text: str) -> dict:
    return {"targets": [
        {"target": "known-good candidate appears in recall top-k",
         "proposed_oracle_class": OracleClass.CHECKABLE_WITH_REFERENCE.value,
         "rationale": "recall@k against the labeled golden set of past shortlists",
         "detected_prerequisites": {HAS_REFERENCE: True, HAS_GOLDEN_SET: True}},
        {"target": "rewritten JD reads in a compelling, on-brand voice",
         "proposed_oracle_class": OracleClass.FUZZY_JUDGE.value,
         "rationale": "voice/brand fit is expert judgment; no calibration set given",
         "detected_prerequisites": {HAS_GOLDEN_SET: False}},
        {"target": "sourced candidate ultimately accepts an offer",
         "proposed_oracle_class": OracleClass.DOWNSTREAM_ONLY.value,
         "rationale": "only a real-world offer-accept outcome confirms it",
         "detected_prerequisites": {HAS_DOWNSTREAM_SIGNAL: False}},
    ]}


def part_a_end_to_end():
    print("PRD in:\n  " + SAMPLE_PRD.replace("\n", "\n  ") + "\n")
    # text -> propose (stub LLM) -> gate (deterministic) -> report
    report = run_readiness(SAMPLE_PRD, proposer=FakeProposer(handler=_stub_handler))
    print(render_report(report))


def part_b_guard():
    print("=" * 72)
    print("Engine B's own eval seed: gate(golden_prd).status == expected?  (recursion)")
    print("=" * 72)
    ok = 0
    for name, target, expected in GOLDEN_PRDS:
        r = gate(target)
        passed = r.status == expected
        ok += int(passed)
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:34} "
              f"-> {r.status.value} (expected {expected.value})")
    print(f"\n  {ok}/{len(GOLDEN_PRDS)} gating verdicts match -- "
          f"the interrogation gate can't silently regress either.\n")


def part_c_fail_loud():
    print("=" * 72)
    print("Adapter fails LOUD on bad model output (never silently accepts free text)")
    print("=" * 72)
    bad_cases = {
        "class not in taxonomy": {"targets": [
            {"target": "x", "proposed_oracle_class": "VIBES", "rationale": "r",
             "detected_prerequisites": {}}]},
        "missing required field": {"targets": [
            {"target": "x", "rationale": "r", "detected_prerequisites": {}}]},
        "not an object": ["nope"],
    }
    for label, payload in bad_cases.items():
        try:
            run_readiness("prd", proposer=FakeProposer(handler=lambda _p, o=payload: o))
            print(f"  [FAIL] {label}: accepted bad output (should have raised)")
        except AdapterError as e:
            print(f"  [PASS] {label}: raised AdapterError")
    print()


if __name__ == "__main__":
    part_a_end_to_end()
    part_b_guard()
    part_c_fail_loud()
