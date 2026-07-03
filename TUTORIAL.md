# Oraculum Tutorial — from your AI feature to a self-guarding eval

This is the hands-on guide. The other docs (`PRD.md`, `TDD.md`, the spec) explain
*why* Oraculum works the way it does. This one just shows you *how to use it on
your own product*, start to finish, with a real recruiting example.

If you have 30 seconds: run `python run_demo.py` and `python run_engine_b_demo.py`,
then come back here.

---

## The one idea to hold onto

> **A model may *act and propose*. A deterministic, inspectable rule set *judges*.
> The verdict never lives inside a model.**

That single rule shows up twice:

- **Engine A** — your plugin *observes* a signal; the spine *judges* whether a bug
  reproduced. (`render(evidence) → verdict`)
- **Engine B** — an LLM *proposes* what kind of oracle each target is; a
  deterministic gate *decides* READY / BLOCKED.

Everything below is just those two engines, used in order.

---

## Step 0 — What you need

- Python 3.9+ (the core is stdlib-only; no install).
- A plain-text description of your feature (a PRD paragraph is enough).
- For `check`/`scaffold`: an Anthropic API key in `ORACULUM_ANTHROPIC_API_KEY`
  (or `CGL_`/`ANTHROPIC_API_KEY`). The key is only used by the thin adapter that
  reads your PRD; the judgment itself is deterministic.

---

## Step 1 — Ask the real question first: *can I even build an honest eval?*

Don't start by writing eval code. Start by interrogating your PRD.

Write your feature as text, e.g. `my_prd.md`:

```
Recruiting Copilot v1. For a hiring brief, the system must:
(1) recall the right shortlist -- a known-good candidate must appear in the top-k;
(2) rewrite the job description in a compelling, on-brand voice;
(3) predict which sourced candidate will ultimately accept an offer.
We have a labeled golden set of past shortlists for (1).
```

Run the gate:

```
oraculum check my_prd.md
```

You get a readiness report. For the PRD above it says:

| Your feature | Verdict | Why |
|---|---|---|
| (1) known-good candidate in recall top-k | ✅ **READY** | checkable predicate + you have a golden set |
| (2) JD "reads in a compelling, on-brand voice" | ⛔ **BLOCKED** | fuzzy judgment, **no calibration set** → an uncalibrated judge is theater |
| (3) candidate "ultimately accepts an offer" | ⛔ **BLOCKED** | correctness is only knowable downstream; no signal wired → Goodhart trap |

It also prints, for each blocked target, the **forced question** you didn't know
to ask, and cites *why* it's blocked. And it **exits non-zero** if anything is
blocked — so you can drop this into CI exactly like a linter:

| exit | meaning |
|---|---|
| `0` | all targets READY |
| `1` | some NEEDS_INPUT, none BLOCKED |
| `2` | at least one BLOCKED |

> **What you just learned:** two of your three "evals" would have been fake. That's
> the whole point — you found out *before* writing them.

---

## Step 2 — For the READY targets, generate a harness

Only target (1) is READY. Generate its harness stub:

```
oraculum scaffold my_prd.md --out my_harness
```

For each READY target you get three files (blocked targets are reported and
**withheld** — refusing to scaffold theater is deliberate):

```
my_harness/
  <target>_oracle.py     # SymptomSpec + evaluate_attempt(): the ONE predicate you write
  <target>_runner.py     # the attempt loop + a positive-control slot
  <target>_fixtures.py   # hand-labeled cases = this harness's own regression guard
```

These import the real `verdict_engine` spine. They run today — they just have
`TODO`s where your domain plugs in.

---

## Step 3 — Fill in the ONE predicate (this is all you write)

Open `<target>_oracle.py`. The only thing you must define is: **"did the symptom
happen this attempt?"** For recall, the symptom is *"the golden candidate fell out
of the top-k"*. Look at the built example `recall_oracle.py` for the exact shape —
you copy that idea:

```python
def evaluate_attempt(attempt_id, raw_signals, symptom, observed=True, env="default"):
    if not observed:                       # crashed/timed out before we could look
        return Attempt(attempt_id, attempt_valid=False, hit=False, env_fingerprint=env)
    ranked = raw_signals["ranked_candidate_ids"]        # <- from YOUR match() call
    hit = symptom.golden_candidate_id not in ranked[:symptom.top_k]   # recall miss = symptom
    return Attempt(attempt_id, attempt_valid=True, hit=hit, env_fingerprint=env)
```

Then in `<target>_runner.py`, wire `capture_signal_once()` to actually run your
system (call your `match()`, capture the ranking). Everything else — the flaky
math, the statistical bar, the verdict — is reused from the spine. You don't write it.

---

## Step 4 — Get a verdict, and trust it

Now run your harness. The spine renders one of:

- **`CONFIRMED_REPRO / STABLE`** — reproduces every time; a clean regression test.
- **`CONFIRMED_REPRO / FLAKY`** — real but intermittent. It reports the rate `p̂`
  **and** how many stress runs a fix must survive (e.g. "80 runs") to count as fixed.
- **`CONFIRMED_NOT_REPRO`** — genuinely gone. But you only earn this after enough
  clean runs to be statistically sure (with `p_floor=0.02` that's **228 runs** —
  "ran it once, didn't see it" is rejected by construction).
- **`INCONCLUSIVE`** — the harness couldn't conclude (too few attempts, environment
  drift, or the positive control failed). This *routes you to act*, it's not a dump.

> **The positive control matters.** Before any NOT_REPRO verdict, the harness
> injects a *known* instance of the symptom and checks it can see it. If it can't,
> "not reproducible" means nothing — so the spine refuses that verdict.

---

## Step 5 — The harness guards itself

`<target>_fixtures.py` holds hand-labeled cases with known verdicts. Running
`render(fixture) == expected` is the harness's own regression guard — so if you
change a threshold or a rule, you find out immediately whether the *judge itself*
regressed. The judge sits above your pipeline; this fixture set sits above the judge.

Add a fixture for every new situation you care about. That's the recursion that
keeps the eval from quietly rotting.

---

## The whole loop, in five commands

```
# 1. interrogate — can I honestly eval this? (fails CI if anything is theater)
oraculum check my_prd.md

# 2. scaffold — emit harness stubs for the READY targets only
oraculum scaffold my_prd.md --out my_harness

# 3. edit my_harness/<target>_oracle.py   -> fill in the one predicate
# 4. edit my_harness/<target>_runner.py   -> wire capture_signal_once() to your system
# 5. python my_harness/<target>_runner.py -> get a verdict you can trust
```

---

## FAQ

**Q: My whole feature got BLOCKED. Is Oraculum useless for me?**
No — it just told you the truth early. A BLOCK is a to-do, not a dead end: the
report names exactly what's missing (a golden set, a downstream signal). Provide
it and re-run; the target flips to READY.

**Q: What's a "golden set" and why does fuzzy stuff need one?**
A set of examples a human has labeled with the correct answer. For fuzzy targets
(tone, "faithfulness", quality) there's no checkable predicate — the only honest
way to judge is a model calibrated *against human labels* and shown to agree with
them. No golden set → the judge is unvalidated → BLOCKED. (Calibrated fuzzy judging
is on the roadmap; today Oraculum blocks it rather than fake it.)

**Q: Do I need an API key just to try it?**
Only for `check`/`scaffold` (the adapter reads your PRD text). The two demos and
the core engines run with zero dependencies and no key.

**Q: Does the LLM decide my verdicts?**
No. The LLM only *proposes* how to classify each target from your PRD text. Every
actual verdict — READY/BLOCKED in Engine B, the repro verdict in Engine A — is a
deterministic rule you can read in `readiness_gate.py` / `verdict_engine.py`.

**Q: How do I change how strict "not reproducible" is?**
Tune `p_floor` in `Thresholds` (verdict_engine.py). Smaller `p_floor` = you insist
on ruling out rarer flakes = more clean runs required. It's your number to own, not
a hard-coded magic constant.

---

## Where to go deeper

- Read the code in this order: `verdict_engine.py` (the spine + the full decision
  tree, documented inline) → `recall_oracle.py` + `recall_runner.py` →
  `golden_fixtures.py` → `readiness_gate.py` → `cli.py` → `scaffold.py`.
- The `README.md` "Intellectual lineage" section names the research this stands on
  (test-oracle theory, flaky-test quantification, LLM-judge calibration).
