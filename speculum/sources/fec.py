"""Campaign money from the OpenFEC API.

For each member: the candidate's totals for the cycle that funds their current seat, their
principal campaign committee, and the committees (PACs, party and others) that gave the most,
grouped from itemized receipts (OpenFEC has no per-contributor aggregate).

Cycles: House members raise money over two years, so their figures cover 2025-2026. Senators
raise over six years, so their figures cover the full election cycle for their seat's next
election (class 1: 2025-2030, class 2: 2021-2026, class 3: 2023-2028).

Totals change only when a new report is filed, so a member's committee breakdown is fetched
again only when their coverage date moves.
"""
from __future__ import annotations

from ..names import org_name
import logging
import re
from datetime import date

log = logging.getLogger("speculum.fec")

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


def _totals(fetch, key, cand: str, cycle: int, election_full: bool) -> dict | None:
    data = _get(fetch, key, f"totals_{cand}_{cycle}_{'full' if election_full else '2yr'}.json",
                f"/candidate/{cand}/totals/", cycle=cycle, election_full=str(election_full).lower(), per_page=20)
    rows = data.get("results", [])
    if election_full:
        rows = [r for r in rows if r.get("candidate_election_year") in (cycle, None)] or rows
    else:
        rows = [r for r in rows if r.get("cycle") in (cycle, None)] or rows
    rows = [r for r in rows if r.get("election_full") in (election_full, None)] or rows
    return rows[0] if rows else None


def _principal_committee(fetch, key, cand: str, cycle: int) -> dict | None:
    """The candidate's principal campaign committee. A senator not up until a later election has a
    full-cycle label (e.g. 2028) that no committee lists yet among its two-year periods, so when the
    cycle query finds nothing, ask again without it and take the committee active most recently."""
    data = _get(fetch, key, f"committees_{cand}_{cycle}.json", f"/candidate/{cand}/committees/",
                cycle=cycle, designation="P", per_page=20)
    rows = data.get("results", [])
    rows = [r for r in rows if cycle in (r.get("cycles") or [])] or rows
    if not rows:
        data = _get(fetch, key, f"committees_{cand}_any.json", f"/candidate/{cand}/committees/",
                    designation="P", per_page=20)
        rows = sorted(data.get("results", []), key=lambda r: max(r.get("cycles") or [0]), reverse=True)
    return {"id": rows[0]["committee_id"], "name": rows[0].get("name")} if rows else None


def _top_committees(fetch, key, committee_id: str, periods: list[int], top_n: int, max_pages: int = 8) -> list[dict]:
    """Group itemized receipts from committees (PACs, party, other campaigns) by giving committee.
    Memo rows are skipped so earmarked pass-throughs are not double counted."""
    agg: dict[str, dict] = {}
    params = {"committee_id": committee_id, "two_year_transaction_period": periods, "contributor_type": "committee",
              "sort": "-contribution_receipt_amount", "per_page": 100}
    for page in range(max_pages):
        data = _get(fetch, key, f"sched_a_{committee_id}_p{page}.json", "/schedules/schedule_a/", **params)
        rows = data.get("results", [])
        for r in rows:
            if r.get("memo_code") == "X" or r.get("memoed_subtotal") or r.get("entity_type") == "IND":
                continue
            cid = r.get("contributor_id") or (r.get("contributor_name") or "").upper()
            if not cid:
                continue
            a = agg.setdefault(cid, {"committee_id": r.get("contributor_id"), "name": org_name(r.get("contributor_name") or cid),
                                     "entity_type": r.get("entity_type"), "amount": 0.0, "count": 0})
            a["amount"] += _num(r.get("contribution_receipt_amount"))
            a["count"] += 1
        last = (data.get("pagination") or {}).get("last_indexes")
        if not rows or not last:
            break
        params = {**params, **last}
    return sorted((a for a in agg.values() if a["amount"] > 0), key=lambda a: -a["amount"])[:top_n]


def fetch_finance(fetch, key: str, members: list[dict], run_date: str, previous: dict | None = None, top_n: int = 50,
                  deadline: float | None = None) -> dict:
    """With a deadline (time.monotonic() value), stops fetching when time runs out and carries
    forward the previous night's figures for members it did not reach. Members missing from the
    previous snapshot go first, so an interrupted first run is completed on the next night."""
    import time
    today = date.fromisoformat(run_date)
    previous = previous or {}
    out = {}
    failures = 0
    order = sorted(members, key=lambda m: m["bioguide"] in previous)
    for idx, m in enumerate(order):
        if deadline is not None and time.monotonic() > deadline:
            for rest in order[idx:]:
                if rest["bioguide"] in previous:
                    out[rest["bioguide"]] = {**previous[rest["bioguide"]], "carried_forward": True}
            out["_partial"] = {"reached": idx, "of": len(order)}
            break
        try:
            cycle, label = election_cycle(m, today)
            best = None
            for cand in _ids_for(m):
                row = _totals(fetch, key, cand, cycle, election_full=True)
                if row and (best is None or _num(row.get("receipts")) > _num(best[1].get("receipts"))):
                    best = (cand, row)
            if not best and m["chamber"] == "senate":
                # Fallback when no full-cycle row exists yet for a seat not up this year: the current two years.
                cycle = today.year + (today.year % 2)
                label = f"{cycle - 1}–{cycle} only"
                for cand in _ids_for(m):
                    row = _totals(fetch, key, cand, cycle, election_full=False)
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
                    span = 4 if (m["chamber"] == "senate" and not label.endswith("only")) else 0
                    periods = [p for p in range(cycle - span, cycle + 1, 2) if p <= today.year + (today.year % 2)]
                    rec["top_committees"] = _top_committees(fetch, key, pc["id"], periods, top_n)
            out[m["bioguide"]] = rec
        except Exception as exc:   # one member's failed request never costs the night everyone else's figures
            failures += 1
            log.warning("finance for %s failed (%s); carrying forward", m["bioguide"], exc)
            if m["bioguide"] in previous:
                out[m["bioguide"]] = {**previous[m["bioguide"]], "carried_forward": True}
            if failures >= 25:       # the API itself is down: stop asking, keep what we have
                for rest in order[idx + 1:]:
                    if rest["bioguide"] in previous:
                        out[rest["bioguide"]] = {**previous[rest["bioguide"]], "carried_forward": True}
                out["_partial"] = {"reached": idx + 1, "of": len(order), "reason": "FEC API errors"}
                break
    return out


_OWN_WORDS = re.compile(r"\b(team|victory|leadership|majority|fund|committee|for congress|for senate|friends|pac|"
                        r"builders|action|alliance)\b", re.I)


def is_own_money(giver: dict, member: dict) -> bool:
    """A member's own money is not an outside interest: the candidate's personal contributions and
    loans (entity type CAN), and transfers from committees named for the member (their joint
    fundraising and leadership committees, e.g. "Team Scalise", "Emmer Victory Committee")."""
    if giver.get("entity_type") == "CAN":
        return True
    last = re.sub(r"[^a-z ]", "", (member.get("last") or "").lower().replace("-", " ")).strip()
    name = (giver.get("name") or "")
    words = " " + re.sub(r"[^a-z]+", " ", name.lower().replace("'s", "")) + " "
    return bool(len(last) > 3 and f" {last} " in words and giver.get("entity_type") in ("COM", "CCM", "PAC", None)
                and _OWN_WORDS.search(name))


def clean_finance(finance: dict, members: list[dict]) -> dict:
    """Drop members' own money from their lists of committee givers. Idempotent."""
    by_id = {m["bioguide"]: m for m in members}
    out = {}
    for bio, f in finance.items():
        m = by_id.get(bio)
        if m and f.get("top_committees") and not f.get("_own_removed"):
            kept = [c for c in f["top_committees"] if not is_own_money(c, m)]
            f = {**f, "top_committees": kept, "_own_removed": len(f["top_committees"]) - len(kept)}
        out[bio] = f
    return out
