"""Advocacy money: what PACs of organizations that took a side on a decision gave the members who
voted on it, with both sides counted by the same rule (data/advocacy.json).

Counted from the giving PAC's side (FEC Schedule B), because conduit PACs such as AIPAC PAC and
JStreetPAC forward earmarked individual gifts: on the recipient's own report those appear as
individual contributions, so a recipient-side count of committee money misses them. On the conduit's
Schedule B each forwarded earmark is a regular itemized disbursement, so summing non-memo rows per
recipient committee captures direct gifts and earmarks once each.

Super PAC spending (Schedule E) is independent of the candidate and kept apart from contributions.

Past cycles are fetched once and carried forward; only the current cycle is fetched again.
"""
from __future__ import annotations

import logging
import re
from datetime import date

from .fec import _get, _num

log = logging.getLogger("speculum.advocacy")
_EARMARK = re.compile(r"earmark|conduit", re.I)


def current_cycle(d: date) -> int:
    return d.year if d.year % 2 == 0 else d.year + 1


class _Pager:
    """Keyset pages from an FEC schedule; .complete says whether it reached the end."""
    def __init__(self, fetch, key, name, path, params, max_pages, deadline=None):
        self.args = (fetch, key, name, path, params, max_pages, deadline)
        self.complete = False

    def __iter__(self):
        import time
        fetch, key, name, path, params, max_pages, deadline = self.args
        for page in range(max_pages):
            if deadline is not None and time.monotonic() > deadline:
                log.warning("%s: stopped at the time budget after %d pages", name, page)
                return
            data = _get(fetch, key, f"{name}_p{page}.json", path, **params)
            rows = data.get("results", [])
            yield from rows
            last = (data.get("pagination") or {}).get("last_indexes")
            if not rows or not last:
                self.complete = True
                return
            params = {**params, **last}
        log.warning("%s: hit the page cap", name)


def pac_disbursements(fetch, key, committee_id: str, cycle: int, max_pages: int = 1500, deadline=None) -> dict:
    """{"complete": bool, "rows": {recipient committee id: {"total", "earmarked", "direct", "count", "name"}}}"""
    out: dict[str, dict] = {}
    params = {"committee_id": committee_id, "two_year_transaction_period": cycle, "per_page": 100,
              "sort": "-disbursement_date"}
    pager = _Pager(fetch, key, f"sched_b_{committee_id}_{cycle}", "/schedules/schedule_b/", params, max_pages, deadline)
    for r in pager:
        if r.get("memo_code") == "X" or r.get("memoed_subtotal"):
            continue
        rid = r.get("recipient_committee_id")
        if not rid:
            continue
        amt = _num(r.get("disbursement_amount"))
        e = out.setdefault(rid, {"total": 0.0, "earmarked": 0.0, "direct": 0.0, "count": 0, "name": r.get("recipient_name")})
        e["total"] += amt
        e["earmarked" if _EARMARK.search(r.get("disbursement_description") or "") else "direct"] += amt
        e["count"] += 1
    return {"complete": pager.complete, "rows": out}


def super_pac_spending(fetch, key, committee_id: str, cycle: int, max_pages: int = 500, deadline=None) -> dict:
    """{candidate id: {"support", "oppose"}} for one super PAC and cycle. The same expenditure can
    appear on a 24/48-hour notice and again on the periodic report; each is counted once."""
    seen, out = set(), {}
    params = {"committee_id": committee_id, "cycle": cycle, "per_page": 100, "sort": "-expenditure_date"}
    pager = _Pager(fetch, key, f"sched_e_{committee_id}_{cycle}", "/schedules/schedule_e/", params, max_pages, deadline)
    for r in pager:
        if r.get("memo_code") == "X":
            continue
        cand = r.get("candidate_id")
        if not cand:
            continue
        amt = _num(r.get("expenditure_amount"))
        sig = (cand, r.get("expenditure_date") or r.get("dissemination_date"), round(amt, 2), (r.get("payee_name") or "").upper(),
               r.get("expenditure_description"))
        if sig in seen:
            continue
        seen.add(sig)
        e = out.setdefault(cand, {"support": 0.0, "oppose": 0.0})
        e["support" if r.get("support_oppose_indicator") == "S" else "oppose"] += amt
    return {"complete": pager.complete, "rows": out}


def fetch_advocacy(fetch, key: str, config: dict, run_date: str, previous: dict | None = None, deadline=None) -> dict:
    previous = previous or {}
    cur = current_cycle(date.fromisoformat(run_date))
    cycles = [c for c in config.get("cycles", [2020, 2022, 2024, 2026]) if c <= cur]
    out = {"cycles": cycles, "fetched": run_date, "pacs": {}, "super_pacs": {}}
    for org in config.get("organizations", []):
        for p in org.get("pacs", []):
            cid = p["fec_id"]
            for c in cycles:
                prior = (previous.get("pacs", {}).get(cid) or {}).get(str(c))
                if c < cur and prior and prior.get("complete"):
                    data = prior
                else:
                    try:
                        data = pac_disbursements(fetch, key, cid, c, deadline=deadline)
                    except Exception as exc:   # an FEC outage costs one cycle, not the whole source
                        log.warning("advocacy %s %s failed (%s); keeping prior", cid, c, exc)
                        data = {**(prior or {"rows": {}}), "complete": False}
                out["pacs"].setdefault(cid, {})[str(c)] = data
            log.info("advocacy PAC %s: %d recipients; complete cycles %s", cid,
                     len({r for c in out["pacs"][cid].values() for r in c["rows"]}),
                     [c for c, v in out["pacs"][cid].items() if v["complete"]])
        for sp in org.get("super_pacs", []):
            cid = sp["fec_id"]
            for c in cycles:
                prior = (previous.get("super_pacs", {}).get(cid) or {}).get(str(c))
                if c < cur and prior and prior.get("complete"):
                    data = prior
                else:
                    try:
                        data = super_pac_spending(fetch, key, cid, c, deadline=deadline)
                    except Exception as exc:
                        log.warning("super PAC %s %s failed (%s); keeping prior", cid, c, exc)
                        data = {**(prior or {"rows": {}}), "complete": False}
                out["super_pacs"].setdefault(cid, {})[str(c)] = data
    return out


def member_totals(bio: str, member: dict, finance: dict, config: dict, data: dict, bill: str) -> dict:
    """Money to one member from the organizations on each side of one bill, summed over all cycles.
    PAC money is matched to the member's principal campaign committee; super PAC spending to their
    FEC candidate IDs. Returns None values where the member's committee is not yet known."""
    fin = finance.get(bio) or {}
    committee = fin.get("committee_id")
    cands = set(member.get("fec_ids") or []) | ({fin["candidate_id"]} if fin.get("candidate_id") else set())
    res = {"block": 0.0, "proceed": 0.0, "known": bool(committee), "ie_support": 0.0, "ie_oppose": 0.0, "by_org": {},
           "complete": True}
    for org in config.get("organizations", []):
        side = org.get("sides", {}).get(bill)
        if not side or bill in org.get("after_only", []):   # praise after the vote is not counted
            continue
        amt = 0.0
        for p in org.get("pacs", []):
            for cyc in (data.get("pacs", {}).get(p["fec_id"]) or {}).values():
                res["complete"] &= bool(cyc.get("complete"))
                if committee and committee in cyc["rows"]:
                    amt += cyc["rows"][committee]["total"]
        if amt:
            res[side] += amt
            res["by_org"][org["name"]] = amt
        if side == "proceed" or side == "block":
            for sp in org.get("super_pacs", []):
                for cyc in (data.get("super_pacs", {}).get(sp["fec_id"]) or {}).values():
                    for cand in cands:
                        if cand in cyc["rows"]:
                            res["ie_support"] += cyc["rows"][cand]["support"]
                            res["ie_oppose"] += cyc["rows"][cand]["oppose"]
    return res
