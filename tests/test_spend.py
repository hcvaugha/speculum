from datetime import date

from speculum.sources import lobbyspend


def _f(reg, cli, own, income=None, expenses=None, codes=("DEF",), posted="2025-04-01"):
    return {"registrant": {"id": reg, "name": f"R{reg}"}, "client": {"id": cli, "name": f"C{cli}", "client_self_select": own},
            "income": income, "expenses": expenses, "dt_posted": posted,
            "lobbying_activities": [{"general_issue_code": c, "general_issue_code_display": c.title()} for c in codes]}


def test_no_double_counting_and_equal_split_and_amendments():
    filings = [
        _f(1, 10, True, expenses="100000", codes=("DEF", "BUD")),        # company lobbies for itself: 100k
        _f(2, 10, False, income="40000", codes=("DEF",)),                # its outside firm: inside the 100k
        _f(3, 20, False, income="30000", codes=("HCR",)),                 # a firm for a client with no own report
        _f(3, 20, False, income="50000", codes=("HCR",), posted="2025-05-01"),   # amendment replaces it
        _f(4, 30, False, income=None, codes=("TAX",)),                    # under the reporting threshold
    ]
    a = lobbyspend.aggregate(filings)
    assert a["total"] == 150000 and a["reports"] == 4
    assert a["issues"]["DEF"]["amount"] == 50000 and a["issues"]["BUD"]["amount"] == 50000
    assert a["issues"]["HCR"]["amount"] == 50000 and a["issues"]["TAX"]["amount"] == 0
    assert a["registrants"]["1"]["issues"] == {"BUD": 50000, "DEF": 50000}
    assert a["registrants"]["3"]["for_clients"] == 1


def test_due_quarters_follow_the_filing_deadlines():
    assert lobbyspend.due_quarters(date(2025, 4, 19)) == []
    assert lobbyspend.due_quarters(date(2025, 4, 20)) == [(2025, 1)]
    assert lobbyspend.due_quarters(date(2026, 11, 6))[-1] == (2026, 3)
    assert len(lobbyspend.due_quarters(date(2026, 11, 6))) == 7
