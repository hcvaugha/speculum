"""Campaign money from the OpenFEC API.

For each member: the candidate's totals for the cycle that funds their current seat, their
principal campaign committee, and the committees (PACs, party and others) that gave the most.

Cycles: House members raise money over two years, so their figures cover 2025-2026. Senators
raise over six years, so their figures cover the full election cycle for their seat's next
election (class 1: 2025-2030, class 2: 2021-2026, class 3: 2023-2028).

Totals change only when a new report is filed, so a member's committee breakdown is fetched
again only when their coverage date moves.
"""
from __future__ import annotations

from datetime import date

BASE = "https://api.open.fec.gov/v1"
NEXT_ELECTION = {1: 2030, 2: 2026, 3: 2028}   # Senate classes as of the 2026 election


def election_cycle(m: dict, today: date) -> tuple[int, str]:
    """The cycle (election year) whose full-cycle totals fund the member's current seat."""
    if m["chamber"] == "senate":
        nxt = NEXT_ELECTION.get(m.get("senate_class"), today.year + (today.year % 2))
        # After a class's election year has passed, its senators begin a new six-year cycle.
        while nxt < today.year:
            nxt += 6
        return nxt, f"{nxt - 5}–{nxt} cycle"
    year = today.year + (today.year % 2)
    return year, f"{year - 1}–{year} cycle"


def _ids_for(m: dict) -> list[str]:
    prefix = "S" if m["chamber"] == "senate" else "H"
    ids = [i for i in m.get("fec_ids", []) if i.startswith(prefix)]
    return ids or list(m.get("fec_ids", []))


def _num(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_totals(row: dict) -> dict:
    return {
        "small": _num(row.get("individual_unitemized_contributions")),
        "large": _num(row.get("individual_itemized_contributions")),
        "pac": _num(row.get("other_political_committee_contributions")),
        "party": _num(row.get("political_party_committee_contributions")),
        "self": _num(row.get("candidate_contribution")) + _num(row.get("loans_made_by_candidate")),
        "receipts": _num(row.get("receipts")),
        "disbursements": _num(row.get("disbursements")),
        "cash_on_hand": _num(row.get("last_cash_on_hand_end_period") or row.get("cash_on_hand_end_period")),
        "coverage_end": (row.get("coverage_end_date") or row.get("last_report_date") or "")[:10] or None,
        "last_report": row.get("last_report_type_full"),
    }


def _get(fetch, key, name, path, **params):
    return fetch.get_json("fec", name, f"{BASE}{path}", params={"api_key": key, **params})


def _totals(fetch, key, cand: str, cycle: int) -> dict | None:
    data = _get(fetch, key, f"totals_{cand}_{cycle}.json", f"/candidate/{cand}/totals/",
                cycle=cycle, election_full="true", per_page=20)
    rows = [r for r in data.get("results", []) if r.get("cycle") in (cycle, None)] or data.get("results", [])
    return rows[0] if rows else None


def _principal_committee(fetch, key, cand: str, cycle: int) -> dict | None:
    data = _get(fetch, key, f"committees_{cand}_{cycle}.json", f"/candidate/{cand}/committees/",
                cycle=cycle, designation="P", per_page=20)
    rows = data.get("results", [])
    return {"id": rows[0]["committee_id"], "name": rows[0].get("name")} if rows else None


def _top_committees(fetch, key, committee_id: str, cycle: int, years: list[int], top_n: int) -> list[dict]:
    """Committees that gave to the campaign, aggregated by giving committee across the cycle."""
    agg: dict[str, dict] = {}
    for period in years:
        data = _get(fetch, key, f"givers_{committee_id}_{period}.json", "/schedules/schedule_b/by_recipient_id/",
                    recipient_id=committee_id, cycle=period, per_page=100, sort="-total")
        for r in data.get("results", []):
            cid = r.get("committee_id")
            a = agg.setdefault(cid, {"committee_id": cid, "name": r.get("committee_name") or cid, "amount": 0.0, "count": 0})
            a["amount"] += _num(r.get("total"))
            a["count"] += int(r.get("count") or 0)
    return sorted((a for a in agg.values() if a["amount"] > 0), key=lambda a: -a["amount"])[:top_n]


def fetch_finance(fetch, key: str, members: list[dict], run_date: str, previous: dict | None = None, top_n: int = 10) -> dict:
    today = date.fromisoformat(run_date)
    previous = previous or {}
    out = {}
    for m in members:
        cycle, label = election_cycle(m, today)
        best = None
        for cand in _ids_for(m):
            row = _totals(fetch, key, cand, cycle)
            if row and (best is None or _num(row.get("receipts")) > _num(best[1].get("receipts"))):
                best = (cand, row)
        if not best:
            continue
        cand, row = best
        rec = {"candidate_id": cand, "cycle": cycle, "cycle_label": label, **parse_totals(row)}
        prev = previous.get(m["bioguide"], {})
        if prev.get("coverage_end") == rec["coverage_end"] and prev.get("cycle") == cycle and "top_committees" in prev:
            rec["committee_id"], rec["committee_name"] = prev.get("committee_id"), prev.get("committee_name")
            rec["top_committees"] = prev["top_committees"]
        else:
            pc = _principal_committee(fetch, key, cand, cycle)
            if pc:
                rec["committee_id"], rec["committee_name"] = pc["id"], pc["name"]
                periods = list(range(cycle - (4 if m["chamber"] == "senate" else 0), cycle + 1, 2))
                periods = [p for p in periods if p <= today.year + (today.year % 2)]
                rec["top_committees"] = _top_committees(fetch, key, pc["id"], cycle, periods, top_n)
        out[m["bioguide"]] = rec
    return out
