"""Normalized, dated snapshots committed to the repo. Git history is the audit trail.

data/snapshots/<date>/members.json        every sitting member and their IDs
data/snapshots/<date>/finance.json        FEC totals and top PAC givers per member
data/snapshots/<date>/votes.json.gz       roll calls and each member's position
data/snapshots/<date>/lobbying.json       LD-203 lobbyist contributions to members
data/snapshots/<date>/meta.json           counts, data ages, archive hashes
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

DAY_ZERO = "2026-11-03"


class Snapshot:
    def __init__(self, root: Path, date: str):
        self.date = date
        self.dir = Path(root) / date
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, name: str, data) -> Path:
        path = self.dir / name
        text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False, default=str)
        if name.endswith(".gz"):
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                fh.write(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def read(self, name: str, default=None):
        path = self.dir / name
        if not path.exists():
            return default
        if name.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        return json.loads(path.read_text(encoding="utf-8"))


def latest(root: Path) -> Snapshot | None:
    dates = all_dates(root)
    return Snapshot(root, dates[-1]) if dates else None


def all_dates(root: Path) -> list[str]:
    if not Path(root).exists():
        return []
    return sorted(p.name for p in Path(root).iterdir() if p.is_dir() and (p / "meta.json").exists())
