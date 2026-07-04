"""
fuzzy_oracle.py -- DOMAIN PLUGIN #3: a fuzzy (LLM-judge) signal source.

Plugins #1 (exception) and #2 (recall) derive `hit` from a HARD predicate. This one
derives it from a JUDGE's binary verdict on a fuzzy target (tone / faithfulness /
helpfulness). The Verdict Engine spine does NOT change -- it still consumes a boolean
`hit` series. Same act-vs-judge split, one more signal source.

BUT fuzzy carries a prerequisite the hard plugins don't: the judge itself must be
CALIBRATED against human ground truth before we trust it. That calibration is
`kappa.py`; this module just (a) turns a judge verdict into an Attempt, and (b)
offers a deterministic, injectable judge so the whole path is reproducible and
testable with NO live model.

>>> v1 scope: BINARY pass/fail judgments (cleanest to calibrate; kappa is binary
    agreement). Score-based judging + threshold calibration is a later version.

>>> The real LLM judge is an OPTIONAL, isolated dependency (AnthropicJudge), mirroring
    llm_adapter.py: lazy import, only when a key is supplied. The spine and the
    default (Fake) path stay pure stdlib. Real-LLM judging is non-reproducible, so it
    never enters the golden-test path.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from verdict_engine import Attempt


PASS = "P"
FAIL = "F"


@dataclass
class FuzzySymptom:
    """A fuzzy, judge-decided symptom, e.g. 'the summary is NOT faithful to source'.
    Framing note: as with the other plugins, `hit` means the SYMPTOM reproduced.
    So if the target is 'output should be faithful', the symptom is 'unfaithful',
    and hit == the judge ruled FAIL (not faithful) this attempt.
    """
    criterion: str                 # human description of what the judge decides
    hit_on: str = FAIL             # which judge verdict counts as the symptom (default: FAIL)


class Judge(ABC):
    """The narrow contract: judge one output -> PASS/FAIL. (A stub or a real LLM.)"""

    @abstractmethod
    def judge(self, output: str, criterion: str) -> str:
        """Return PASS or FAIL for `output` against `criterion`."""


class FakeJudge(Judge):
    """Deterministic, no network. Drives calibration + oracle tests without a model.

    handler: (output, criterion) -> PASS/FAIL. Without a handler, judges PASS iff the
    output does NOT contain any configured 'bad marker' -- a trivial but deterministic
    rule, enough to run the path end to end.
    """

    def __init__(self, handler: Callable[[str, str], str] | None = None,
                 bad_markers: tuple = ()) -> None:
        self._handler = handler
        self._bad = bad_markers

    def judge(self, output: str, criterion: str) -> str:
        if self._handler is not None:
            return self._handler(output, criterion)
        return FAIL if any(m in output for m in self._bad) else PASS


class AnthropicJudge(Judge):
    """Real Claude judge. OPTIONAL: anthropic SDK imported lazily, only with a key.
    Non-reproducible -> never used in golden tests; only for live use."""

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        self._api_key = api_key
        self._model = model
        self._client = None

    def _ensure(self):
        if self._client is None:
            import anthropic  # lazy, optional
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def judge(self, output: str, criterion: str) -> str:
        client = self._ensure()
        tool = {"name": "verdict", "description": "Emit PASS or FAIL.",
                "input_schema": {"type": "object", "required": ["verdict"],
                                 "properties": {"verdict": {"type": "string",
                                                            "enum": [PASS, FAIL]}}}}
        # max_tokens must be large enough to finish the tool-call JSON; too small
        # (e.g. 16) truncates it and the tool_use block comes back with empty input.
        resp = client.messages.create(
            model=self._model, max_tokens=64, temperature=0.0,
            system=("You judge whether an output meets a criterion. Emit PASS if it "
                    "clearly meets it, FAIL otherwise. Binary only."),
            tools=[tool], tool_choice={"type": "tool", "name": "verdict"},
            messages=[{"role": "user",
                       "content": f"CRITERION: {criterion}\n\nOUTPUT:\n{output}"}])
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                v = dict(block.input).get("verdict")
                if v in (PASS, FAIL):
                    return v
        return FAIL  # conservative: if we couldn't read a verdict, don't claim PASS


def evaluate_attempt(attempt_id: str,
                     output: str,
                     symptom: FuzzySymptom,
                     judge: Judge,
                     observed: bool = True,
                     env: str = "default") -> Attempt:
    """Oracle: one output -> the judge's verdict -> a structured Attempt.

    observed=False models "we couldn't get an output to judge" (crash/timeout) =>
    attempt_valid=False. hit == the judge's verdict equals symptom.hit_on.
    """
    if not observed:
        return Attempt(attempt_id=attempt_id, attempt_valid=False,
                       hit=False, env_fingerprint=env)
    verdict = judge.judge(output, symptom.criterion)
    hit = (verdict == symptom.hit_on)
    return Attempt(attempt_id=attempt_id, attempt_valid=True,
                   hit=hit, env_fingerprint=env)


def judge_labels_for_calibration(outputs: list, criterion: str, judge: Judge) -> list:
    """Run the judge over a labeled golden set's outputs -> its PASS/FAIL labels,
    ready to compare against the human labels via kappa.cohen_kappa. This is the
    calibration step: it produces the judge's side of the agreement measurement."""
    return [judge.judge(o, criterion) for o in outputs]
