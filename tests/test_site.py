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


def test_text_cleanups_from_the_site_check():
    from speculum.names import org_name
    from speculum.site.build import _plain
    from speculum.sources.lobbyspend import no_post
    assert org_name("THE HOME DEPOT INC. POLITICAL ACTION COMMITTEE; THE") == "The Home Depot Inc. Political Action Committee"
    assert org_name("HOME DEPOT INC POLITICAL ACTION COMMITTEE, THE") == "The Home Depot Inc Political Action Committee"
    assert _plain("Centers for Medicare &#38; Medicaid Services") == "Centers for Medicare & Medicaid Services"
    assert _plain("&#226;€‹Public policy advocacy.") == "Public policy advocacy."
    assert all(no_post(x) for x in ("None.", "N/A", "none", " Not applicable. "))
    assert not any(no_post(x) for x in ("Chief of Staff", "LA, US Senate"))


def test_long_pages_get_jump_links_and_short_ones_do_not():
    from jinja2 import Environment, FileSystemLoader
    from speculum.site.build import HERE
    e = Environment(loader=FileSystemLoader(HERE / "templates"))
    t = e.from_string('{% from "_toc.html" import toc %}{{ toc(items) }}')
    assert 'href="#a"' in t.render(items=[("a", "A"), ("b", "B"), None, ("c", "C")])
    assert "toc" not in t.render(items=[("a", "A"), None, ("b", "B")])          # fewer than three sections: no bar
