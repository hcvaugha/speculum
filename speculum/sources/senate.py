"""Senate roll-call votes from senate.gov XML. Senators are keyed by LIS ID in these files and
mapped to Bioguide IDs through the congress-legislators crosswalk."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime

from ..http import NotFound
from .congress import congress_for

MENU = "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{c}_{s}.xml"
VOTE = "https://www.senate.gov/legislative/LIS/roll_call_votes/vote{c}{s}/vote_{c}_{s}_{n:05d}.xml"
PAGE = "https://www.senate.gov/legislative/LIS/roll_call_votes/vote{c}{s}/vote_{c}_{s}_{n:05d}.htm"


def _text(el, path: str) -> str:
    found = el.find(path)
    return re.sub(r"\s+", " ", found.text).strip() if found is not None and found.text else ""


def parse_menu(xml: bytes) -> list[int]:
    root = ET.fromstring(xml)
    return sorted(int(_text(v, "vote_number")) for v in root.iter("vote") if _text(v, "vote_number"))


def parse_vote(xml: bytes, lis_to_bio: dict[str, str]) -> tuple[dict, dict]:
    root = ET.fromstring(xml)
    raw_date = _text(root, "vote_date")
    try:
        when = datetime.strptime(raw_date, "%B %d, %Y, %I:%M %p").isoformat()
    except ValueError:
        when = raw_date
    doc = root.find("document")
    bill = _text(doc, "document_name") if doc is not None else ""
    vote = {
        "question": _text(root, "question") or _text(root, "vote_question_text"),
        "title": _text(root, "vote_title"),
        "result": _text(root, "vote_result"),
        "date": when,
        "bill": bill or None,
    }
    positions = {}
    for m in root.iter("member"):
        lis = _text(m, "lis_member_id")
        if lis:
            positions[lis_to_bio.get(lis, f"lis:{lis}")] = _text(m, "vote_cast")
    return vote, positions


def fetch_senate_votes(fetch, members: list[dict], previous: dict | None, today: date | None = None) -> dict:
    today = today or date.today()
    congress, sessions = congress_for(today)
    lis_to_bio = {m["lis"]: m["bioguide"] for m in members if m.get("lis")}
    prev_votes = {v["id"]: v for v in (previous or {}).get("votes", []) if v["id"].startswith(f"s-{congress}-")}
    prev_pos = (previous or {}).get("positions", {})
    votes, positions = [], {}
    for session in sessions:
        try:
            menu = fetch.get("senate", f"vote_menu_{congress}_{session}.xml", MENU.format(c=congress, s=session))
        except NotFound:
            continue
        for n in parse_menu(menu):
            vid = f"s-{congress}-{session}-{n}"
            if vid in prev_votes and vid in prev_pos:
                votes.append(prev_votes[vid])
                positions[vid] = prev_pos[vid]
                continue
            xml = fetch.get("senate", f"vote_{congress}_{session}_{n:05d}.xml", VOTE.format(c=congress, s=session, n=n))
            meta, pos = parse_vote(xml, lis_to_bio)
            votes.append({"id": vid, "chamber": "senate", "congress": congress, "session": session, "number": n,
                          "url": PAGE.format(c=congress, s=session, n=n), "type": None, **meta})
            positions[vid] = pos
    return {"votes": votes, "positions": positions}
