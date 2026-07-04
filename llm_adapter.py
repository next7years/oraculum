"""
llm_adapter.py -- Engine B's thin LLM adapter (TDD section 4.3).

Exactly ONE job, tightly constrained: PRD text -> a structured list of proposed
targets. It PROPOSES; it does NOT decide. The deterministic gate (readiness_gate.py)
applies the rules. This is the same act-vs-judge split as Engine A -- the model
never holds the verdict.

Design invariants (load-bearing):
  - The adapter lives BEHIND an interface (ProposerClient), so the gate and the
    whole report path are testable WITHOUT a live model (use FakeProposer).
  - Output is constrained to JSON via tool-use and VALIDATED against a schema.
    On parse/validation failure we FAIL LOUD (AdapterError) -- we never silently
    accept free text (TDD 4.3).
  - The Anthropic SDK is an OPTIONAL dependency: it is imported lazily, only inside
    AnthropicProposer, only when a real key is supplied. The spine, the gate, and
    the default (Fake) path stay pure stdlib -- consistent with Oraculum's rule that
    any LLM dependency lives only in the adapter, never in the core.

The class the model may PROPOSE is one of the fixed taxonomy classes -- it cannot
invent new ones (the schema enum enforces this).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from oracle_taxonomy import (
    Target, OracleClass,
    HAS_REFERENCE, HAS_GOLDEN_SET, HAS_DOWNSTREAM_SIGNAL,
)


class AdapterError(RuntimeError):
    """Raised when the model's output can't be parsed/validated. Fail loud."""


# The one constrained output shape. The model fills this; nothing more.
_ALLOWED_CLASSES = [c.value for c in OracleClass]
_ALLOWED_PREREQS = [HAS_REFERENCE, HAS_GOLDEN_SET, HAS_DOWNSTREAM_SIGNAL]

PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["targets"],
    "properties": {
        "targets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["target", "proposed_oracle_class", "rationale",
                             "detected_prerequisites"],
                "properties": {
                    "target": {"type": "string"},
                    "proposed_oracle_class": {"type": "string", "enum": _ALLOWED_CLASSES},
                    "rationale": {"type": "string"},
                    "detected_prerequisites": {
                        "type": "object",
                        "properties": {k: {"type": "boolean"} for k in _ALLOWED_PREREQS},
                    },
                },
            },
        }
    },
}

_SYSTEM = (
    "You classify each evaluation target in a product/feature spec into exactly one "
    "of a FIXED oracle taxonomy. You do NOT decide readiness and you do NOT invent "
    "classes. For each distinct thing the spec claims the system should do, emit one "
    "target with: a short name, the single best-fitting oracle_class from the enum, a "
    "one-line rationale, and which prerequisites are present.\n\n"
    "Oracle classes:\n"
    "- CHECKABLE: a machine-checkable predicate exists (exit code, exact match, threshold).\n"
    "- CHECKABLE_WITH_REFERENCE: checkable only against a reference/ground-truth set "
    "(e.g. recall@k needs a golden set).\n"
    "- FUZZY_JUDGE: correctness is expert judgment (tone, faithfulness, helpfulness).\n"
    "- DOWNSTREAM_ONLY: correctness is only knowable from a real-world downstream "
    "outcome (a hire, a click, a renewal).\n"
    "- UNOBSERVABLE: no signal reaches a harness at all (claims about internal "
    "'understanding' with no emitted signal).\n\n"
    "Prerequisites (set true only if the spec clearly provides it): "
    f"{_ALLOWED_PREREQS}."
)


class ProposerClient(ABC):
    """The narrow contract Engine B uses to reach an LLM (or a stub)."""

    @abstractmethod
    def propose(self, prd_text: str) -> dict[str, Any]:
        """Return a dict conforming to PROPOSAL_SCHEMA. Raise AdapterError on failure."""


class FakeProposer(ProposerClient):
    """Deterministic, no network. Drives the gate/report tests without a live model.

    handler: (prd_text) -> proposal dict. Without a handler, returns an empty target
    list (still schema-valid), so the pipeline runs even absent a business handler.
    """

    def __init__(self, handler: Callable[[str], dict[str, Any]] | None = None) -> None:
        self._handler = handler

    def propose(self, prd_text: str) -> dict[str, Any]:
        raw = self._handler(prd_text) if self._handler else {"targets": []}
        validate_proposal(raw)
        return raw


class AnthropicProposer(ProposerClient):
    """Real Claude API. Forces JSON via tool-use, validates, retries, fails loud.
    The anthropic SDK is imported lazily so this file has no hard dependency on it.
    """

    def __init__(self, api_key: str, model: str = "claude-opus-4-8",
                 max_retries: int = 2) -> None:
        self._api_key = api_key
        self._model = model
        self._max_retries = max_retries
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            import anthropic  # lazy, optional dependency
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def propose(self, prd_text: str) -> dict[str, Any]:
        client = self._ensure_client()
        tool = {"name": "emit", "description": "Emit the classified targets.",
                "input_schema": PROPOSAL_SCHEMA}
        last_err: Exception | None = None
        for _ in range(self._max_retries + 1):
            try:
                # No temperature: forced tool_use already constrains the output, and
                # newer models (e.g. Haiku 4.5) reject `temperature` as deprecated.
                # Omitting it works across model generations.
                resp = client.messages.create(
                    model=self._model,
                    max_tokens=2048,
                    system=_SYSTEM,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": "emit"},
                    messages=[{"role": "user", "content": prd_text}],
                )
                for block in resp.content:
                    if getattr(block, "type", None) == "tool_use":
                        raw = dict(block.input)
                        validate_proposal(raw)
                        return raw
                last_err = AdapterError("no tool_use block in response")
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise AdapterError(f"propose failed after retries: {last_err}")


def validate_proposal(raw: dict[str, Any]) -> None:
    """Minimal, dependency-free schema check. Fail loud on any deviation (TDD 4.3)."""
    if not isinstance(raw, dict) or "targets" not in raw or not isinstance(raw["targets"], list):
        raise AdapterError("proposal must be an object with a 'targets' list")
    for i, t in enumerate(raw["targets"]):
        if not isinstance(t, dict):
            raise AdapterError(f"target[{i}] must be an object")
        for req in ("target", "proposed_oracle_class", "rationale"):
            if req not in t:
                raise AdapterError(f"target[{i}] missing required field '{req}'")
        cls = t["proposed_oracle_class"]
        if cls not in _ALLOWED_CLASSES:
            raise AdapterError(f"target[{i}] proposed_oracle_class '{cls}' not in taxonomy")
        prereqs = t.get("detected_prerequisites", {})
        if not isinstance(prereqs, dict):
            raise AdapterError(f"target[{i}] detected_prerequisites must be an object")


def proposal_to_targets(raw: dict[str, Any]) -> list:
    """Convert a validated proposal into Target[] for the gate. The proposed class
    becomes the Target's oracle_class -- but the VERDICT is still the gate's, not the
    model's."""
    validate_proposal(raw)
    targets = []
    for t in raw["targets"]:
        targets.append(Target(
            target=t["target"],
            oracle_class=OracleClass(t["proposed_oracle_class"]),
            detected_prerequisites={k: bool(v) for k, v in
                                    t.get("detected_prerequisites", {}).items()},
            rationale=t.get("rationale", ""),
        ))
    return targets


def get_proposer(api_key: str = "", handler: Callable | None = None) -> ProposerClient:
    """Select the proposer: a real key -> Anthropic; otherwise -> Fake (default).
    Mirrors CGL's get_llm_client() shape: no key, no network, still runs."""
    if api_key:
        return AnthropicProposer(api_key=api_key)
    return FakeProposer(handler=handler)
