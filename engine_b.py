"""
engine_b.py -- Engine B end-to-end: PRD text -> Eval Readiness Report.

Wires the thin LLM adapter (propose) to the deterministic gate (judge) to the
report (render). One entry point:

    run_readiness(prd_text, proposer) -> ReadinessReport

The proposer is injected. Default is the Fake one (no model, reproducible), so the
whole pipeline runs and is testable offline. Pass a real AnthropicProposer (via
get_proposer(api_key=...)) to classify arbitrary PRD text with a live model. Either
way, the VERDICT is the gate's -- the model only proposes.
"""
from __future__ import annotations

from llm_adapter import ProposerClient, FakeProposer, proposal_to_targets
from report import build_report, ReadinessReport


def run_readiness(prd_text: str, proposer: ProposerClient | None = None) -> ReadinessReport:
    """PRD text -> propose targets -> gate each -> assembled readiness report."""
    proposer = proposer or FakeProposer()
    proposal = proposer.propose(prd_text)          # LLM (or stub) PROPOSES
    targets = proposal_to_targets(proposal)        # validated -> Target[]
    return build_report(targets)                   # deterministic gate JUDGES
