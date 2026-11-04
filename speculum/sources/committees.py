"""Committee seats from unitedstates/congress-legislators (public domain): who sits where.

Used as a control: members of the committees that oversee defense and foreign affairs draw money
from those industries whatever they vote, so a money pattern that holds only because of them is a
committee effect, not a vote effect.
"""
from __future__ import annotations

MEMBERSHIP = "https://unitedstates.github.io/congress-legislators/committee-membership-current.json"
COMMITTEES = "https://unitedstates.github.io/congress-legislators/committees-current.json"

# Senate committees whose seats bear on arms sales: Armed Services, Foreign Relations, and the
# Appropriations subcommittee on Defense (found by name, since subcommittee codes can change).
SENATE_DEFENSE = {"SSAS": "Armed Services", "SSFR": "Foreign Relations"}


def normalize(membership: dict, committees: list[dict]) -> dict:
    names, codes = {}, {}
    for c in committees:
        tid = c.get("thomas_id")
        if not tid:
            continue
        names[tid] = c.get("name")
        for s in c.get("subcommittees") or []:
            sid = f"{tid}{s.get('thomas_id', '')}"
            names[sid] = f"{c.get('name')}: {s.get('name')}"
            if tid == "SSAP" and (s.get("name") or "").strip().lower() == "defense":
                codes["SSAP-DEFENSE"] = sid
    defense = dict(SENATE_DEFENSE)
    if "SSAP-DEFENSE" in codes:
        defense[codes["SSAP-DEFENSE"]] = "Appropriations: Defense"
    seats: dict[str, list[dict]] = {}
    for code, people in (membership or {}).items():
        for p in people or []:
            bio = p.get("bioguide")
            if bio:
                seats.setdefault(bio, []).append({"code": code, "name": names.get(code, code),
                                                  "title": p.get("title"), "rank": p.get("rank")})
    return {"seats": seats, "defense_codes": defense}


def fetch_committees(fetch) -> dict:
    membership = fetch.get_json("legislators", "committee-membership-current.json", MEMBERSHIP)
    committees = fetch.get_json("legislators", "committees-current.json", COMMITTEES)
    return normalize(membership, committees)


def on_defense_committee(bio: str, data: dict) -> list[str]:
    codes = (data or {}).get("defense_codes", {})
    return [codes[s["code"]] for s in (data or {}).get("seats", {}).get(bio, []) if s["code"] in codes]
