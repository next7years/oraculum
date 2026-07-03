"""
recall_runner.py -- a SELF-CONTAINED, SEEDED, reproducible stress runner for the
recall symptom. Pure stdlib. No network, no LLM, no CGL import.

WHY SEEDED (not a real LLM):
    A real retrieval/ranking system jitters run-to-run (embedding noise, LLM judge
    sampling at temperature>0), which is exactly what makes a golden candidate flap
    in and out of the top-k -- the FLAKY case. But if we drove this example with a
    real LLM, every run would differ and we could NOT put it under a golden
    regression guard (render(fixture) == expected would flake). That would violate
    Oraculum's core discipline: the JUDGE must be reproducible and its verdicts
    deterministically auditable.

    The Verdict Engine consumes a boolean hit/miss series -- it does not care whether
    that series came from a real LLM or a seeded generator. So we MODEL the real
    jitter with a seeded simulator: same seed => same attempt series => reproducible
    verdict, and it can live in golden_fixtures. Real-API jitter belongs to the later
    fuzzy-oracle stage, where the jitter IS the object under judgment.

SCENARIO KNOB (base_rank_prob = probability the golden candidate lands in top-k):
    base_rank_prob = 1.0  -> golden always recalled      -> H == 0  -> ... NOT_REPRO burden
    base_rank_prob ~ 0.8  -> occasionally missed         -> flaky misses -> FLAKY
    base_rank_prob = 0.0  -> golden never recalled       -> H == n_valid -> STABLE (symptom always repros)
    (Recall: hit == the golden candidate MISSED the top-k, i.e. the symptom reproduced.)
"""
from __future__ import annotations

import json
import os
import random

from verdict_engine import EvidenceBundle, PositiveControl, PCStatus
from recall_oracle import RecallSymptom, evaluate_attempt

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recall_data")


def load_snapshot():
    """Load the self-contained candidate pool + golden cases (static CGL snapshot)."""
    with open(os.path.join(_DATA_DIR, "candidates.json"), encoding="utf-8") as f:
        candidates = json.load(f)
    with open(os.path.join(_DATA_DIR, "recall_golds.json"), encoding="utf-8") as f:
        golds = json.load(f)
    return candidates, golds


class SeededRecallSimulator:
    """Models a jittering recall system reproducibly.

    Each attempt, the golden candidate lands in the top-k with prob=base_rank_prob;
    the rest of the top-k is filled from the distractor pool. Same seed => same runs.
    This stands in for 'run CGL recall N times and watch the ranking jitter', without
    any external dependency.
    """

    def __init__(self, candidates: list, golden_id: str, top_k: int,
                 base_rank_prob: float, seed: int = 1234):
        self.top_k = top_k
        self.golden_id = golden_id
        self.base_rank_prob = base_rank_prob
        self._rng = random.Random(seed)
        self._distractors = [c["candidate_id"] for c in candidates
                             if c["candidate_id"] != golden_id]

    def recall_once(self) -> list:
        """Return one ranked list of candidate ids (top of list = rank 1)."""
        pool = list(self._distractors)
        self._rng.shuffle(pool)
        if self._rng.random() < self.base_rank_prob:
            # golden lands somewhere inside the top-k
            slot = self._rng.randrange(self.top_k)
            ranked = pool[:slot] + [self.golden_id] + pool[slot:]
        else:
            # golden pushed out beyond the top-k (the recall MISS)
            ranked = pool[:self.top_k] + [self.golden_id] + pool[self.top_k:]
        return ranked


def run_recall_attempts(query_id: str,
                        golden_id: str,
                        s: int,
                        top_k: int = 5,
                        base_rank_prob: float = 0.8,
                        seed: int = 1234,
                        positive_control: PCStatus = PCStatus.PASS,
                        invalid_every: int = 0,
                        env: str = "recall-sim:local-embed/temp0") -> EvidenceBundle:
    """Run S seeded recall attempts and package them as an EvidenceBundle.

    positive_control: models the mandatory harness-not-blind check (section 2.0). We
    default to PASS -- we separately verify (in the demo) that the harness CAN see a
    recall miss by injecting a known one. That gate is decoupled from whether the
    (snapshot) golden labels themselves are biased.

    invalid_every>0: mark every k-th attempt attempt_valid=False, to model a recall
    call that errored before the observation point (couldn't-look, not absent).
    """
    candidates, _ = load_snapshot()
    symptom = RecallSymptom(query_id=query_id, golden_candidate_id=golden_id, top_k=top_k)
    sim = SeededRecallSimulator(candidates, golden_id, top_k, base_rank_prob, seed=seed)

    attempts = []
    for i in range(s):
        observed = not (invalid_every and (i + 1) % invalid_every == 0)
        ranked = sim.recall_once() if observed else []
        attempts.append(evaluate_attempt(f"a{i}", ranked, symptom, observed=observed, env=env))

    return EvidenceBundle(run_id=f"recall-{query_id}", symptom_id=query_id,
                          attempts=attempts,
                          positive_control=PositiveControl(positive_control))


def positive_control_selfcheck(top_k: int = 3, seed: int = 7) -> PCStatus:
    """Inject a KNOWN recall miss and confirm the oracle flags it as a hit.
    This is the harness-not-blind proof: if we can't even see a deliberately
    injected miss, no NOT_REPRO verdict may be trusted (section 2.0 / E2).
    """
    candidates, _ = load_snapshot()
    golden_id = candidates[0]["candidate_id"]
    symptom = RecallSymptom(query_id="pc", golden_candidate_id=golden_id, top_k=top_k)
    # a ranking that deliberately omits the golden from the top-k
    ranked = [c["candidate_id"] for c in candidates if c["candidate_id"] != golden_id]
    ranked = ranked[:top_k] + [golden_id]
    att = evaluate_attempt("pc0", ranked, symptom)
    return PCStatus.PASS if att.hit else PCStatus.FAIL
