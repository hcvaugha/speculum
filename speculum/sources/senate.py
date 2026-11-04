"""Senate roll-call votes from senate.gov XML. Senators are keyed by LIS ID in these files and
mapped to Bioguide IDs through the congress-legislators crosswalk."""
from __future__ import annotations

import csv
import io
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime

import requests

from ..http import NotFound
from .congress import congress_for

log = logging.getLogger("speculum.senate")

MENU = "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{c}_{s}.xml"
VOTE = "https://www.senate.gov/legislative/LIS/roll_call_votes/vote{c}{s}/vote_{c}_{s}_{n:05d}.xml"
PAGE = "https://www.senate.gov/legislative/LIS/roll_call_votes/vote{c}{s}/vote_{c}_{s}_{n:05d}.htm"
# senate.gov turns away clients that don't look like a browser.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Speculum/0.1; +https://speculum.vote)",
           "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8"}
# Fallback: Voteview (UCLA) republishes every Senate roll call, keyed by ICPSR ID, usually within days.
VV_ROLLCALLS = "https://voteview.com/static/data/out/rollcalls/S{c}_rollcalls.csv"
VV_VOTES = "https://voteview.com/static/data/out/votes/S{c}_votes.csv"
VV_MEMBERS = "https://voteview.com/static/data/out/members/S{c}_members.csv"
VV_CAST = {"1": "Yea", "2": "Yea", "3": "Yea", "4": "Nay", "5": "Nay", "6": "Nay",
           "7": "Present", "8": "Present", "9": "Not Voting"}


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
    """senate.gov first; if it refuses us, Voteview. Vote IDs are the same either way."""
    try:
        return _from_senate_gov(fetch, members, previous, today)
    except requests.HTTPError as exc:
        log.warning("senate.gov refused (%s); using Voteview instead", exc)
        out = _from_voteview(fetch, members, today)
        out["source"] = "voteview"
        return out


def _from_voteview(fetch, members: list[dict], today: date | None = None) -> dict:
    today = today or date.today()
    congress, _ = congress_for(today)
    icpsr_to_bio = {m["icpsr"]: m["bioguide"] for m in members if m.get("icpsr")}
    # The crosswalk lacks ICPSR IDs for some newer senators; Voteview's own member list carries Bioguide IDs.
    try:
        for r in csv.DictReader(io.StringIO(fetch.get("voteview", f"S{congress}_members.csv",
                                                      VV_MEMBERS.format(c=congress)).decode("utf-8"))):
            icpsr = (r.get("icpsr") or "").split(".")[0]
            if icpsr and r.get("bioguide_id"):
                icpsr_to_bio.setdefault(icpsr, r["bioguide_id"].strip())
    except Exception as exc:
        log.warning("Voteview member list unavailable (%s); matching by crosswalk only", exc)
    rows = csv.DictReader(io.StringIO(fetch.get("voteview", f"S{congress}_rollcalls.csv",
                                                VV_ROLLCALLS.format(c=congress)).decode("utf-8")))
    key_to_id, votes = {}, []
    for r in rows:
        if r.get("chamber", "Senate") != "Senate":
            continue
        session = int(float(r.get("session") or 1))
        n = int(float(r.get("clerk_rollnumber") or r["rollnumber"]))
        vid = f"s-{congress}-{session}-{n}"
        key_to_id[r["rollnumber"]] = vid
        votes.append({"id": vid, "chamber": "senate", "congress": congress, "session": session, "number": n,
                      "url": PAGE.format(c=congress, s=session, n=n), "type": None,
                      "question": (r.get("vote_question") or "").strip(), "title": (r.get("vote_desc") or r.get("dtl_desc") or "").strip(),
                      "result": (r.get("vote_result") or "").strip(), "date": r.get("date"),
                      "bill": (r.get("bill_number") or "").strip() or None, "via": "voteview"})
    positions: dict[str, dict] = {vid: {} for vid in key_to_id.values()}
    for r in csv.DictReader(io.StringIO(fetch.get("voteview", f"S{congress}_votes.csv",
                                                  VV_VOTES.format(c=congress)).decode("utf-8"))):
        if (r.get("chamber") or "Senate") != "Senate" or (r.get("icpsr") or "").startswith("999"):
            continue        # Voteview also records the President's announced position; he is not a senator
        vid = key_to_id.get(r.get("rollnumber"))
        cast = VV_CAST.get((r.get("cast_code") or "").split(".")[0])
        if vid and cast:
            icpsr = (r.get("icpsr") or "").split(".")[0]
            positions[vid][icpsr_to_bio.get(icpsr, f"icpsr:{icpsr}")] = cast
    votes.sort(key=lambda v: (v["session"], v["number"]))
    return {"votes": votes, "positions": positions}


def _from_senate_gov(fetch, members: list[dict], previous: dict | None, today: date | None = None) -> dict:
    today = today or date.today()
    congress, sessions = congress_for(today)
    lis_to_bio = {m["lis"]: m["bioguide"] for m in members if m.get("lis")}
    prev_votes = {v["id"]: v for v in (previous or {}).get("votes", []) if v["id"].startswith(f"s-{congress}-")}
    prev_pos = (previous or {}).get("positions", {})
    votes, positions = [], {}
    for session in sessions:
        try:
            menu = fetch.get("senate", f"vote_menu_{congress}_{session}.xml", MENU.format(c=congress, s=session), headers=HEADERS)
        except NotFound:
            continue
        for n in parse_menu(menu):
            vid = f"s-{congress}-{session}-{n}"
            if vid in prev_votes and vid in prev_pos:
                votes.append(prev_votes[vid])
                positions[vid] = prev_pos[vid]
                continue
            xml = fetch.get("senate", f"vote_{congress}_{session}_{n:05d}.xml", VOTE.format(c=congress, s=session, n=n), headers=HEADERS)
            meta, pos = parse_vote(xml, lis_to_bio)
            votes.append({"id": vid, "chamber": "senate", "congress": congress, "session": session, "number": n,
                          "url": PAGE.format(c=congress, s=session, n=n), "type": None, **meta})
            positions[vid] = pos
    return {"votes": votes, "positions": positions}


def clean_votes(votes: dict) -> dict:
    """Remove non-senators from saved Senate positions (the President's announced position, which
    Voteview records with ICPSR 999xx). Idempotent."""
    pos = votes.get("positions", {})
    if not any(k.startswith("icpsr:999") for p in pos.values() for k in p):
        return votes
    return {**votes, "positions": {vid: {k: v for k, v in p.items() if not k.startswith("icpsr:999")} for vid, p in pos.items()}}
