"""
report.py -- the Eval Readiness Report (TDD section 4.4).

Engine B's OUTPUT: not eval code, but a verdict on whether an honest eval is even
possible for each target, with the questions you must answer and the reasons why
(the credibility pillar shows up here -- every BLOCK cites *why*).

The overall gate line says how many targets are READY / NEEDS_INPUT / BLOCKED, and
withholds the scaffold whenever any target is not READY. Generation of a harness
stub is deferred (TDD 4.5) until every target is READY -- refusing to generate is
the moat.

Pure rendering over structured GateResults. No LLM. Input is a Target[] (hand-written
here; an LLM adapter would produce them in TDD step 6).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from oracle_taxonomy import Target
from readiness_gate import gate, GateResult, Status


@dataclass
class ReadinessReport:
    results: list = field(default_factory=list)  # GateResult[]

    @property
    def counts(self) -> dict:
        c = {Status.READY: 0, Status.NEEDS_INPUT: 0, Status.BLOCKED: 0}
        for r in self.results:
            c[r.status] += 1
        return c

    @property
    def all_ready(self) -> bool:
        return all(r.status == Status.READY for r in self.results) and bool(self.results)

    @property
    def scaffold_withheld(self) -> bool:
        """The scaffold is only emitted when EVERY target is READY (TDD 4.5)."""
        return not self.all_ready


def build_report(targets: list) -> ReadinessReport:
    """Judge every target through the deterministic gate."""
    return ReadinessReport(results=[gate(t) for t in targets])


def render_report(report: ReadinessReport) -> str:
    """Human-readable render. The explanations are the point -- they cite *why*."""
    c = report.counts
    lines = []
    lines.append("=" * 72)
    lines.append("EVAL READINESS REPORT")
    lines.append("=" * 72)
    lines.append(f"  {c[Status.READY]} READY   {c[Status.NEEDS_INPUT]} NEEDS_INPUT   "
                 f"{c[Status.BLOCKED]} BLOCKED")
    if report.scaffold_withheld:
        lines.append("  ⛔ SCAFFOLD WITHHELD — not every target has an honest oracle yet.")
    else:
        lines.append("  ✅ All targets READY — scaffold may be generated.")
    lines.append("")

    for r in report.results:
        icon = {"READY": "✅", "NEEDS_INPUT": "❓", "BLOCKED": "⛔"}[r.status.value]
        lines.append(f"  {icon} [{r.status.value}] {r.target}")
        lines.append(f"        oracle_class: {r.oracle_class.value}   "
                     f"(rule: {r.reason_code})")
        if r.blocked_on:
            lines.append(f"        blocked_on:   {', '.join(r.blocked_on)}")
        lines.append(f"        why:  {r.why}")
        if r.status == Status.BLOCKED:
            lines.append("        agent risk:   if you let an agent iterate against "
                         "this, it will converge on a fake signal — 'passing' a "
                         "hallucination. Fix the prerequisite first, THEN let it iterate.")
        for q in r.forced_questions:
            lines.append(f"        ▸ forced question: {q}")
        lines.append("")

    lines.extend(_next_steps(report))
    return "\n".join(lines)


def _next_steps(report: ReadinessReport) -> list:
    """Point the report at the next action in the loop — a readiness report is a
    node in the develop -> check -> scaffold -> iterate cycle, not a dead end."""
    c = report.counts
    out = ["-" * 72, "NEXT"]
    if c[Status.READY]:
        out.append(f"  → {c[Status.READY]} READY: run `oraculum scaffold` to emit the "
                    "honest verifier your agent iterates against (agentic coding loop).")
    if c[Status.NEEDS_INPUT]:
        out.append(f"  → {c[Status.NEEDS_INPUT]} NEEDS_INPUT: supply the named prerequisite, "
                   "then re-run `oraculum check`.")
    if c[Status.BLOCKED]:
        out.append(f"  → {c[Status.BLOCKED]} BLOCKED: this is your developer-feedback-loop "
                   "to-do — build the missing golden set / downstream signal, update the "
                   "spec, and check again. Don't hand these to an agent yet.")
    if report.all_ready:
        out.append("  → All targets READY — the whole spec has an honest eval. Scaffold "
                   "and let the agent iterate.")
    out.append("")
    return out
