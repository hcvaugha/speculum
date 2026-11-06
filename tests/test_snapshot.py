import json
import subprocess

from speculum.snapshot import DAY_ZERO, Snapshot, prune, read_newest, restore


def _night(root, date, files, meta=None):
    """Write one night the way the nightly run does: data files, then meta with the index."""
    s = Snapshot(root, date)
    for name, data in files.items():
        s.write(name, data)
    s.write("meta.json", {**(meta or {}), "date": date, "files": s.index()})
    s.write("tide.json", {"date": date})
    return s


def test_unchanged_files_are_not_stored_again_and_reads_follow_the_index(tmp_path):
    _night(tmp_path, DAY_ZERO, {"members.json": [1], "lobbying.json": {"a": 1}})
    s = _night(tmp_path, "2026-11-04", {"members.json": [1], "lobbying.json": {"a": 2}})
    assert not (s.dir / "members.json").exists()
    assert s.read("members.json") == [1]
    assert s.read("lobbying.json") == {"a": 2}
    files = s.read("meta.json")["files"]
    assert files["members.json"]["night"] == DAY_ZERO and files["lobbying.json"]["night"] == "2026-11-04"


def test_a_failed_source_stands_from_the_last_good_night(tmp_path):
    _night(tmp_path, DAY_ZERO, {"finance.json": {"x": 1}})
    _night(tmp_path, "2026-11-04", {"finance.json": {"x": 2}})
    s = _night(tmp_path, "2026-11-05", {})                       # finance failed tonight
    assert s.read("finance.json") == {"x": 2}
    assert s.read("meta.json")["files"]["finance.json"]["night"] == "2026-11-04"
    assert read_newest(tmp_path, "finance.json", "2026-11-05") == {"x": 2}


def test_prune_keeps_day_zero_and_what_the_newest_night_stands_on(tmp_path):
    _night(tmp_path, DAY_ZERO, {"lobbying.json": {"a": 0}, "members.json": [1]})
    _night(tmp_path, "2026-11-04", {"lobbying.json": {"a": 1}, "committees.json": {"c": 1}})
    _night(tmp_path, "2026-11-05", {"lobbying.json": {"a": 2}, "committees.json": {"c": 1}})
    removed = {f"{p.parent.name}/{p.name}" for p in prune(tmp_path)}
    assert removed == {"2026-11-04/lobbying.json"}
    assert (tmp_path / DAY_ZERO / "lobbying.json").exists()      # the baseline stays whole
    assert (tmp_path / "2026-11-04" / "committees.json").exists()  # still what stands tonight
    assert (tmp_path / "2026-11-04" / "meta.json").exists() and (tmp_path / "2026-11-04" / "tide.json").exists()
    old = Snapshot(tmp_path, "2026-11-04")
    assert old.read("lobbying.json") is None                     # in git history only: never a wrong copy


def test_prune_does_nothing_without_an_index(tmp_path):
    for d in (DAY_ZERO, "2026-11-04", "2026-11-05"):
        s = Snapshot(tmp_path, d)
        s.write("lobbying.json", {"d": d})
        s.write("meta.json", {"date": d})
    assert prune(tmp_path) == []


def test_gzip_is_byte_stable_and_large_files_are_compact(tmp_path):
    a, b = Snapshot(tmp_path / "a", "2026-11-04"), Snapshot(tmp_path / "b", "2026-11-04")
    a.write("votes.json.gz", {"votes": [1, 2]})
    b.write("votes.json.gz", {"votes": [1, 2]})
    assert (a.dir / "votes.json.gz").read_bytes() == (b.dir / "votes.json.gz").read_bytes()
    a.write("lobbying.json", {"b": 1, "a": [1, 2]})
    assert (a.dir / "lobbying.json").read_text() == '{"a":[1,2],"b":1}'


def test_restore_rebuilds_a_pruned_night_from_git_history(tmp_path):
    repo = tmp_path
    root = repo / "data" / "snapshots"
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    _night(root, DAY_ZERO, {"lobbying.json": {"a": 0}})
    _night(root, "2026-11-04", {"lobbying.json": {"a": 1}})
    git("add", "-A")
    git("commit", "-qm", "n1")
    _night(root, "2026-11-05", {"lobbying.json": {"a": 2}})
    prune(root)
    git("add", "-A")
    git("commit", "-qm", "n2")
    assert not (root / "2026-11-04" / "lobbying.json").exists()
    out = tmp_path / "out"
    result = restore(root, "2026-11-04", out, repo=repo)
    assert result["lobbying.json"].startswith("ok")
    assert json.loads((out / "lobbying.json").read_text()) == {"a": 1}
    assert (out / "meta.json").exists()
