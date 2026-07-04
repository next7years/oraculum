"""
capture_log.py -- Append-only, hash-chained event log (capture-v0 spec §3.1).

This is the tamper-evident spine of the capture layer. Evidence is captured at a
boundary the evaluated agent cannot write to; every observed signal becomes one
JSONL LogRecord whose entry_hash chains to its predecessor. A single-record
mutation, a reorder, a delete/insert, or a tail truncation all break the chain --
and `verify_chain` names exactly where.

Design maxims (capture-v0 §0):
    - Capture raw signals, never interpretations. `payload` is raw UTF-8 text.
    - Tamper-evidence via hashing, not trust. SHA-256, canonical JSON, no network.
    - Absence must be distinguishable from evidence-of-absence: the ChainSeal
      records the expected length so a truncated tail is detectable, not silent.

Pure stdlib. Zero runtime dependencies -- the auditability of this layer depends
on its minimality (capture-v0 §1).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Optional

# capture-v0 §2: canonical JSON + hash algorithm are pre-decided, not tunable.
GENESIS_PREV_HASH = "0" * 64


def _canonical(obj) -> bytes:
    """Canonical JSON bytes: sorted keys, tight separators, UTF-8 (capture-v0 §2)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class LogRecord:
    seq: int                  # 0-based, strictly increasing
    ts_host: float            # observer's wall clock (time.time())
    ts_mono: float            # observer's monotonic clock (time.monotonic())
    event_type: str           # e.g. "subprocess_start", "stdout_chunk", "exit", "canary"
    payload_sha256: str       # sha256 of raw payload bytes
    payload: str              # payload as UTF-8 text (raw signal, never a summary)
    prev_hash: str            # entry_hash of seq-1; genesis uses 64*"0"
    entry_hash: str           # sha256(canonical(record minus entry_hash))

    def hashed_view(self) -> dict:
        """The record as it is fed to the hash: every field EXCEPT entry_hash.

        entry_hash is derived from this, so it must not be part of its own input.
        prev_hash IS included -> mutating any earlier record changes every later
        entry_hash, which is what makes the chain tamper-evident.
        """
        return {
            "seq": self.seq,
            "ts_host": self.ts_host,
            "ts_mono": self.ts_mono,
            "event_type": self.event_type,
            "payload_sha256": self.payload_sha256,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
        }

    def to_json_line(self) -> str:
        d = self.hashed_view()
        d["entry_hash"] = self.entry_hash
        return json.dumps(d, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def from_dict(d: dict) -> "LogRecord":
        return LogRecord(
            seq=d["seq"],
            ts_host=d["ts_host"],
            ts_mono=d["ts_mono"],
            event_type=d["event_type"],
            payload_sha256=d["payload_sha256"],
            payload=d["payload"],
            prev_hash=d["prev_hash"],
            entry_hash=d["entry_hash"],
        )


def compute_entry_hash(record_wo_hash: dict) -> str:
    """entry_hash = sha256(canonical(record minus entry_hash)) (capture-v0 §3.1)."""
    return _sha256_hex(_canonical(record_wo_hash))


@dataclass
class ChainSeal:
    """The manifest's truth about the chain's length and terminal hash.

    Written once at seal() time. verify_chain compares the on-disk chain against
    this: a mismatch in record_count or final_hash is how tail truncation (records
    silently dropped from the end) is caught -- the log alone can't self-report a
    tail it no longer has.
    """
    final_hash: str
    record_count: int


@dataclass
class ChainReport:
    intact: bool
    break_seq: Optional[int]   # seq of the first offending record (None if intact)
    records: int               # number of records actually read from disk
    reason: str


class CaptureLog:
    """Append-only hash-chained JSONL writer.

    Append-only by construction: the ONLY mutating method is append(). There is no
    update or delete API -- the evaluated agent (and this code) can extend the log
    but cannot rewrite history without breaking the chain.
    """

    def __init__(self, jsonl_path: str, clock=None, monoclock=None):
        self.path = jsonl_path
        # Injectable clocks keep the module deterministic under test/seeded demos;
        # in production these default to the observer's real host + monotonic clocks.
        import time
        self._clock = clock or time.time
        self._monoclock = monoclock or time.monotonic
        self._seq = 0
        self._prev_hash = GENESIS_PREV_HASH
        self._final_hash = GENESIS_PREV_HASH
        # Truncate/create the file so a log always starts at genesis.
        d = os.path.dirname(os.path.abspath(jsonl_path))
        os.makedirs(d, exist_ok=True)
        with open(jsonl_path, "w", encoding="utf-8"):
            pass

    def append(self, event_type: str, payload_bytes: bytes) -> LogRecord:
        """Compute hashes, write ONE JSONL line, flush. No mutation of prior lines."""
        if not isinstance(payload_bytes, (bytes, bytearray)):
            raise TypeError("payload must be bytes (raw signal), got %r" % type(payload_bytes))
        payload_text = bytes(payload_bytes).decode("utf-8", errors="replace")
        wo_hash = {
            "seq": self._seq,
            "ts_host": self._clock(),
            "ts_mono": self._monoclock(),
            "event_type": event_type,
            "payload_sha256": _sha256_hex(bytes(payload_bytes)),
            "payload": payload_text,
            "prev_hash": self._prev_hash,
        }
        entry_hash = compute_entry_hash(wo_hash)
        rec = LogRecord(entry_hash=entry_hash, **wo_hash)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(rec.to_json_line() + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._seq += 1
        self._prev_hash = entry_hash
        self._final_hash = entry_hash
        return rec

    def seal(self) -> ChainSeal:
        """Snapshot the expected terminal state -> the truncation detector."""
        return ChainSeal(final_hash=self._final_hash, record_count=self._seq)


def _read_records(jsonl_path: str) -> list:
    records = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(LogRecord.from_dict(json.loads(line)))
    return records


def verify_chain(jsonl_path: str, seal: Optional[ChainSeal] = None) -> ChainReport:
    """Recompute the chain from disk and detect any tampering (capture-v0 §3.1).

    Pure: reads the file, no other side effects. Detects, in order:
        (a) payload/field mutation      -> entry_hash no longer matches contents
        (b) record reordering / seq gap -> seq must be 0,1,2,... with no jumps
        (c) prev_hash break             -> a record's prev_hash != predecessor's hash
            (covers insertion / deletion in the middle: the link no longer closes)
        (d) tail truncation             -> seal.record_count / seal.final_hash
            mismatch (a dropped tail leaves the on-disk chain internally intact,
            so ONLY the seal can catch it)

    The first offending record's seq is returned as break_seq. reason is a short,
    machine-greppable string (the eval and CI key off it).
    """
    try:
        records = _read_records(jsonl_path)
    except (OSError, ValueError) as e:
        return ChainReport(intact=False, break_seq=None, records=0,
                           reason="unreadable:%s" % type(e).__name__)

    n = len(records)
    prev_hash = GENESIS_PREV_HASH
    for i, rec in enumerate(records):
        # (b) reordering / deletion / insertion shows up as a broken seq sequence.
        if rec.seq != i:
            return ChainReport(intact=False, break_seq=rec.seq, records=n,
                               reason="seq_out_of_order:expected_%d_got_%d" % (i, rec.seq))
        # (c) the link to the predecessor must close.
        if rec.prev_hash != prev_hash:
            return ChainReport(intact=False, break_seq=rec.seq, records=n,
                               reason="prev_hash_mismatch")
        # (a) recompute entry_hash over the record's own contents.
        recomputed = compute_entry_hash(rec.hashed_view())
        if recomputed != rec.entry_hash:
            return ChainReport(intact=False, break_seq=rec.seq, records=n,
                               reason="entry_hash_mismatch")
        prev_hash = rec.entry_hash

    # (d) truncation: the on-disk chain can be internally perfect yet be missing
    #     its tail. Only the seal, taken at capture time, knows the true length.
    if seal is not None:
        if n != seal.record_count:
            return ChainReport(intact=False, break_seq=None, records=n,
                               reason="record_count_mismatch:expected_%d_got_%d"
                                      % (seal.record_count, n))
        actual_final = records[-1].entry_hash if records else GENESIS_PREV_HASH
        if actual_final != seal.final_hash:
            return ChainReport(intact=False, break_seq=None, records=n,
                               reason="final_hash_mismatch")

    return ChainReport(intact=True, break_seq=None, records=n, reason="ok")
