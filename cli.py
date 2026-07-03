"""
cli.py -- a THIN wrapper that embeds Engine B in the dev flow (TDD section 5).

    oraculum check <path-to-prd>      # print an Eval Readiness Report; exit non-zero
                                      # if any target is BLOCKED (composes with CI)

The engines stay pure; this file is only argument parsing, I/O, and exit codes.
No judgment lives here -- it calls run_readiness() (Engine B) and maps the result
to a process exit code so a CI job can gate a merge on it, exactly like a linter
or type-checker.

Exit codes (the CI contract):
    0  all targets READY            -> an honest eval is possible; scaffold may follow
    1  some NEEDS_INPUT, none BLOCKED-> solvable, but a prerequisite is missing
    2  at least one BLOCKED          -> hard fail: the eval would be theater

LLM: with no key we use the deterministic FakeProposer. It only produces targets
if given a handler, so a real `check` on arbitrary PRD text needs a real key
(CGL_/ORACULUM_ANTHROPIC_API_KEY or --api-key). We say so loudly rather than
emit a misleading empty report.
"""
from __future__ import annotations

import argparse
import os
import sys

from engine_b import run_readiness
from llm_adapter import get_proposer, AdapterError
from report import render_report
from readiness_gate import Status
from scaffold import generate, ScaffoldError


def _api_key(explicit: str = "") -> str:
    return (explicit
            or os.environ.get("ORACULUM_ANTHROPIC_API_KEY", "")
            or os.environ.get("CGL_ANTHROPIC_API_KEY", "")
            or os.environ.get("ANTHROPIC_API_KEY", ""))


def _exit_code(report) -> int:
    """Map the readiness verdict to a CI-friendly exit code."""
    c = report.counts
    if c[Status.BLOCKED] > 0:
        return 2
    if c[Status.NEEDS_INPUT] > 0:
        return 1
    return 0


def cmd_check(args) -> int:
    report, err = _load_report(args)
    if report is None:
        return err
    print(render_report(report))
    return _exit_code(report)


def _load_report(args):
    """Shared by check/scaffold: read PRD -> require key -> run Engine B.
    Returns (report, None) on success, or (None, exit_code) on failure."""
    try:
        with open(args.path, encoding="utf-8") as f:
            prd_text = f.read()
    except OSError as e:
        print(f"oraculum: cannot read {args.path!r}: {e}", file=sys.stderr)
        return None, 2

    key = _api_key(args.api_key)
    if not key:
        # No model => the adapter can't extract targets from free text. Be loud.
        print("oraculum: no API key set (ORACULUM_/CGL_/ANTHROPIC_API_KEY) — the "
              "interrogation adapter needs a model to extract targets from PRD text.\n"
              "          Set a key to run a real check. Refusing to emit an empty "
              "report that would look like 'all clear'.", file=sys.stderr)
        return None, 2

    try:
        report = run_readiness(prd_text, proposer=get_proposer(api_key=key))
    except AdapterError as e:
        print(f"oraculum: the model returned malformed output: {e}", file=sys.stderr)
        return None, 2

    return report, None


def cmd_scaffold(args) -> int:
    """Generate harness stubs for the READY targets in a PRD. Non-READY targets
    are reported but skipped -- refusing to scaffold theater is the point."""
    report, err = _load_report(args)
    if report is None:
        return err

    ready = [r for r in report.results if r.status == Status.READY]
    skipped = [r for r in report.results if r.status != Status.READY]

    if not ready:
        print("oraculum: no READY targets — nothing to scaffold. Run `oraculum check` "
              "to see what's blocked and why.", file=sys.stderr)
        for r in skipped:
            print(f"  - [{r.status.value}] {r.target}", file=sys.stderr)
        return _exit_code(report)

    written = []
    for r in ready:
        for fname, contents in generate(r).items():
            path = os.path.join(args.out, fname)
            if os.path.exists(path) and not args.force:
                print(f"oraculum: {path} exists (use --force to overwrite); skipping",
                      file=sys.stderr)
                continue
            os.makedirs(args.out, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(contents)
            written.append(path)

    print(f"Scaffolded {len(ready)} READY target(s) -> {len(written)} file(s) in {args.out}/")
    for p in written:
        print(f"  + {p}")
    for r in skipped:
        print(f"  ~ skipped [{r.status.value}] {r.target} (scaffold withheld until READY)")
    # Success as long as we generated something; skipped non-READY targets are expected.
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oraculum",
        description="Interrogate a PRD for eval-readiness before you generate eval code.")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="print an Eval Readiness Report for a PRD file")
    c.add_argument("path", help="path to a PRD / feature-spec text file")
    c.add_argument("--api-key", default="",
                   help="Anthropic API key (else read from env)")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("scaffold",
                       help="emit Verdict-Engine harness stubs for the READY targets")
    s.add_argument("path", help="path to a PRD / feature-spec text file")
    s.add_argument("--out", default="oraculum_harness",
                   help="output directory for the generated stubs")
    s.add_argument("--force", action="store_true",
                   help="overwrite existing files in --out")
    s.add_argument("--api-key", default="",
                   help="Anthropic API key (else read from env)")
    s.set_defaults(func=cmd_scaffold)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
