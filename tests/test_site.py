import json

from speculum.site.build import build
from speculum.snapshot import Snapshot

from test_sources import members


def _site(tmp_path, mode):
    data = tmp_path / "data"
    (data / "snapshots").mkdir(parents=True)
    (data / "site.json").write_text(json.dumps({"public": mode}))
    s = Snapshot(data / "snapshots", "2026-11-03")
    s.write("members.json", members())
    s.write("votes.json.gz", {"votes": [], "positions": {}})
    s.write("meta.json", {"date": "2026-11-03", "counts": {}})
    s.write("tide.json", {"date": "2026-11-03", "stories": []})
    build(data / "snapshots", tmp_path / "dist")
    return tmp_path / "dist"


def test_holding_mode_keeps_the_holding_page_and_the_owners_list(tmp_path):
    d = _site(tmp_path, "holding")
    assert "Request early access" in (d / "public" / "index.html").read_text()
    assert (d / "public" / "signups.html").exists()
    assert not (d / "public" / "join.html").exists()
    assert "Early access · not yet public" in (d / "preview" / "join.html").read_text()


def test_launch_keeps_the_owners_list_and_offers_joining_the_readers(tmp_path):
    d = _site(tmp_path, "launch")
    pub = d / "public"
    assert (pub / "signups.html").exists()                      # the list of requests survives the flip
    join = (pub / "join.html").read_text()
    assert "api/early-access" in join and "Ask to join the readers" in join
    assert 'href="join.html"' in (pub / "index.html").read_text()
    assert 'href="join.html"' in (pub / "about.html").read_text()
    assert "preview-flag" not in join
    assert "Readers&#39; preview · not yet published" in (d / "preview" / "index.html").read_text()


def test_the_glass_frames_the_record_and_teases_the_game(tmp_path):
    d = _site(tmp_path, "launch")
    g = (d / "public" / "glass.html").read_text()
    for pane in ("What has been", "What is", "What is coming", "What could not be"):
        assert pane in g
    assert "Malleus Cordis" in g and 'href="join.html"' in g
    assert "casts no real person as anything" in g
    assert 'href="glass.html"' in (d / "public" / "index.html").read_text()      # the seal in every footer
