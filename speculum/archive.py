"""Raw archive: every byte fetched from a source is kept, hashed, and listed in a manifest.

Layout for one run:
    data/raw/<run_date>/<source>/<name>
    data/raw/<run_date>/MANIFEST.jsonl   one line per file: path, sha256, bytes, url, fetched_at

The nightly workflow tars data/raw/<run_date> and attaches it to a GitHub release, so the
untouched filings behind every number can always be recovered and re-verified.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    return _SAFE.sub("_", name).strip("_")[:180] or "file"


class RawArchive:
    def __init__(self, root: Path, run_date: str):
        self.dir = Path(root) / run_date
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self.dir / "MANIFEST.jsonl"

    def save(self, source: str, name: str, body: bytes, url: str) -> str:
        """Store body under source/name and record its hash. Returns the sha256."""
        digest = hashlib.sha256(body).hexdigest()
        rel = Path(safe_name(source)) / safe_name(name)
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        entry = {
            "path": str(rel),
            "sha256": digest,
            "bytes": len(body),
            "url": _strip_key(url),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with self.manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        return digest


_KEY_PARAM = re.compile(r"(api_key=)[^&]+", re.I)


def _strip_key(url: str) -> str:
    """Never write API keys into the archive or the public record."""
    return _KEY_PARAM.sub(r"\1REDACTED", url)
