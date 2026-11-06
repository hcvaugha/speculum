from speculum import findings


def test_party_agreement_counts_majorities_and_solid_opposition():
    members = [{"bioguide": f"D{i}", "party": "D"} for i in range(10)] + [{"bioguide": f"R{i}", "party": "R"} for i in range(10)]
    votes = {"votes": [
        {"id": "a", "chamber": "house", "question": "On Passage", "date": "2025-02-01"},           # same side
        {"id": "b", "chamber": "house", "question": "On Ordering the Previous Question", "date": "2025-02-02"},  # solidly opposed
        {"id": "q", "chamber": "house", "question": "Call by States", "type": "Quorum", "date": "2025-02-03"},
    ], "positions": {
        "a": {**{f"D{i}": "Yea" for i in range(10)}, **{f"R{i}": ("Yea" if i < 6 else "Nay") for i in range(10)}},
        "b": {**{f"D{i}": "Nay" for i in range(10)}, **{f"R{i}": "Yea" for i in range(10)}},
        "q": {f"D{i}": "Present" for i in range(10)},
    }}
    pa = findings.party_agreement(votes, members)["chambers"]["house"]
    assert pa["all"] == {"n": 2, "same": 1, "solid_opposed": 1, "solid_same": 0}
    assert pa["bills"]["same"] == 1 and pa["procedure"]["solid_opposed"] == 1
    assert findings.billname("sjres32") == "S.J.Res. 32"
