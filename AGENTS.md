# AGENTS.md — how an AI agent wires Oraculum into a user's project

You are an AI coding agent (Claude Code, Codex, etc.). A user asked you to set up
**Oraculum** — an honest-eval framework — for their code. This file tells you how.
Follow it exactly. The whole point of Oraculum is honesty, so the one thing you must
NOT do is fake a judgment on the user's behalf (see the STOP rule below).

## What Oraculum is (30-second model)

A model may *act and propose*; deterministic, inspectable rules *judge*. The verdict
never lives in a model. Two engines on one spine:

- **Engine A** (`verdict_engine.py`): `render(EvidenceBundle) -> verdict` for a
  probabilistic repro/regression oracle (STABLE / FLAKY / NOT_REPRO / INCONCLUSIVE).
  It consumes a boolean `hit` series and judges it with statistical rules.
- **Engine B** (`readiness_gate.py` + `oracle_taxonomy.py`): given a target, decides
  whether an honest eval is even possible (READY / NEEDS_INPUT / BLOCKED).

You extend it by writing **one small plugin** that turns the user's real signal into a
per-attempt `hit`. The spine never changes. Study these built plugins as templates —
match their shape:
- `example_exception_oracle.py` — hit from an exception type (toy)
- `recall_oracle.py` — hit from "a golden item fell out of a top-k" (a real retrieval signal)
- `fuzzy_oracle.py` — hit from an LLM judge's PASS/FAIL (fuzzy targets; needs κ calibration)

## 🛑 The STOP rule (non-negotiable)

Oraculum's value is that **judgment is not outsourced to a model.** So when wiring it,
you (the agent) do ALL the boilerplate — create files, imports, glue the user's
function in, run demos — but you must **STOP and ask the user** for every *judgment*,
never guess one:

- **"What counts as a `hit`?"** (the symptom predicate — e.g. "the golden candidate is
  NOT in the top-k", "the exception was raised", "the judge scored FAIL"). This is the
  soul of the eval; it is the user's call, not yours.
- **`p_floor`** (how rare a flake they insist on ruling out → sets the NOT_REPRO bar).
- **κ threshold** for fuzzy targets (how much judge-vs-human agreement they require;
  default 0.6). And **who the human annotators are** — never invent golden labels.
- Which targets they actually care about, when Engine B proposes several.

If you catch yourself about to pick one of these "to be helpful," STOP and ask. A
plausible guess here is exactly the theater Oraculum exists to kill.

## Recipe: wire a new signal source (the common request)

1. **Find the user's signal.** Ask where the thing-to-judge comes from — a function
   they call (e.g. `match(query)`), a log, an HTTP status, an LLM output. Get the
   actual entry point; read it.
2. **Ask the STOP questions** above — at minimum, "what counts as a hit?"
3. **Generate the plugin**, copying the closest built template:
   - Define a `Symptom` dataclass (the machine-checkable spec).
   - Write `evaluate_attempt(...) -> Attempt` that maps one captured signal to a
     `hit` (all required checks passed) and sets `attempt_valid=False` when the system
     crashed/timed out before you could observe (that's "couldn't look", not "absent").
   - Import `Attempt` from `verdict_engine`; do not touch the spine.
4. **Write a small runner** that calls the user's real function N times, builds an
   `EvidenceBundle`, and calls `render(...)`. Include a **positive control** (inject a
   known instance of the symptom and confirm the harness sees it) — without it, no
   NOT_REPRO verdict is valid.
5. **Add golden fixtures** for the cases the user cares about, so `render(fixture) ==
   expected` guards against regressions. Ask the user to confirm the expected verdicts.
6. **Run it** and show the user the verdict, plus the derived numbers (the FLAKY
   `s_fix`, or the NOT_REPRO bar). Explain what they cost.

## Recipe: check a PRD before building an eval (Engine B)

If the user has a PRD/spec and wants to know what's honestly evaluable:
- Run `oraculum check <prd>` (needs an API key for the adapter; see README).
- It returns a readiness report: each target's oracle class, READY/NEEDS_INPUT/BLOCKED,
  the forced questions, and a NEXT step. **Do not override a BLOCKED verdict to be
  helpful** — a blocked target means an honest eval isn't possible yet; relay that and
  the forced question, don't paper over it.
- For READY targets, `oraculum scaffold <prd>` emits harness stubs — then follow the
  "wire a new signal source" recipe to fill them in (with the STOP rule).

## Recipe: fuzzy target (LLM-as-judge)

If the target has no hard answer (tone, quality, faithfulness):
- It needs a **calibrated** judge. Copy `fuzzy_oracle.py`.
- Ask the user for a **golden set** (items humans labeled PASS/FAIL) and **who labeled
  it**. Use ≥2 annotators — compute the human ceiling (`kappa.py`); if the humans
  themselves disagree (κ below the bar), tell the user this target has no trustworthy
  ground truth and should NOT be auto-evaluated. Do not push past that.
- Only if the judge clears the κ bar AND the user opted in is the target READY — and
  the verdict stays stamped `fuzzy`.

## Ground rules

- Core is stdlib-only and deterministic. Any LLM/network dependency lives ONLY in the
  adapter / judge, never in `verdict_engine.py` or golden-test paths.
- Prefer editing a copy of a built plugin over writing from scratch — they encode the
  right shape.
- When done, run `python run_demo.py`, `python run_engine_b_demo.py`, and (if fuzzy)
  `python run_fuzzy_demo.py` to confirm nothing regressed.
- Read `TUTORIAL.md` for the human-facing walkthrough; this file is the agent-facing
  version of the same thing.
