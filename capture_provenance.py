"""
capture_provenance.py -- Provenance schema + canonical serialization (capture-v0 §3.3).

Provenance is the chain-of-custody metadata that travels with an EvidenceBundle:
WHERE it was captured (capture_point), WHOSE clock timestamped it (time_source),
whether the capture channel itself was proven able to see (the canary /
capture_control), and whether any events went unaccounted (completeness). The
admissibility gate (capture_admissibility.py) reads exactly these fields to decide
whether the evidence may reach the judge.

The enums are ordered by strength on purpose (SEALED > SHARED > SELF_REPORTED;
CAPTURE_HOST > MONOTONIC_ONLY > AGENT_REPORTED) so the gate can express a minimum.

Canonical serialization is byte-stable for identical inputs (capture-v0 §5, M3
DoD) -- provenance is hashed and logged, so it must serialize deterministically.

Pure stdlib. The only cross-module import is EvidenceBundle from the spine, whose
schema is untouched (capture-v0 §1: do not modify verdict_engine.py).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from verdict_engine import EvidenceBundle
from capture_log import ChainSeal, ChainReport

SPEC_VERSION = "capture-v0"


class CapturePoint(str, Enum):
    SEALED = "SEALED"                # observer outside agent's write domain
    SHARED = "SHARED"                # same host/fs; agent could theoretically write
    SELF_REPORTED = "SELF_REPORTED"  # evidence produced by the evaluated entity


class TimeSource(str, Enum):
    CAPTURE_HOST = "CAPTURE_HOST"
    MONOTONIC_ONLY = "MONOTONIC_ONLY"
    AGENT_REPORTED = "AGENT_REPORTED"


class CaptureControlStatus(str, Enum):   # mirrors PCStatus deliberately
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


# Strength ranks: higher == stronger. The gate compares against a minimum.
CAPTURE_POINT_RANK = {
    CapturePoint.SELF_REPORTED: 0,
    CapturePoint.SHARED: 1,
    CapturePoint.SEALED: 2,
}
TIME_SOURCE_RANK = {
    TimeSource.AGENT_REPORTED: 0,
    TimeSource.MONOTONIC_ONLY: 1,
    TimeSource.CAPTURE_HOST: 2,
}


@dataclass
class Completeness:
    events_observed: int
    events_claimed: Optional[int]    # None if the source makes no claim
    truncated: bool
    blind_windows: list             # list[tuple[float, float]] -- [start,end) in ts_mono

    def __post_init__(self):
        # Normalize blind_windows to tuples so serialization is stable regardless of
        # whether callers pass lists or tuples.
        self.blind_windows = [tuple(w) for w in self.blind_windows]


@dataclass
class Provenance:
    spec_version: str                # must equal SPEC_VERSION
    capture_point: CapturePoint
    time_source: TimeSource
    chain_seal: ChainSeal
    chain_report: ChainReport        # produced by verify_chain at admissibility time
    capture_control: CaptureControlStatus   # canary observed end-to-end through the channel
    completeness: Completeness
    observer_id: str                 # which harness captured this


@dataclass
class CapturedEvidenceBundle:
    bundle: EvidenceBundle           # from verdict_engine -- untouched schema
    provenance: Provenance


# ---------------------------------------------------------------------------
# Canonical serialization (capture-v0 §2 / §5 M3 DoD).
# Byte-identical for identical inputs; enums -> their .value, tuples -> lists.
# ---------------------------------------------------------------------------

def provenance_to_dict(p: Provenance) -> dict:
    """Deterministic dict view of Provenance (enum values, plain scalars)."""
    return {
        "spec_version": p.spec_version,
        "capture_point": p.capture_point.value,
        "time_source": p.time_source.value,
        "chain_seal": {
            "final_hash": p.chain_seal.final_hash,
            "record_count": p.chain_seal.record_count,
        },
        "chain_report": {
            "intact": p.chain_report.intact,
            "break_seq": p.chain_report.break_seq,
            "records": p.chain_report.records,
            "reason": p.chain_report.reason,
        },
        "capture_control": p.capture_control.value,
        "completeness": {
            "events_observed": p.completeness.events_observed,
            "events_claimed": p.completeness.events_claimed,
            "truncated": p.completeness.truncated,
            # tuples -> lists for JSON; order preserved.
            "blind_windows": [list(w) for w in p.completeness.blind_windows],
        },
        "observer_id": p.observer_id,
    }


def canonical_provenance_bytes(p: Provenance) -> bytes:
    """Canonical JSON bytes: sorted keys, tight separators, UTF-8 (capture-v0 §2)."""
    return json.dumps(provenance_to_dict(p), sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def provenance_digest(p: Provenance) -> str:
    return hashlib.sha256(canonical_provenance_bytes(p)).hexdigest()


def _make_sample(blind_windows) -> Provenance:
    return Provenance(
        spec_version=SPEC_VERSION,
        capture_point=CapturePoint.SEALED,
        time_source=TimeSource.CAPTURE_HOST,
        chain_seal=ChainSeal(final_hash="a" * 64, record_count=7),
        chain_report=ChainReport(intact=True, break_seq=None, records=7, reason="ok"),
        capture_control=CaptureControlStatus.PASS,
        completeness=Completeness(events_observed=7, events_claimed=7,
                                  truncated=False, blind_windows=blind_windows),
        observer_id="capture-selfcheck",
    )


def _selfcheck() -> int:
    """DoD (capture-v0 §5, M3): canonical serialization is byte-identical across
    runs for identical inputs (seeded, deterministic)."""
    # Build the SAME logical provenance twice, independently.
    a = canonical_provenance_bytes(_make_sample([(1.0, 2.0), (3.5, 4.0)]))
    b = canonical_provenance_bytes(_make_sample([[1.0, 2.0], [3.5, 4.0]]))  # tuples vs lists
    assert a == b, "byte-identical serialization must not depend on tuple-vs-list input"

    # Digest is stable, and sensitive to a real change.
    d1 = provenance_digest(_make_sample([(1.0, 2.0)]))
    d2 = provenance_digest(_make_sample([(1.0, 2.0)]))
    d3 = provenance_digest(_make_sample([(1.0, 9.0)]))
    assert d1 == d2, "identical inputs must yield identical digest"
    assert d1 != d3, "a changed blind window must change the digest"

    print("capture_provenance self-check: PASS "
          "(canonical serialization byte-stable, digest stable + change-sensitive)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selfcheck())
