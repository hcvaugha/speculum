"""Normalized, dated snapshots committed to the repo. Git history is the audit trail.

data/snapshots/<date>/members.json        every sitting member and their IDs
data/snapshots/<date>/finance.json        FEC totals and top PAC givers per member
data/snapshots/<date>/votes.json.gz       roll calls and each member's position
data/snapshots/<date>/lobbying.json       LD-203 lobbyist contributions to members
data/snapshots/<date>/spend.json          LD-2 lobbying spending by issue area
data/snapshots/<date>/meta.json           counts, data ages, errors, and the night's file index
data/snapshots/<date>/tide.json           that night's edition of The Tide

Only what changed is stored. A data file is written on a night only when its content differs from
the copy that stood the night before, and once a newer copy exists the older one leaves the working
tree (it stays in git history, and the raw filings behind it stay in that night's raw release).
Every night's meta.json keeps an index, `files`, naming the night whose copy stood that night and
its SHA-256, so any night can be rebuilt exactly with `python -m speculum restore <date>`.

Day Zero keeps every file: it is the baseline the record is measured from.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import subprocess
from pathlib import Path

DAY_ZERO = "2026-11-03"
log = logging.getLogger("speculum")

KEPT = {"meta.json", "tide.json"}                  # every night keeps its own; all other files roll forward
COMPACT = {"lobbying.json", "spend.json"}          # the large ones: no indentation (about 15% smaller)
WARN_BYTES = 80 * 1024 * 1024                      # GitHub refuses any file over 100 MB
TIDE_NEEDS = ("members.json", "votes.json.gz", "lobbying.json", "finance.json")   # an edition reads these, tonight's and the night before's


def _text(name: str, data) -> str:
    if name in COMPACT:
        return json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=False, default=str)
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False, default=str)


def _encode(name: str, text: str) -> bytes:
    raw = text.encode("utf-8")
    if not name.endswith(".gz"):
        return raw
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as fh:   # same data, same bytes
        fh.write(raw)
    return buf.getvalue()


def _decode(name: str, body: bytes) -> str:
    return (gzip.decompress(body) if name.endswith(".gz") else body).decode("utf-8")


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class Snapshot:
    def __init__(self, root: Path, date: str):
        self.root = Path(root)
        self.date = date
        self.dir = self.root / date
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, name: str, data) -> Path | None:
        """Write one file for this night. A data file whose content matches the copy that already
        stands for this night (an earlier night's, or a rerun's) is not written again; returns None then."""
        text = _text(name, data)
        path = self.dir / name
        if name not in KEPT:
            prev = previous(self.root, self.date)
            seen = set()
            for standing in (self.standing(name), prev.standing(name) if prev else None):
                if standing is None or standing in seen:
                    continue
                seen.add(standing)
                try:
                    if _decode(name, standing.read_bytes()) == text:
                        if standing.parent != self.dir and path.exists():
                            path.unlink()                     # a rerun's own copy, now the same as the night before's
                            self._repoint(name, standing)
                        return None
                except (OSError, ValueError):
                    pass
        body = _encode(name, text)
        if len(body) > WARN_BYTES:
            msg = f"{self.date}/{name} is {len(body) / 1048576:.0f} MB; GitHub refuses files over 100 MB"
            log.warning(msg)
            print(f"::warning::{msg}", flush=True)
        path.write_bytes(body)
        return path

    def standing(self, name: str) -> Path | None:
        """The stored copy that stands for this night: its own, else the one its index names,
        else (a night still being written) the newest earlier copy."""
        own = self.dir / name
        if own.exists():
            return own
        if name in KEPT:
            return None
        meta = self._meta()
        entry = (meta.get("files") or {}).get(name) if meta else None
        if entry:
            p = self.root / entry["night"] / name
            if p.exists():
                return p
            log.warning("%s for %s is in git history only (night %s); run `python -m speculum restore %s`",
                        name, self.date, entry["night"], self.date)
            return None
        if meta and "files" in meta:
            return None                                       # indexed, and this night had no such file
        prev = previous(self.root, self.date)                 # a night still being written: what stood the night before
        if prev and "files" in prev._meta():
            return prev.standing(name)
        for d in reversed(all_dates(self.root, with_meta=False)):   # nights saved before the index existed
            if d < self.date and (self.root / d / name).exists():
                return self.root / d / name
        return None

    def read(self, name: str, default=None):
        path = self.standing(name)
        if path is None:
            return default
        return json.loads(_decode(name, path.read_bytes()))

    def index(self) -> dict:
        """What stands for this night: {name: {"night", "sha256", "bytes"}}, for meta.json."""
        names = {p.name for p in self.dir.iterdir() if p.is_file() and p.name not in KEPT}
        prev = previous(self.root, self.date)
        before = (prev._meta().get("files") if prev else None)
        if before is not None:
            names |= set(before)
        else:                                                 # nights saved before the index existed
            for d in all_dates(self.root, with_meta=False):
                if d < self.date:
                    names |= {p.name for p in (self.root / d).iterdir() if p.is_file() and p.name not in KEPT}
        out = {}
        for name in sorted(names):
            p = self.standing(name)
            if p is not None:
                body = p.read_bytes()
                out[name] = {"night": p.parent.name, "sha256": sha256(body), "bytes": len(body)}
            elif before and name in before:
                out[name] = before[name]                      # stood before, now in git history only
        return out

    def _repoint(self, name: str, standing: Path) -> None:
        """Keep this night's index true when a rerun drops its own copy in favor of an earlier night's."""
        meta = self._meta()
        if "files" in meta:
            body = standing.read_bytes()
            meta["files"][name] = {"night": standing.parent.name, "sha256": sha256(body), "bytes": len(body)}
            (self.dir / "meta.json").write_bytes(_encode("meta.json", _text("meta.json", meta)))

    def _meta(self) -> dict:
        p = self.dir / "meta.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except ValueError:
            return {}


def latest(root: Path) -> Snapshot | None:
    dates = all_dates(root)
    return Snapshot(root, dates[-1]) if dates else None


def previous(root: Path, date: str) -> Snapshot | None:
    earlier = [d for d in all_dates(root) if d < date]
    return Snapshot(root, earlier[-1]) if earlier else None


def all_dates(root: Path, with_meta: bool = True) -> list[str]:
    if not Path(root).exists():
        return []
    return sorted(p.name for p in Path(root).iterdir() if p.is_dir() and (not with_meta or (p / "meta.json").exists()))


def read_newest(root: Path, name: str, upto: str, default=None):
    """The copy that stands on a date. A night whose fetch of one source failed, or whose data did
    not change, shows the last stored copy of that source."""
    for d in reversed([d for d in all_dates(root) if d <= upto]):
        v = Snapshot(root, d).read(name)
        if v:
            return v
    return default


def prune(root: Path) -> list[Path]:
    """Remove data files no longer standing for the newest night. Day Zero keeps everything; meta.json
    and tide.json stay on every night. Removed copies remain in git history, indexed by hash."""
    root = Path(root)
    dates = all_dates(root)
    if not dates:
        return []
    newest = Snapshot(root, dates[-1])
    files = newest._meta().get("files")
    if not files:
        return []                                             # no index yet: nothing can be safely removed
    keep = {p.resolve() for p in (newest.standing(n) for n in files) if p}
    # A night without a saved edition of The Tide gets one built from its own files and the night before's,
    # so those stay until it has one.
    for i, d in enumerate(dates):
        if not (root / d / "tide.json").exists():
            for s in (Snapshot(root, d), Snapshot(root, dates[i - 1]) if i else None):
                if s:
                    keep |= {p.resolve() for p in (s.standing(n) for n in TIDE_NEEDS) if p}
    removed = []
    for d in all_dates(root, with_meta=False):
        if d in (DAY_ZERO, newest.date) or d > newest.date:
            continue
        for p in sorted((root / d).iterdir()):
            if p.is_file() and p.name not in KEPT and p.resolve() not in keep:
                p.unlink()
                removed.append(p)
    return removed


def restore(root: Path, date: str, out: Path, repo: Path | None = None) -> dict:
    """Rebuild one night's files into `out` from the working tree or git history, checking each
    against the SHA-256 in that night's index. Needs the full history (`git fetch --unshallow`)."""
    root, out = Path(root), Path(out)
    if date not in all_dates(root):
        raise SystemExit(f"no saved night {date}")
    snap = Snapshot(root, date)
    files = snap._meta().get("files")
    if files is None:
        raise SystemExit(f"{date} has no file index")
    repo = Path(repo or root.parent.parent)
    out.mkdir(parents=True, exist_ok=True)
    result = {}
    for name in sorted(KEPT):
        if (snap.dir / name).exists():
            (out / name).write_bytes((snap.dir / name).read_bytes())
    for name, entry in sorted(files.items()):
        body = _find(root, repo, entry["night"], name, entry["sha256"])
        if body is None:
            result[name] = "not found"
            continue
        (out / name).write_bytes(body)
        result[name] = f"ok ({entry['night']})"
    return result


def _find(root: Path, repo: Path, night: str, name: str, digest: str) -> bytes | None:
    p = root / night / name
    if p.exists() and sha256(p.read_bytes()) == digest:
        return p.read_bytes()
    rel = p.resolve().relative_to(repo.resolve()).as_posix()
    commits = subprocess.run(["git", "-C", str(repo), "rev-list", "--all", "--", rel],
                             capture_output=True, text=True).stdout.split()
    for c in commits:
        body = subprocess.run(["git", "-C", str(repo), "show", f"{c}:{rel}"], capture_output=True).stdout
        if body and sha256(body) == digest:
            return body
    return None
