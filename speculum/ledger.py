"""Ledger readings computed from a snapshot. Descriptive only: every reading is a rate or a
dollar figure with a source, compared with the chamber and the member's party.

Readings available in this first version:
  funding      shares of contributions from small donors, larger individual donors, PACs,
               party committees and the candidate (FEC)
  lobbyist     dollars reported by registered lobbyists on LD-203 forms honoring the member
  unity        on party-unity votes, how often the member votes with their party's majority
  attendance   share of roll calls the member voted on

Party unity follows the standard definition: a vote where a majority of Democrats opposed a
majority of Republicans. Independents are counted with the party they caucus with.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median

YES = {"Yea", "Aye", "Yes"}
NO = {"Nay", "No"}

FUNDING_PARTS = [
    ("small", "Small donors", "Unitemized individual contributions ($200 or less per donor per cycle)"),
    ("large", "Larger individual donors", "Itemized individual contributions"),
    ("pac", "PACs and other committees", "Contributions from political action committees and other non-party committees"),
    ("party", "Party committees", "Contributions from party committees"),
    ("self", "The candidate", "Contributions and loans from the candidate"),
]


def side(position: str | None) -> str | None:
    if position in YES:
        return "Y"
    if position in NO:
        return "N"
    return None


def caucus_party(m: dict) -> str:
    return m.get("caucus") or m["party"]


def funding_shares(fin: dict | None) -> dict | None:
    if not fin:
        return None
    parts = {k: max(float(fin.get(k) or 0), 0.0) for k, _, _ in FUNDING_PARTS}
    total = sum(parts.values())
    if total <= 0:
        return None
    return {"total": total, "parts": {k: v / total for k, v in parts.items()}, "dollars": parts}


def party_unity(members: list[dict], votes: dict) -> dict[str, dict]:
    """Returns bioguide -> {unity, unity_votes, attendance, cast, eligible}."""
    by_id = {m["bioguide"]: m for m in members}
    positions = votes.get("positions", {})
    out: dict[str, dict] = defaultdict(lambda: {"with": 0, "unity_votes": 0, "cast": 0, "eligible": 0})
    for v in votes.get("votes", []):
        pos = positions.get(v["id"], {})
        if not pos:
            continue
        tally: dict[str, dict[str, int]] = defaultdict(lambda: {"Y": 0, "N": 0})
        for bio, p in pos.items():
            m = by_id.get(bio)
            s = side(p)
            if m and s:
                tally[caucus_party(m)][s] += 1
        d, r = tally.get("D"), tally.get("R")
        majority = {}
        for party, t in (("D", d), ("R", r)):
            if t and t["Y"] != t["N"]:
                majority[party] = "Y" if t["Y"] > t["N"] else "N"
        is_unity = len(majority) == 2 and majority["D"] != majority["R"]
        for bio, p in pos.items():
            m = by_id.get(bio)
            if not m:
                continue
            rec = out[bio]
            rec["eligible"] += 1
            s = side(p)
            if p not in (None, "Not Voting"):
                rec["cast"] += 1
            party = caucus_party(m)
            if is_unity and s and party in majority:
                rec["unity_votes"] += 1
                if s == majority[party]:
                    rec["with"] += 1
    result = {}
    for bio, rec in out.items():
        result[bio] = {
            "unity": rec["with"] / rec["unity_votes"] if rec["unity_votes"] else None,
            "unity_votes": rec["unity_votes"],
            "attendance": rec["cast"] / rec["eligible"] if rec["eligible"] else None,
            "cast": rec["cast"],
            "eligible": rec["eligible"],
        }
    return result


def percentile(values: list[float], x: float) -> float:
    if not values:
        return 0.0
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return 100.0 * (below + 0.5 * equal) / len(values)


def build(members: list[dict], finance: dict, votes: dict, lobbying: dict) -> dict[str, dict]:
    """Assemble one Ledger per member, with chamber percentiles and party medians."""
    unity = party_unity(members, votes)
    ledgers: dict[str, dict] = {}
    for m in members:
        bio = m["bioguide"]
        fin = finance.get(bio)
        lob = lobbying.get(bio, [])
        ledgers[bio] = {
            "funding": funding_shares(fin),
            "finance": fin,
            "lobbyist_total": sum(float(c.get("amount") or 0) for c in lob),
            "lobbyist_items": sorted(lob, key=lambda c: -float(c.get("amount") or 0)),
            **unity.get(bio, {"unity": None, "unity_votes": 0, "attendance": None, "cast": 0, "eligible": 0}),
        }

    metrics = {
        "small_share": lambda L: L["funding"]["parts"]["small"] if L["funding"] else None,
        "pac_share": lambda L: L["funding"]["parts"]["pac"] if L["funding"] else None,
        "lobbyist_total": lambda L: L["lobbyist_total"],
        "unity": lambda L: L["unity"],
        "attendance": lambda L: L["attendance"],
    }
    for chamber in ("house", "senate"):
        group = [m for m in members if m["chamber"] == chamber]
        for key, get in metrics.items():
            vals = [get(ledgers[m["bioguide"]]) for m in group]
            vals = [v for v in vals if v is not None]
            party_vals: dict[str, list[float]] = defaultdict(list)
            for m in group:
                v = get(ledgers[m["bioguide"]])
                if v is not None:
                    party_vals[caucus_party(m)].append(v)
            for m in group:
                L = ledgers[m["bioguide"]]
                v = get(L)
                L.setdefault("pct", {})[key] = percentile(vals, v) if v is not None else None
                pv = party_vals.get(caucus_party(m))
                L.setdefault("party_median", {})[key] = median(pv) if pv else None
                L.setdefault("party_pct", {})[key] = percentile(vals, median(pv)) if pv else None
                L.setdefault("chamber_median", {})[key] = median(vals) if vals else None
    return ledgers
