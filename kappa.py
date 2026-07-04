"""
kappa.py -- judge calibration measure (the fuzzy prerequisite). Pure stdlib.

A fuzzy oracle (an LLM judging tone / faithfulness / quality) is only trustworthy
if it AGREES with human ground truth. This module is that measurement: given the
judge's labels and the human golden labels for the same items, it computes

    cohen_kappa(judge_labels, human_labels) -> KappaResult

Cohen's kappa corrects raw agreement for chance. It is the deterministic, testable
gate on "may we trust this judge at all?" -- the fuzzy analog of a positive control.
The judge itself is under eval; this is how (spec: "an oracle sits above the oracle").

NOTHING here calls an LLM. Judge labels are inputs. Grounds "calibrate the judge
against a golden set before you trust it" (Gu et al. 2024; Zheng et al. 2023).
"""
from __future__ import annotations

from dataclasses import dataclass


# Landis-Koch (1977) interpretation bands for kappa. 0.61-0.80 = "substantial";
# this is the common "trustworthy" threshold the gate defaults to (see Thresholds).
def kappa_band(k: float) -> str:
    if k < 0.0:
        return "poor (worse than chance)"
    if k < 0.20:
        return "slight"
    if k < 0.40:
        return "fair"
    if k < 0.60:
        return "moderate"
    if k < 0.80:
        return "substantial"
    return "almost perfect"


@dataclass
class KappaResult:
    kappa: float                # chance-corrected agreement
    raw_agreement: float        # simple % of items the two agree on
    n: int                      # number of paired items
    band: str = ""              # Landis-Koch interpretation

    def __post_init__(self):
        if not self.band:
            self.band = kappa_band(self.kappa)


def cohen_kappa(judge_labels: list, human_labels: list) -> KappaResult:
    """Cohen's kappa for two raters over the same items.

    Works for any finite label set (binary pass/fail in v1, but not limited to it).
    kappa = (p_observed - p_chance) / (1 - p_chance).

    Edge cases handled honestly:
      - n == 0            -> kappa 0.0 (nothing to conclude; caller should treat as
                            insufficient, not as agreement).
      - perfect agreement -> kappa 1.0.
      - both raters constant & identical -> perfect agreement is real: kappa 1.0.
      - p_chance == 1 but not perfect -> undefined ratio; report kappa 0.0.
    """
    if len(judge_labels) != len(human_labels):
        raise ValueError("judge_labels and human_labels must be the same length")
    n = len(judge_labels)
    if n == 0:
        return KappaResult(kappa=0.0, raw_agreement=0.0, n=0)

    agree = sum(1 for a, b in zip(judge_labels, human_labels) if a == b)
    p_observed = agree / n

    # chance agreement: sum over labels of P(judge=label) * P(human=label)
    labels = set(judge_labels) | set(human_labels)
    p_chance = 0.0
    for lab in labels:
        pj = judge_labels.count(lab) / n
        ph = human_labels.count(lab) / n
        p_chance += pj * ph

    if p_observed == 1.0:
        kappa = 1.0
    elif p_chance >= 1.0:
        # raters are constant and agree everywhere but p_observed<1 can't happen;
        # if the ratio is undefined, don't fabricate agreement.
        kappa = 0.0
    else:
        kappa = (p_observed - p_chance) / (1.0 - p_chance)

    return KappaResult(kappa=round(kappa, 4), raw_agreement=round(p_observed, 4), n=n)
