"""
run_fuzzy_demo.py -- the recruiting fuzzy-gradient demo. Pure stdlib, reproducible.

    python run_fuzzy_demo.py

One recruiting intake call (fuzzy_data/meeting_notes.md), three judgments at rising
fuzziness, each calibrated and gated HONESTLY:

  1. Extract explicitly-stated requirements  -> judge agrees with humans -> READY
  2. Judge the manager's tone / emphasis      -> partial agreement       -> borderline
  3. Infer the UNSPOKEN real need             -> even two experts disagree, so there
                                                 is no trustworthy ground truth      -> BLOCKED

The lesson: Oraculum doesn't claim it can read between the lines. It *measures*
whether that's honestly evaluable — and refuses where it isn't. That refusal, backed
by a κ number, is the product.

Deterministic: a FakeJudge stands in for a real LLM judge (good at extraction, so-so
at tone, basically guessing at inference — which is the honest reality). Swap in
fuzzy_oracle.AnthropicJudge with a key to run it for real (see --real note below).
"""
from __future__ import annotations

import json
import os

from kappa import cohen_kappa
from fuzzy_oracle import FakeJudge, PASS, FAIL
from oracle_taxonomy import Target, OracleClass, HAS_GOLDEN_SET, ALLOW_FUZZY
from readiness_gate import gate, KAPPA_MIN

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fuzzy_data")


def load():
    with open(os.path.join(_DATA, "fuzzy_golds.json"), encoding="utf-8") as f:
        golds = json.load(f)
    with open(os.path.join(_DATA, "meeting_notes.md"), encoding="utf-8") as f:
        notes = f.read()
    return golds, notes


# A DETERMINISTIC stand-in for an LLM judge, tuned to the honest reality of each task:
# it extracts well, reads tone so-so, and effectively guesses at unspoken intent.
# (These per-item verdicts are hard-wired to be reproducible; a real judge would
# produce its own, and the calibration step would then measure ITS agreement.)
_JUDGE_VERDICTS = {
    # extraction: judge matches the human truth exactly (it's near-checkable)
    "extract_stated": [PASS, PASS, PASS, PASS, FAIL, FAIL, PASS, FAIL],
    # tone: judge gets most but slips on the subtle ones
    "judge_tone":     [PASS, PASS, FAIL, FAIL, FAIL, PASS],
    # inference: judge "guesses" — barely better than chance, like the humans themselves
    "infer_unspoken": [PASS, FAIL, PASS, PASS, FAIL, PASS, FAIL],
}


def _judge_for(target_id):
    """Return a FakeJudge that replays the hard-wired verdicts for this target."""
    seq = iter(_JUDGE_VERDICTS[target_id])
    return FakeJudge(handler=lambda _o, _c: next(seq))


def run():
    golds, _notes = load()
    print("=" * 74)
    print("RECRUITING FUZZY DEMO — one intake call, three judgments, honestly gated")
    print("=" * 74)
    print("  source: fuzzy_data/meeting_notes.md  ·  judge calibrated against human golden\n")

    for t in golds["targets"]:
        tid = t["target_id"]
        items = t["items"]

        # human ground truth for this target. For the inference target we have TWO
        # experts; their agreement IS the ceiling — if they don't agree, no judge can
        # be trusted, because there's no stable truth to calibrate against.
        if "human" in items[0]:
            human = [i["human"] for i in items]
            human_ceiling = None
        else:
            human = [i["human_a"] for i in items]        # calibrate against expert A
            hb = [i["human_b"] for i in items]
            human_ceiling = cohen_kappa(human, hb).kappa   # expert-vs-expert κ

        judge = _judge_for(tid)
        judge_labels = [judge.judge("", t["criterion"]) for _ in items]
        k = cohen_kappa(judge_labels, human)

        # build the Target and gate it (opted in, so we exercise the κ bar)
        target = Target(t["name"], OracleClass.FUZZY_JUDGE,
                        detected_prerequisites={HAS_GOLDEN_SET: True, ALLOW_FUZZY: True},
                        measured_kappa=k.kappa)
        r = gate(target)

        print(f"▸ {t['name']}")
        print(f"    fuzziness: {t['fuzziness']}")
        if human_ceiling is not None:
            print(f"    expert-vs-expert κ (the ceiling): {human_ceiling:.2f} "
                  f"— if experts don't agree, no judge can be trusted here")
        print(f"    judge-vs-human κ: {k.kappa:.2f} ({k.band}), bar κ≥{KAPPA_MIN}")
        icon = {"READY": "✅", "NEEDS_INPUT": "❓", "BLOCKED": "⛔"}[r.status.value]
        print(f"    VERDICT: {icon} {r.status.value}  [{'FUZZY' if r.fuzzy else 'hard'}]"
              f"  (rule: {r.reason_code})")
        if r.status.value != "READY":
            print(f"    why: {r.why}")
        print()

    print("-" * 74)
    print("The point: it doesn't pretend to read between the lines. It measures whether")
    print("that's honestly evaluable — and BLOCKS where even humans can't agree. The")
    print("refusal, backed by a κ number, is the product.\n")
    print("Run for real: set ORACULUM_ANTHROPIC_API_KEY and swap FakeJudge ->")
    print("fuzzy_oracle.AnthropicJudge to have Claude judge each item live (non-repro).")


if __name__ == "__main__":
    run()
