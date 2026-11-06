"""House roll-call votes from the Congress.gov API (v3, house-vote endpoints).

Covers legislation-related House votes from the 118th Congress onward. Each night only roll
calls that are new or whose updateDate changed are fetched again.
"""
from __future__ import annotations

from datetime import date

BASE = "https://api.congress.gov/v3"


def congress_for(d: date) -> tuple[int, list[int]]:
    """119th Congress = 2025-2026. Sessions held so far in that Congress."""
    congress = (d.year - 1789) // 2 + 1
    first_year = 1789 + 2 * (congress - 1)
    sessions = [1] if d.year == first_year else [1, 2]
    return congress, sessions


def _params(key: str, **extra) -> dict:
    return {"api_key": key, "format": "json", **extra}


def _list_votes(fetch, key: str, congress: int, session: int) -> list[dict]:
    items, offset, limit = [], 0, 250
    while True:
        data = fetch.get_json("congress", f"house-vote-list_{congress}_{session}_{offset}.json",
                              f"{BASE}/house-vote/{congress}/{session}", params=_params(key, limit=limit, offset=offset))
        page = data.get("houseRollCallVotes", [])
        items.extend(page)
        count = data.get("pagination", {}).get("count", 0)
        offset += limit
        if not page or offset >= count:
            return items


_CLERK_XML = __import__("re").compile(r"^https?://clerk\.house\.gov/evs/(\d{4})/roll0*(\d+)\.xml$")


def page_url(url: str | None) -> str | None:
    """The Clerk's readable page for a House roll call, for links. Congress.gov gives only the XML file
    (clerk.house.gov/evs/2025/roll144.xml), which a browser shows as code; the page for the same vote is
    clerk.house.gov/Votes/2025144. The snapshot keeps the XML address: it is the file that is read."""
    m = _CLERK_XML.match(url or "")
    return f"https://clerk.house.gov/Votes/{m.group(1)}{int(m.group(2))}" if m else url


def _bill_label(v: dict) -> str | None:
    if v.get("legislationType") and v.get("legislationNumber"):
        return f"{v['legislationType']} {v['legislationNumber']}"
    if v.get("amendmentType") and v.get("amendmentNumber"):
        return f"{v['amendmentType']} {v['amendmentNumber']}"
    return None


def fetch_house_votes(fetch, key: str, previous: dict | None, today: date | None = None) -> dict:
    today = today or date.today()
    congress, sessions = congress_for(today)
    prev_votes = {v["id"]: v for v in (previous or {}).get("votes", []) if v["id"].startswith(f"h-{congress}-")}
    prev_pos = (previous or {}).get("positions", {})
    votes, positions = [], {}
    for session in sessions:
        for item in sorted(_list_votes(fetch, key, congress, session), key=lambda x: x["rollCallNumber"]):
            vid = f"h-{congress}-{session}-{item['rollCallNumber']}"
            old = prev_votes.get(vid)
            if old and old.get("update") == item.get("updateDate") and vid in prev_pos:
                votes.append(old)
                positions[vid] = prev_pos[vid]
                continue
            roll = item["rollCallNumber"]
            detail = fetch.get_json("congress", f"house-vote-members_{congress}_{session}_{roll}.json",
                                    f"{BASE}/house-vote/{congress}/{session}/{roll}/members", params=_params(key, limit=500))
            body = detail.get("houseRollCallVoteMemberVotes", {})
            question = body.get("voteQuestion")
            if not question:
                meta = fetch.get_json("congress", f"house-vote_{congress}_{session}_{roll}.json",
                                      f"{BASE}/house-vote/{congress}/{session}/{roll}", params=_params(key))
                question = meta.get("houseRollCallVote", {}).get("voteQuestion")
            pos = {}
            for r in body.get("results", []):
                bio = r.get("bioguideID") or r.get("bioguideId")
                if bio:
                    pos[bio] = r.get("voteCast")
            votes.append({
                "id": vid, "chamber": "house", "congress": congress, "session": session, "number": roll,
                "date": item.get("startDate"), "question": (question or item.get("voteType") or "").strip(),
                "result": item.get("result"), "type": item.get("voteType"), "bill": _bill_label(item),
                "bill_url": item.get("legislationUrl"), "url": item.get("sourceDataURL"),
                "update": item.get("updateDate"),
            })
            positions[vid] = pos
    return {"votes": votes, "positions": positions}
