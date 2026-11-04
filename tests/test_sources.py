"""Parsers tested against real responses captured from each source on 2026-11-03."""
import json
from datetime import date

from speculum import ledger
from speculum.sources import congress, lda, legislators, senate

CANTWELL = {
    "id": {"bioguide": "C000127", "lis": "S275", "fec": ["S8WA00194", "H2WA01054"]},
    "name": {"first": "Maria", "last": "Cantwell", "official_full": "Maria Cantwell"},
    "terms": [
        {"type": "rep", "start": "1993-01-05", "end": "1995-01-03", "state": "WA", "district": 1, "party": "Democrat"},
        {"type": "sen", "start": "2025-01-03", "end": "2031-01-03", "state": "WA", "class": 1, "party": "Democrat"},
    ],
}
MARKEY = {"id": {"bioguide": "M000133", "lis": "S369", "fec": ["S4MA00028"]},
          "name": {"first": "Edward", "last": "Markey", "nickname": "Ed", "official_full": "Edward J. Markey"},
          "terms": [{"type": "sen", "start": "2021-01-03", "end": "2027-01-03", "state": "MA", "class": 2, "party": "Democrat"}]}
PELOSI = {"id": {"bioguide": "P000197", "fec": ["H8CA05035"]},
          "name": {"first": "Nancy", "last": "Pelosi", "official_full": "Nancy Pelosi"},
          "terms": [{"type": "rep", "start": "2025-01-03", "end": "2027-01-03", "state": "CA", "district": 11, "party": "Democrat"}]}
DELEGATE = {"id": {"bioguide": "N000147"}, "name": {"first": "Eleanor", "last": "Norton"},
            "terms": [{"type": "rep", "start": "2025-01-03", "end": "2027-01-03", "state": "DC", "district": 0, "party": "Democrat"}]}
SANDERS = {"id": {"bioguide": "S000033", "lis": "S313", "fec": ["S4VT00033"]},
           "name": {"first": "Bernard", "last": "Sanders", "nickname": "Bernie"},
           "terms": [{"type": "sen", "start": "2025-01-03", "end": "2031-01-03", "state": "VT", "class": 1,
                      "party": "Independent", "caucus": "Democrat"}]}


def members():
    return legislators.normalize([CANTWELL, MARKEY, PELOSI, DELEGATE, SANDERS])


def test_roster_excludes_delegates_and_reads_current_term():
    ms = {m["bioguide"]: m for m in members()}
    assert "N000147" not in ms
    assert ms["C000127"]["chamber"] == "senate" and ms["C000127"]["lis"] == "S275"
    assert ms["C000127"]["fec_ids"] == ["S8WA00194", "H2WA01054"]
    assert ms["P000197"]["district"] == 11 and ms["P000197"]["chamber"] == "house"
    assert ms["S000033"]["party"] == "I" and ms["S000033"]["caucus"] == "D"


def test_congress_numbering():
    assert congress.congress_for(date(2026, 11, 3)) == (119, [1, 2])
    assert congress.congress_for(date(2027, 2, 1)) == (120, [1])


SENATE_MENU = b"""<?xml version="1.0" encoding="UTF-8"?><vote_summary><congress>119</congress><session>2</session>
<congress_year>2026</congress_year><votes><vote><vote_number>00256</vote_number><vote_date>30-Sep</vote_date>
<issue>PN1129</issue><question>On the Nomination
         </question><result>Confirmed</result><vote_tally><yeas>47</yeas><nays>41</nays></vote_tally>
<title>Confirmation</title></vote><vote><vote_number>00255</vote_number><vote_date>30-Sep</vote_date>
<issue>PN1129</issue><question>On the Cloture Motion</question><result>Agreed to</result></vote></votes></vote_summary>"""

SENATE_VOTE = b"""<?xml version="1.0" encoding="UTF-8"?><roll_call_vote><congress>119</congress><session>2</session>
<congress_year>2026</congress_year><vote_number>1</vote_number><vote_date>January 5, 2026,  05:31 PM</vote_date>
<question>On the Nomination</question><vote_title>Confirmation</vote_title><majority_requirement>1/2</majority_requirement>
<vote_result>Nomination Confirmed</vote_result><document><document_type>PN</document_type><document_number>12-1</document_number>
<document_name>PN12-1</document_name></document><count><yeas>50</yeas><nays>35</nays><present/><absent>15</absent></count>
<members><member><member_full>Cantwell (D-WA)</member_full><last_name>Cantwell</last_name><first_name>Maria</first_name>
<party>D</party><state>WA</state><vote_cast>Nay</vote_cast><lis_member_id>S275</lis_member_id></member>
<member><member_full>Someone (R-XX)</member_full><last_name>Someone</last_name><party>R</party><state>XX</state>
<vote_cast>Yea</vote_cast><lis_member_id>S999</lis_member_id></member></members></roll_call_vote>"""


def test_senate_menu_and_vote():
    assert senate.parse_menu(SENATE_MENU) == [255, 256]
    vote, pos = senate.parse_vote(SENATE_VOTE, {"S275": "C000127"})
    assert vote["date"] == "2026-01-05T17:31:00"
    assert vote["question"] == "On the Nomination" and vote["bill"] == "PN12-1"
    assert pos == {"C000127": "Nay", "lis:S999": "Yea"}


class FakeFetch:
    """Replays recorded responses keyed by URL path; fails loudly on anything unexpected."""
    def __init__(self, responses):
        self.responses = responses
        self.min_interval = {}

    def get_json(self, source, name, url, **kw):
        for frag, body in self.responses.items():
            if url.endswith(frag):
                return body
        raise AssertionError(f"unexpected request {url}")


HOUSE_LIST = {"houseRollCallVotes": [{
    "congress": 119, "identifier": 11912025240, "legislationNumber": "3424", "legislationType": "HR",
    "legislationUrl": "https://www.congress.gov/bill/119/house-bill/3424", "result": "Passed", "rollCallNumber": 240,
    "sessionNumber": 1, "sourceDataURL": "https://clerk.house.gov/evs/2025/roll240.xml",
    "startDate": "2025-09-08T18:56:00-04:00", "updateDate": "2025-09-09T18:53:19-04:00",
    "url": "https://api.congress.gov/v3/house-vote/119/1/240", "voteType": "2/3 Yea-And-Nay"}],
    "pagination": {"count": 1}}
HOUSE_MEMBERS = {"houseRollCallVoteMemberVotes": {"congress": 119, "results": [
    {"bioguideID": "A000055", "firstName": "Robert", "lastName": "Aderholt", "voteCast": "Yea", "voteParty": "R", "voteState": "AL"},
    {"bioguideID": "P000197", "firstName": "Nancy", "lastName": "Pelosi", "voteCast": "Yea", "voteParty": "D", "voteState": "CA"}]}}
HOUSE_ITEM = {"houseRollCallVote": {"voteQuestion": "On Motion to Suspend the Rules and Pass"}}


def test_house_votes_from_recorded_responses():
    fetch = FakeFetch({"/house-vote/119/1": HOUSE_LIST, "/house-vote/119/2": {"houseRollCallVotes": [], "pagination": {"count": 0}},
                       "/house-vote/119/1/240/members": HOUSE_MEMBERS, "/house-vote/119/1/240": HOUSE_ITEM})
    out = congress.fetch_house_votes(fetch, "KEY", None, today=date(2026, 11, 3))
    v = out["votes"][0]
    assert v["id"] == "h-119-1-240" and v["bill"] == "HR 3424"
    assert v["question"] == "On Motion to Suspend the Rules and Pass"
    assert out["positions"]["h-119-1-240"] == {"A000055": "Yea", "P000197": "Yea"}
    # A second night with an unchanged updateDate fetches nothing new.
    again = congress.fetch_house_votes(FakeFetch({"/house-vote/119/1": HOUSE_LIST,
                                                 "/house-vote/119/2": {"houseRollCallVotes": [], "pagination": {"count": 0}}}),
                                       "KEY", out, today=date(2026, 11, 4))
    assert again["positions"] == out["positions"]


LD203 = [
    {"filing_uuid": "46f61aad", "filing_year": 2025, "filing_period": "year_end", "filing_period_display": "Year-End",
     "dt_posted": "2026-01-20T10:00:00-05:00", "filing_document_url": "https://lda.gov/filings/public/contribution/46f61aad/print/",
     "registrant": {"id": 1, "name": "AMERICAN ASSOCIATION FOR JUSTICE"}, "lobbyist": None,
     "contribution_items": [
         {"contribution_type": "feca", "contribution_type_display": "FECA",
          "contributor_name": "AMERICAN ASSOCIATION FOR JUSTICE POLITICAL ACTION COMMITTEE",
          "payee_name": "THE MARKEY COMMITTEE", "honoree_name": "Sen. Ed Markey", "amount": "5000.00", "date": "2025-12-15"},
         {"contribution_type": "feca", "contribution_type_display": "FECA", "contributor_name": "SELF",
          "payee_name": "PELOSI FOR CONGRESS", "honoree_name": "Rep. Nancy Pelosi", "amount": "-2500.00", "date": "2025-11-01"},
         {"contribution_type": "feca", "contribution_type_display": "FECA", "contributor_name": "SELF",
          "payee_name": "NRCC", "honoree_name": "National Republican Congressional Committee", "amount": "6000.00", "date": "2025-01-16"},
         {"contribution_type": "ple", "contribution_type_display": "Presidential Library Expenses", "contributor_name": "SELF",
          "payee_name": "X", "honoree_name": "Sen. Ed Markey", "amount": "100.00", "date": "2025-02-01"}]},
    # An older version of the same report, superseded by the amendment above.
    {"filing_uuid": "old", "filing_year": 2025, "filing_period": "year_end", "dt_posted": "2026-01-10T10:00:00-05:00",
     "registrant": {"id": 1, "name": "AMERICAN ASSOCIATION FOR JUSTICE"}, "lobbyist": None,
     "contribution_items": [{"contribution_type": "feca", "honoree_name": "Sen. Ed Markey", "amount": "9999.00"}]},
]


def test_ld203_matching_amendments_and_types():
    out = lda.match_reports(lda._latest_versions(LD203), members(), [2025, 2026])
    markey = out["by_member"]["M000133"]
    assert [c["amount"] for c in markey] == [5000.0]          # PLE excluded; superseded report ignored
    assert out["by_member"]["P000197"][0]["amount"] == -2500.0  # voids are kept so totals net out
    assert "C000127" not in out["by_member"]


def test_honoree_matching_is_conservative():
    m = lda.HonoreeMatcher(members())
    assert m.match("Sen. Ed Markey")["bioguide"] == "M000133"
    assert m.match("Senator Bernie Sanders")["bioguide"] == "S000033"
    assert m.match("Rep. Nancy Pelosi")["bioguide"] == "P000197"
    assert m.match("National Republican Congressional Committee") is None
    assert m.match("Rep. Unknown Person") is None


def test_party_unity_counts_independents_with_caucus():
    ms = members()
    votes = {"votes": [{"id": "v1"}, {"id": "v2"}],
             "positions": {
                 "v1": {"C000127": "Nay", "M000133": "Nay", "S000033": "Yea", "X1": "Yea"},
                 "v2": {"C000127": "Yea", "M000133": "Yea", "S000033": "Not Voting"}}}
    ms.append({"bioguide": "X1", "party": "R", "caucus": "R", "chamber": "senate"})
    u = ledger.party_unity(ms, votes)
    # v1 splits the parties (D majority Nay, R majority Yea); Sanders broke with Democrats.
    assert u["S000033"]["unity"] == 0.0 and u["C000127"]["unity"] == 1.0
    assert u["S000033"]["attendance"] == 0.5


from speculum.sources import fec

TOTALS = {"results": [{"candidate_id": "S4VT00033", "cycle": 2030, "candidate_election_year": 2030, "election_full": True,
    "individual_unitemized_contributions": 3000000, "individual_itemized_contributions": 2000000,
    "other_political_committee_contributions": 100000, "political_party_committee_contributions": 0,
    "candidate_contribution": 0, "loans_made_by_candidate": 0, "receipts": 5200000,
    "last_cash_on_hand_end_period": 900000, "coverage_end_date": "2026-09-30T00:00:00"}]}
COMMITTEES = {"results": [{"committee_id": "C00411330", "name": "FRIENDS OF BERNIE SANDERS", "cycles": [2024, 2026, 2028, 2030]}]}
SCHED_A = {"results": [
    {"contributor_id": "C00000001", "contributor_name": "EXAMPLE UNION PAC", "entity_type": "PAC", "contribution_receipt_amount": 5000},
    {"contributor_id": "C00000001", "contributor_name": "EXAMPLE UNION PAC", "entity_type": "PAC", "contribution_receipt_amount": 2500},
    {"contributor_id": "C00000002", "contributor_name": "MEMO ROW", "entity_type": "PAC", "contribution_receipt_amount": 9999, "memo_code": "X"},
    {"contributor_id": None, "contributor_name": "Jane Doe", "entity_type": "IND", "contribution_receipt_amount": 100}],
    "pagination": {"last_indexes": None}}


def test_fec_totals_and_top_committees():
    sanders = [m for m in members() if m["bioguide"] == "S000033"]
    fetch = FakeFetch({"/candidate/S4VT00033/totals/": TOTALS, "/candidate/S4VT00033/committees/": COMMITTEES,
                       "/schedules/schedule_a/": SCHED_A})
    out = fec.fetch_finance(fetch, "KEY", sanders, "2026-11-03")["S000033"]
    assert out["cycle"] == 2030 and out["cycle_label"] == "2025–2030 cycle"
    assert out["small"] == 3e6 and out["pac"] == 1e5 and out["cash_on_hand"] == 9e5
    assert out["coverage_end"] == "2026-09-30"
    assert out["committee_id"] == "C00411330"
    assert out["top_committees"] == [{"committee_id": "C00000001", "name": "Example Union Pac", "entity_type": "PAC",
                                      "amount": 7500.0, "count": 2}]
    shares = ledger.funding_shares(out)
    assert round(shares["parts"]["small"], 3) == round(3e6 / 5.1e6, 3)


def test_senate_cycles():
    assert fec.election_cycle({"chamber": "senate", "senate_class": 2}, date(2026, 11, 3)) == (2026, "2021–2026 cycle")
    assert fec.election_cycle({"chamber": "senate", "senate_class": 2}, date(2027, 2, 1))[0] == 2032
    assert fec.election_cycle({"chamber": "house"}, date(2026, 11, 3)) == (2026, "2025–2026 cycle")


def test_tide_edition_reports_party_breaks(tmp_path):
    from speculum import tide
    from speculum.snapshot import Snapshot
    ms = members() + [{"bioguide": "X1", "name": "Senator X", "sort_name": "X, S", "party": "R", "caucus": "R",
                       "state": "XX", "chamber": "senate"}]
    prev = Snapshot(tmp_path, "2026-11-03")
    prev.write("members.json", ms)
    prev.write("votes.json.gz", {"votes": [], "positions": {}})
    cur = Snapshot(tmp_path, "2026-11-04")
    cur.write("members.json", ms)
    cur.write("votes.json.gz", {"votes": [{"id": "s-119-2-300", "chamber": "senate", "number": 300, "date": "2026-11-04T12:00:00",
                                           "question": "On Passage of the Bill", "result": "Passed", "bill": "S. 1", "url": "https://www.senate.gov/x"}],
                                "positions": {"s-119-2-300": {"C000127": "Nay", "M000133": "Nay", "S000033": "Yea", "X1": "Yea"}}})
    ed = tide.build_edition(cur, prev)
    assert ed["roll_calls"] == 1 and not ed["first"]
    s = ed["stories"][0]
    assert s["kind"] == "Broke ranks" and s["members"] == ["S000033"]
    assert s["headline"] == "1 member voted against their party on S. 1"
    assert s["facts"][0] == "Question: On Passage of the Bill."
    assert s["status"] == "draft" and s["sources"][0]["url"] == "https://www.senate.gov/x"


def test_interests_aggregate_split_and_link():
    from speculum import interests
    ms = members()
    finance = {
        "C000127": {"cycle_label": "x", "top_committees": [{"committee_id": "C001", "name": "American Association For Justice Political Action Committee", "entity_type": "PAC", "amount": 5000, "count": 1}]},
        "P000197": {"cycle_label": "x", "top_committees": [{"committee_id": "C001", "name": "American Association For Justice Political Action Committee", "entity_type": "PAC", "amount": 3000, "count": 2}]},
    }
    lob = lda.match_reports(lda._latest_versions([{**LD203[0], "pacs": ["American Association for Justice Political Action Committee"]}]), ms, [2025])
    out = interests.build(ms, finance, lob)
    pac = out["c-C001"]
    assert pac["total"] == 8000 and pac["members"] == 2 and pac["split"] == {"D": 1.0}
    assert pac["rows"][0]["bioguide"] == "C000127"
    reg = out["r-1"]
    assert reg["kind"] == "registrant" and reg["total"] == 2500.0   # 5000 to Markey, -2500 void to Pelosi
    assert reg["linked_slugs"] == ["c-C001"] and pac["linked_slugs"] == ["r-1"]


def test_finance_stops_at_deadline_and_carries_forward():
    import time
    sanders = [m for m in members() if m["bioguide"] == "S000033"]
    pelosi = [m for m in members() if m["bioguide"] == "P000197"]
    prev = {"P000197": {"candidate_id": "H8CA05035", "receipts": 1, "coverage_end": "2026-09-30"}}
    out = fec.fetch_finance(FakeFetch({}), "KEY", sanders + pelosi, "2026-11-03", previous=prev, deadline=time.monotonic() - 1)
    assert out["P000197"]["carried_forward"] is True
    assert "S000033" not in out and out["_partial"] == {"reached": 0, "of": 2}


def test_voteview_fallback_when_senate_refuses():
    import requests
    from datetime import date
    from speculum.sources import senate

    rollcalls = ("congress,chamber,rollnumber,date,session,clerk_rollnumber,yea_count,nay_count,bill_number,vote_result,vote_desc,vote_question,dtl_desc\n"
                 "119,Senate,1,2025-01-07,1,1,52,47,S5,Agreed to,A bill,On the Motion,\n"
                 "119,Senate,700,2026-01-06,2,3,50,50,,Rejected,Nomination,On the Cloture Motion,\n")
    votes = "congress,chamber,rollnumber,icpsr,cast_code,prob\n119,Senate,1,41301,1,99\n119,Senate,1,41302,6.0,99\n119,Senate,700,41301,9,\n119,Senate,700,41303,1,\n"

    class F:
        def get(self, source, name, url, params=None, headers=None, retries=5):
            if "senate.gov" in url:
                r = requests.Response(); r.status_code = 403
                raise requests.HTTPError("403 Forbidden", response=r)
            if "members" in url:
                return b"congress,chamber,icpsr,bioname,bioguide_id\n119,Senate,41303,\"NEW, Senator\",C000003\n"
            return (rollcalls if "rollcalls" in url else votes).encode()

    members = [{"bioguide": "A000001", "icpsr": "41301", "lis": "S1"}, {"bioguide": "B000002", "icpsr": "41302", "lis": "S2"}]
    out = senate.fetch_senate_votes(F(), members, None, today=date(2026, 11, 3))
    assert out["source"] == "voteview"
    assert [v["id"] for v in out["votes"]] == ["s-119-1-1", "s-119-2-3"]
    assert out["positions"]["s-119-1-1"] == {"A000001": "Yea", "B000002": "Nay"}
    assert out["positions"]["s-119-2-3"] == {"A000001": "Not Voting", "C000003": "Yea"}
    assert out["votes"][0]["bill"] == "S5"


def test_lda_tolerates_blank_lobbyist_names():
    from speculum.sources import lda
    members = [{"bioguide": "S000001", "first": "Jane", "last": "Smith", "nickname": None, "name": "Jane Smith",
                "chamber": "senate", "state": "OH", "party": "D"}]
    reports = [{"registrant": {"id": 7, "name": "ACME LOBBYING"}, "lobbyist": {"first_name": None, "last_name": "DOE"},
                "contribution_items": [{"contribution_type": "feca", "honoree_name": "Sen. Jane Smith", "amount": None}]},
               {"registrant": {}, "lobbyist": None, "contribution_items": []}]
    out = lda.match_reports(reports, members, [2026])
    assert out["reports"] == 2
    for items in out["by_member"].values():
        assert items[0]["reported_by"] == "Doe (Acme Lobbying)"
