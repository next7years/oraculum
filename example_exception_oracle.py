"""
example_exception_oracle.py -- DOMAIN PLUGIN (swap this per application).

This is the Oracle Layer: it maps raw captured signals + a SymptomSpec into a
per-attempt `hit` that the (domain-agnostic) Verdict Engine can consume.

This particular plugin checks an EXCEPTION-TYPE signal.

>>> OPEN ITEM #1: this is the first SignalSource choice. Swap it for your real
    signal -- e.g. a VR frame-state probe, a log regex, an HTTP status, a metric
    threshold. The spine in verdict_engine.py does NOT change when you swap this.
    That separation is the whole point: plugins observe, the spine judges.
"""
from dataclasses import dataclass

from verdict_engine import Attempt


@dataclass
class ExceptionSymptom:
    """A machine-checkable symptom: 'this exception type was raised'."""
    expected_exception: str          # e.g. "NullReferenceException"


def evaluate_attempt(attempt_id: str,
                     raw_signals: dict,
                     symptom: ExceptionSymptom,
                     observed: bool = True,
                     env: str = "default") -> Attempt:
    """Oracle: raw signals -> a structured Attempt.

    observed=False models an attempt that crashed / timed out BEFORE the
    observation point => attempt_valid=False. That encodes "we couldn't look",
    which is categorically different from "the symptom was absent".
    """
    if not observed:
        return Attempt(attempt_id=attempt_id, attempt_valid=False,
                       hit=False, env_fingerprint=env)

    raised = raw_signals.get("exception_type")
    hit = (raised == symptom.expected_exception)     # <- the predicate
    return Attempt(attempt_id=attempt_id, attempt_valid=True,
                   hit=hit, env_fingerprint=env)
