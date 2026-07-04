"""
golden_kappa.py -- the calibration measure's own eval seed.

Cohen's κ is the gate on "may we trust this judge?" — so it, too, must not silently
regress. These are hand-checked (judge_labels, human_labels) -> expected κ band /
value. Running cohen_kappa(fixture) == expected guards the fuzzy prerequisite the
same way golden_fixtures guards the Verdict Engine.
"""
from kappa import cohen_kappa

P, F = "P", "F"

# each entry: (name, judge_labels, human_labels, expected_kappa, expected_band)
GOLDEN_KAPPA = [
    ("perfect_agreement",  [P, F, P, F], [P, F, P, F], 1.0,   "almost perfect"),
    ("total_disagreement", [P, P, F, F], [F, F, P, P], -1.0,  "poor (worse than chance)"),
    # 4/5 raw agreement, but chance-corrected only "substantial" — the teaching case:
    ("high_raw_but_substantial", [P, P, P, F, F], [P, P, F, F, F], 0.6154, "substantial"),
    # empty -> honest 0.0 (insufficient, not agreement). Note κ=0.0 reads as "slight",
    # which is exactly right: nothing observed is NOT evidence of a good judge.
    ("empty_is_zero", [], [], 0.0, "slight"),
]


def check():
    ok = 0
    rows = []
    for name, jl, hl, exp_k, exp_band in GOLDEN_KAPPA:
        r = cohen_kappa(jl, hl)
        passed = abs(r.kappa - exp_k) < 1e-3 and r.band == exp_band
        ok += int(passed)
        rows.append((passed, name, r.kappa, r.band, exp_k))
    return ok, rows
