"""
recall_oracle.py -- DOMAIN PLUGIN #2 (a recruiting / candidate-recall signal).

The Verdict Engine spine (verdict_engine.py) does not change when you swap the
signal source. The FIRST example plugin (example_exception_oracle.py) watched an
EXCEPTION-TYPE signal. THIS one watches a RECALL signal from a recruiting/talent
matcher: given a hiring query, the system recalls & ranks candidates (top-k), and
we ask whether a KNOWN-GOOD (golden) candidate FELL OUT of that top-k.

Two different SignalSources through ONE spine == the plugin interface is genuinely
domain-agnostic. Plugins observe; the spine judges.

>>> Framing the recall failure as a reproducible SYMPTOM (this is the key move):
    The Verdict Engine judges "did a symptom reproduce, across N attempts?". So we
    define the symptom as: "the golden candidate is NOT in the recall top-k". Each
    re-run of recall is one Attempt; hit == the golden candidate missed the top-k.
    A retrieval system that jitters (embedding/ranking noise) will hit sometimes
    and miss sometimes -- exactly the FLAKY case the engine is built to judge.

>>> Self-contained by design: the candidate pool + golden labels are STATIC
    SNAPSHOTS under recall_data/ (copied from CGL, not a live import). The jitter
    that drives multi-attempt runs is a SEEDED, reproducible simulator
    (recall_runner.py), NOT a real LLM call -- because the whole point of Oraculum
    is that the judge must be reproducible. Real-LLM jitter is a later, fuzzy-oracle
    concern; here the symptom is a hard, checkable predicate.
"""
from dataclasses import dataclass

from verdict_engine import Attempt


@dataclass
class RecallSymptom:
    """A machine-checkable symptom: 'this golden candidate fell out of top-k'."""
    query_id: str
    golden_candidate_id: str      # the candidate that SHOULD be recalled
    top_k: int = 5


def evaluate_attempt(attempt_id: str,
                     ranked_candidate_ids: list,
                     symptom: RecallSymptom,
                     observed: bool = True,
                     env: str = "default") -> Attempt:
    """Oracle: one recall result (a ranked candidate-id list) -> a structured Attempt.

    observed=False models a recall call that errored / timed out / returned nothing
    BEFORE we could inspect the ranking => attempt_valid=False. That encodes
    "we couldn't look", categorically different from "the golden candidate was present".

    hit == the golden candidate is ABSENT from the top-k (a recall MISS == the
    symptom reproduced). Present in top-k => no symptom this attempt.
    """
    if not observed:
        return Attempt(attempt_id=attempt_id, attempt_valid=False,
                       hit=False, env_fingerprint=env)

    top_k_ids = ranked_candidate_ids[:symptom.top_k]
    hit = symptom.golden_candidate_id not in top_k_ids   # <- the predicate: recall miss
    return Attempt(attempt_id=attempt_id, attempt_valid=True,
                   hit=hit, env_fingerprint=env)
