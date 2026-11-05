from speculum import chains


def _chain(**kw):
    ch = {"mechanism": {"notified": "2026-03-01"}, "outcomes": [{"claim": "x", "grade": "documented"}], "context": [],
          "genocide_note": None, "advocacy_orgs": {"block": [], "proceed": []}, "advocacy_after": [],
          "advocacy_noside": [], "advocacy_meta": {}, "research_by": "research", "draft_meta": None,
          "members": [{"name": "A", "total": 100}], "money_pattern": {"verdict": "gap"}}
    ch.update(kw)
    return ch


def test_approval_holds_while_only_figures_change():
    ch = _chain()
    fp = chains.fingerprint(ch)
    ch["review"] = {"approved_by": "the editor", "date": "2026-11-05", "research": fp, "ai_verified": {"date": "2026-11-04"}}
    ch["members"] = [{"name": "A", "total": 250}]          # tonight's money changed
    ch["money_pattern"] = {"verdict": "small"}
    chains.set_status(ch)
    assert ch["status"] == "approved" and ch["lapsed"] is None


def test_approval_lapses_when_research_changes():
    ch = _chain()
    ch["review"] = {"approved_by": "the editor", "date": "2026-11-05", "research": chains.fingerprint(ch),
                    "ai_verified": {"date": "2026-11-04"}}
    ch["outcomes"] = ch["outcomes"] + [{"claim": "y", "grade": "associated"}]
    chains.set_status(ch)
    assert ch["status"] == "verified" and ch["lapsed"]["approved_by"] == "the editor"


def test_approval_without_fingerprint_does_not_count():
    ch = _chain(review={"approved_by": "someone", "date": "2026-11-05"})
    chains.set_status(ch)
    assert ch["status"] == "draft" and ch["lapsed"]
