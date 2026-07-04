"""
capture_store.py -- Content-addressed blob store + bundle manifest (capture-v0 §3.2).

Used by capture_subprocess.py to snapshot a workspace before and after a sealed
run: every file's bytes are content-addressed (keyed by their SHA-256 digest), and
a Manifest maps logical names -> digests. The manifest is itself serialized
canonically and hashed, so it too is tamper-evident.

Why content-addressing: it makes the store immutable-by-key -- the digest IS the
name, so you cannot silently swap a blob's contents without changing its address.
get() re-verifies the digest on read and raises on mismatch (bit-rot / tampering
caught at the boundary, not trusted away).

Pure stdlib, zero runtime deps (capture-v0 §1).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


class DigestMismatch(Exception):
    """Raised when a blob read back does not hash to the digest it was filed under."""


class BlobStore:
    """A content-addressed file store.

    put(blob) -> digest; the blob is written to <store_dir>/<digest[:2]>/<digest>.
    get(digest) reads it back and RE-VERIFIES the hash before returning -- a
    corrupted or swapped blob raises DigestMismatch rather than returning bad bytes.
    """

    def __init__(self, store_dir: str):
        self.store_dir = store_dir
        os.makedirs(store_dir, exist_ok=True)

    def _path_for(self, digest: str) -> str:
        return os.path.join(self.store_dir, digest[:2], digest)

    def put(self, blob: bytes) -> str:
        if not isinstance(blob, (bytes, bytearray)):
            raise TypeError("blob must be bytes, got %r" % type(blob))
        blob = bytes(blob)
        digest = _sha256_hex(blob)
        path = self._path_for(digest)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Content-addressed: if it already exists with this digest, it IS this blob.
        if not os.path.exists(path):
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        return digest

    def has(self, digest: str) -> bool:
        return os.path.exists(self._path_for(digest))

    def get(self, digest: str) -> bytes:
        path = self._path_for(digest)
        if not os.path.exists(path):
            raise KeyError("no blob for digest %s" % digest)
        with open(path, "rb") as f:
            blob = f.read()
        actual = _sha256_hex(blob)
        if actual != digest:
            raise DigestMismatch("stored blob for %s hashes to %s" % (digest, actual))
        return blob


@dataclass
class Manifest:
    """Logical name -> content digest, serialized canonically and self-hashed.

    A workspace snapshot is a Manifest: {relative_path: sha256_of_file_bytes}. The
    manifest's own digest (manifest_digest) is logged into the chain, so the snapshot
    is anchored to the tamper-evident log just like every other captured signal.
    """
    entries: dict = field(default_factory=dict)   # name -> digest

    def add(self, name: str, digest: str) -> None:
        self.entries[name] = digest

    def canonical_bytes(self) -> bytes:
        return _canonical({"entries": self.entries})

    def manifest_digest(self) -> str:
        return _sha256_hex(self.canonical_bytes())

    def to_json(self) -> str:
        return self.canonical_bytes().decode("utf-8")

    @staticmethod
    def from_json(text: str) -> "Manifest":
        d = json.loads(text)
        return Manifest(entries=dict(d.get("entries", {})))


def snapshot_workdir(workdir: str, store: BlobStore) -> Manifest:
    """Content-address every regular file under workdir into the store.

    Returns a Manifest mapping each file's workdir-relative path to its blob digest.
    Deterministic: entries are keyed by sorted relative path, so the manifest digest
    is stable for identical trees regardless of filesystem walk order.
    """
    manifest = Manifest()
    for root, _dirs, files in os.walk(workdir):
        for fname in sorted(files):
            abspath = os.path.join(root, fname)
            if not os.path.isfile(abspath):
                continue
            rel = os.path.relpath(abspath, workdir)
            with open(abspath, "rb") as f:
                digest = store.put(f.read())
            manifest.add(rel.replace(os.sep, "/"), digest)
    return manifest


def _selfcheck() -> int:
    """DoD (capture-v0 §5, M2): round-trip + digest-mismatch-raises, seeded.

    Infrastructure, not a judgment -> plain asserts are appropriate here (the DoD
    explicitly permits stdlib asserts for this milestone).
    """
    import random
    import tempfile

    rng = random.Random(1234)
    store_dir = tempfile.mkdtemp(prefix="oraculum_store_")
    store = BlobStore(store_dir)

    # 1. round-trip: put then get returns the exact bytes, keyed by digest.
    blobs = [bytes(rng.randrange(256) for _ in range(rng.randrange(1, 512)))
             for _ in range(50)]
    digests = []
    for b in blobs:
        d = store.put(b)
        digests.append(d)
        assert store.get(d) == b, "round-trip mismatch"
    # identical content -> identical digest (content-addressing is deterministic)
    assert store.put(blobs[0]) == digests[0], "same content must map to same digest"

    # 2. digest-mismatch-raises: corrupt a stored blob on disk, get() must raise.
    victim = digests[0]
    path = store._path_for(victim)
    with open(path, "wb") as f:
        f.write(b"corrupted-payload-does-not-hash-to-key")
    raised = False
    try:
        store.get(victim)
    except DigestMismatch:
        raised = True
    assert raised, "get() must raise DigestMismatch on corrupted blob"

    # 3. manifest round-trip + stable self-digest.
    wd = tempfile.mkdtemp(prefix="oraculum_ws_")
    for name, content in [("a.txt", b"alpha"), ("sub/b.txt", b"beta"), ("c.txt", b"gamma")]:
        p = os.path.join(wd, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(content)
    m1 = snapshot_workdir(wd, store)
    m2 = snapshot_workdir(wd, store)
    assert m1.manifest_digest() == m2.manifest_digest(), "manifest digest must be stable"
    m_round = Manifest.from_json(m1.to_json())
    assert m_round.manifest_digest() == m1.manifest_digest(), "manifest JSON round-trip"

    print("capture_store self-check: PASS "
          "(round-trip, digest-mismatch-raises, manifest stability)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selfcheck())
