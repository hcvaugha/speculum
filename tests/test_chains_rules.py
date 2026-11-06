from speculum import chains

CRA = ("Providing for congressional disapproval under chapter 8 of title 5, United States Code, of the rule submitted "
       "by the Bureau of Consumer Financial Protection relating to \"Overdraft Lending: Very Large Financial Institutions\".")


def _members():
    out = []
    for i in range(12):
        out.append({"bioguide": f"H{i}", "name": f"Rep {i}", "party": "R" if i < 6 else "D", "state": "XX", "chamber": "house"})
        out.append({"bioguide": f"S{i}", "name": f"Sen {i}", "party": "R" if i < 6 else "D", "state": "XX", "chamber": "senate"})
    return out


def _votes():
    house = {f"H{i}": "Yea" if i < 6 else "Nay" for i in range(12)}
    senate = {f"S{i}": "Yea" if i < 6 else "Nay" for i in range(12)}
    return {"votes": [
        {"id": "h-119-1-50", "chamber": "house", "congress": 119, "bill": "HJRES 18", "date": "2025-03-04", "question": "On Passage",
         "result": "Passed", "url": "https://clerk.house.gov/evs/2025/roll050.xml"},                 # House: no title on the vote
        {"id": "s-119-1-120", "chamber": "senate", "congress": 119, "bill": "HJRES18", "date": "2025-03-27", "title": CRA,
         "question": "On the Joint Resolution", "result": "Joint Resolution Passed", "url": "https://www.senate.gov/x"},
        {"id": "s-119-1-121", "chamber": "senate", "congress": 119, "bill": "SJRES9", "date": "2025-03-27",
         "title": "A joint resolution providing for congressional disapproval of the proposed foreign military sale to X of certain defense articles.",
         "question": "On the Motion to Discharge", "result": "Rejected", "url": "https://www.senate.gov/y"},
    ], "positions": {"h-119-1-50": house, "s-119-1-120": senate, "s-119-1-121": senate}}


def test_rule_parts_reads_agency_and_rule():
    assert chains.rule_parts(CRA) == ("Bureau of Consumer Financial Protection", "Overdraft Lending: Very Large Financial Institutions")
    assert chains.area_of("Bureau of Consumer Financial Protection") == "finance"
    assert chains.area_of("Environmental Protection Agency") == "energy-environment"


def test_one_chain_per_resolution_across_chambers_with_money_from_who_lobbied_it():
    info = {"bills": {"119-HJRES18": {"title": CRA, "latest_action": {"date": "2025-05-09", "text": "Became Public Law No: 119-10."}}}}
    spend = {"quarters": {"2025-Q1": {"orgs": {"bankco": ["Bank Co", "finance", "777"], "firm": ["Client Inc", "finance", ""]},
                                      "people": {}, "bills": {"HJRES18": {"orgs": ["bankco", "firm"], "reports": 2}}}}}
    # Bank Co (registrant 777) gave to a Republican representative before the House vote and after it; another
    # registrant, which never named the resolution, gave too.
    lobbying = {"years": [2025, 2026], "by_member": {
        "H0": [{"date": "2025-02-01", "amount": 500.0, "registrant_id": "777", "type": "FECA"},
               {"date": "2025-04-01", "amount": 9000.0, "registrant_id": "777", "type": "FECA"},
               {"date": "2025-02-01", "amount": 300.0, "registrant_id": "999", "type": "FECA"}]}}
    out = chains.build(_members(), {}, lobbying, _votes(), {}, {}, {}, spend=spend, bill_info=info)
    cra = [c for c in out if c["rule"] == "rules-overturned"]
    assert len(cra) == 1
    c = cra[0]
    assert c["id"] == "rules-overturned-119-hjres18" and c["enacted"] and c["area"] == "finance"
    assert [x["chamber"] for x in c["chambers"]] == ["house", "senate"]
    house = c["chambers"][0]
    h0 = next(r for r in house["members"] if r["bioguide"] == "H0")
    assert h0["lobby"] == 500.0                     # only the organization that named it, only before the vote
    assert [o["name"] for o in c["lobbied_orgs"]] == ["Bank Co", "Client Inc"]
    arms = [x for x in out if x["rule"] == "arms-sales"]
    assert len(arms) == 1 and arms[0]["siblings"] == []   # same day, different theme: not compared
    assert "chamber" not in arms[0]["votes"][0]


def test_measures_lists_every_selected_resolution_once():
    assert chains.measures(_votes()) == [(119, "HJRES18"), (119, "SJRES9")]


def test_each_theme_selects_by_the_titles_the_law_prescribes():
    R = {r["id"]: r for r in chains.RULES}
    assert R["reconciliation"]["test"].search("An act to provide for reconciliation pursuant to title II of H. Con. Res. 14.")
    assert not R["reconciliation"]["test"].search("To create a point of order against any reconciliation bill that would not decrease the cost of housing.")
    assert R["reconciliation"]["subject"]("An act to provide for reconciliation pursuant to title II of H. Con. Res. 14.") == "H. Con. Res. 14"
    e = "A joint resolution terminating the national emergency declared to impose duties on articles imported from Canada."
    assert R["emergencies"]["test"].search(e) and R["emergencies"]["area"](e) == "trade-industry-ag"
    assert R["emergencies"]["area"]("A joint resolution terminating the national emergency declared with respect to energy.") == "energy-environment"
    w = ("A joint resolution to direct the removal of United States Armed Forces from hostilities within or against the "
         "Islamic Republic of Iran that have not been authorized by Congress.")
    assert R["war-powers"]["test"].search(w) and R["war-powers"]["subject"](w) == "Islamic Republic of Iran"
    assert not R["war-powers"]["test"].search("To limit the provision of support by the Armed Forces to civilian law enforcement activities.")
    ids = [r["id"] for r in chains.RULES]
    assert ids == ["arms-sales", "rules-overturned", "reconciliation", "emergencies", "war-powers"]   # the index's order


def test_war_powers_heading_keeps_the_titles_article():
    v = {"votes": [{"id": "s-119-1-200", "chamber": "senate", "congress": 119, "bill": "SJRES59", "date": "2025-06-27",
                    "title": "A joint resolution to direct the removal of United States Armed Forces from hostilities against the "
                             "Islamic Republic of Iran that have not been authorized by Congress.",
                    "question": "On the Motion to Discharge", "result": "Rejected"}],
         "positions": {"s-119-1-200": {f"S{i}": "Yea" for i in range(12)}}}
    c = chains.build(_members(), {}, {"years": [2025]}, v, {}, {}, {}, spend={"quarters": {}}, bill_info={"bills": {}})[0]
    assert c["heading"] == "removing US forces from hostilities with the Islamic Republic of Iran"
    assert c["area"] == "defense-foreign" and c["template"] == "chain_rule.html"
