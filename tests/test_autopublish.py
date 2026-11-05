import json

from speculum import chains, verify
from speculum.site.build import public_view

SRC = {"title": "Report", "url": "https://example.org/r.pdf", "publisher": "UN", "date": "2025-01-01"}


def _research():
    return {"resolutions": {"SJRES1": {"bill": "S.J.Res. 1", "items": "bombs", "sources": [SRC]}},
            "outcomes": [{"country": "X", "grade": "documented", "claim": "A happened.", "source": SRC},
                         {"country": "X", "grade": "associated", "claim": "B was reported.", "source": SRC}],
            "context": [{"country": "X", "claim": "C is context.", "source": SRC}]}


def _setup(tmp_path, research, disputes=None):
    d = tmp_path / "chains"
    d.mkdir()
    (d / "research.json").write_text(json.dumps(research))
    (d / "disputes.json").write_text(json.dumps(disputes or {"open": [], "closed": []}))
    (tmp_path / "advocacy.json").write_text(json.dumps({"organizations": [{"name": "Org", "sides": {"SJRES1": "block"},
                                                                          "evidence": [SRC]}]}))
    return d


def test_unchecked_claims_are_checked_and_only_clean_passes_publish(tmp_path, monkeypatch):
    d = _setup(tmp_path, _research())
    # order: the two outcomes, the context item, the sale record, the organization's position
    answers = iter([{"result": "supported", "grade_ok": True}, {"result": "supported", "grade_ok": False},
                    {"result": "not_supported", "note": "no such figure"}, {"result": "supported", "grade_ok": None},
                    {"result": "supported", "grade_ok": None}])
    monkeypatch.setattr(verify, "check_claim", lambda key, claim, dispute=None: next(answers))
    assert verify.run(tmp_path, key="k")["checked"] == 5
    checks = json.loads((d / "checks.json").read_text())["checks"]
    assert sorted(v["result"] for v in checks.values()) == ["grade_too_strong", "not_supported", "supported", "supported", "supported"]
    assert verify.run(tmp_path, key="k")["checked"] == 0          # nothing new: nothing re-checked


def test_public_view_shows_only_passed_strong_items():
    research = _research()
    chains.tag_claims(research, {})
    o1, o2 = research["outcomes"]
    ctx = research["context"][0]
    ch = {"outcomes": [dict(o1), dict(o2)], "context": [dict(ctx)], "mechanism": research["resolutions"]["SJRES1"],
          "advocacy_orgs": {"block": [], "proceed": []}}
    ok = {"result": "supported", "on": "2026-11-05"}
    checks = {o1["_fp"]: ok, o2["_fp"]: ok, research["resolutions"]["SJRES1"]["_fp"]: ok}   # context unchecked
    chains.apply_checks(ch, checks, {"open": []})
    assert ch["auto_public"] and ch["held"] == {"unchecked": 1, "failed": 0, "disputed": 0, "weak": 1}
    v = public_view(ch)
    assert [o["claim"] for o in v["outcomes"]] == ["A happened."] and v["context"] == []


def test_a_dispute_withdraws_the_item_until_rechecked(tmp_path, monkeypatch):
    research = _research()
    chains.tag_claims(research, {})
    fp = research["outcomes"][0]["_fp"]
    d = _setup(tmp_path, _research(), {"open": [{"id": "1", "fp": fp, "source": "https://other.org"}], "closed": []})
    (d / "checks.json").write_text(json.dumps({"checks": {fp: {"result": "supported", "on": "2026-11-04"}}}))
    ch = {"outcomes": [dict(research["outcomes"][0])], "context": [], "mechanism": None, "advocacy_orgs": {}}
    chains.apply_checks(ch, json.loads((d / "checks.json").read_text())["checks"], json.loads((d / "disputes.json").read_text()))
    assert not ch["outcomes"][0]["_public"] and ch["held"]["disputed"] == 1

    seen = []
    def fake(key, claim, dispute=None):
        seen.append(dispute)
        return {"result": "supported", "grade_ok": True, "dispute_upheld": True} if dispute else {"result": "supported", "grade_ok": True}
    monkeypatch.setattr(verify, "check_claim", fake)
    verify.run(tmp_path, key="k")
    assert seen[0]["source"] == "https://other.org"                 # disputes are checked first
    checks = json.loads((d / "checks.json").read_text())["checks"]
    disputes = json.loads((d / "disputes.json").read_text())
    assert checks[fp]["dispute_upheld"] and not disputes["open"] and disputes["closed"][0]["outcome"] == "stands"


def test_no_key_checks_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _setup(tmp_path, _research())
    assert verify.run(tmp_path)["checked"] == 0


def test_editing_a_claim_needs_a_new_check():
    a = {"claim": "A happened.", "source": SRC, "grade": "documented", "priority": {"X": 1}}
    b = dict(a, priority={"X": 9})
    c = dict(a, claim="A happened twice.")
    assert chains.claim_fp(a) == chains.claim_fp(b) != chains.claim_fp(c)
