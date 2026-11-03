"""Roster and ID crosswalk from unitedstates/congress-legislators (public domain).

The file lists every sitting member with their Bioguide, Senate LIS and FEC IDs, which is how
a Senate roll call (LIS IDs) and a campaign account (FEC IDs) are tied to the same person.
Non-voting delegates (DC and the territories) are excluded from the 535 voting members.
"""
from __future__ import annotations

import json

URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
PARTY = {"Democrat": "D", "Republican": "R", "Independent": "I", "Libertarian": "L"}
NON_VOTING = {"DC", "PR", "GU", "VI", "AS", "MP"}


def normalize(records: list[dict]) -> list[dict]:
    out = []
    for r in records:
        term = r["terms"][-1]
        if term["state"] in NON_VOTING:
            continue
        name = r.get("name", {})
        full = name.get("official_full") or f"{name.get('first', '')} {name.get('last', '')}".strip()
        party = PARTY.get(term.get("party"), (term.get("party") or "?")[:1])
        caucus = PARTY.get(term["caucus"]) if term.get("caucus") else None
        out.append({
            "bioguide": r["id"]["bioguide"],
            "lis": r["id"].get("lis"),
            "fec_ids": r["id"].get("fec", []),
            "name": full,
            "first": name.get("first"),
            "last": name.get("last"),
            "nickname": name.get("nickname"),
            "sort_name": f"{name.get('last', '')}, {name.get('first', '')}",
            "party": party,
            "caucus": caucus or (party if party in ("D", "R") else None),
            "state": term["state"],
            "district": term.get("district"),
            "chamber": "senate" if term["type"] == "sen" else "house",
            "senate_class": term.get("class"),
            "start": term.get("start"),
            "end": term.get("end"),
            "first_served": r["terms"][0].get("start"),
            "chamber_since": min(t["start"] for t in r["terms"] if t["type"] == term["type"]),
            "url": term.get("url"),
        })
    out.sort(key=lambda m: (m["chamber"], m["state"], m.get("district") or 0, m["sort_name"]))
    return out


def fetch_members(fetch) -> list[dict]:
    body = fetch.get("legislators", "legislators-current.json", URL)
    return normalize(json.loads(body))
