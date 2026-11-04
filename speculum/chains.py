"""Consequence chains: the Interpreter's output.

A chain follows one decision forward:

    decision   a recorded vote, found by a fixed rule (never picked by topic or party)
    mechanism  what the decision actually changed (what was sold, to whom, by which contractors)
    outcome    what happened to people afterwards, from public datasets and investigations
    money      the funding present around the decision, from FEC and LD-203 filings

Every outcome link carries an evidence grade (documented, estimated, associated) and a source.
Decision and money links are computed from filings each night. Mechanism and outcome links are
drafted (by the AI drafter or by research) and stay marked as drafts until a reviewer approves them
in data/chains/reviews.json. A chain judges a decision; it never calls a person good or evil.
"""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from . import ledger

HERE = Path(__file__).parent
GRADES = {
    "documented": "The decision did it directly, and an official or investigative record shows the result.",
    "estimated": "An official or peer-reviewed estimate of the effect, not tied to this decision alone.",
    "associated": "A pattern only. Shown so readers can see it; never presented as cause.",
}

# Rules decide which votes become chains. Each is a fixed text test on the vote's official title,
# so the same rule catches every such vote, whoever brought it and whichever way it went.
RULES = [
    {
        "id": "arms-sales",
        "name": "Foreign arms sales",
        "test": re.compile(r"disapproval of the proposed (foreign military sale|export|direct commercial sale|transfer)"
                           r"[^.]*defense articles", re.I),
        "sector": "arms",
        "yes_means": "block the sale",
        "no_means": "let the sale proceed",
        "why": "Congress can block a notified foreign arms sale by joint resolution of disapproval "
               "(Arms Export Control Act, 22 U.S.C. 2776). A vote for the resolution, or to discharge it "
               "from committee, is a vote to block; a vote against lets the sale go ahead.",
    },
]

_COUNTRY = re.compile(r"(?:sale|export)[^.]*?\bto (?:the Government of )?(?:the )?([A-Z][A-Za-z .'-]+?)(?: of certain| certain|\.|$)")


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def country_of(title: str) -> str | None:
    m = _COUNTRY.search(title or "")
    return m.group(1).strip() if m else None


def sector_index(sectors: dict, name: str) -> tuple[dict, dict]:
    """FEC committee id -> company and LDA registrant id -> company, for one sector."""
    fec, lda = {}, {}
    for co in sectors.get(name, {}).get("companies", []):
        for cid in co.get("fec", []):
            fec[cid] = co["name"]
        for rid in co.get("lda", []):
            lda[str(rid)] = co["name"]
    return fec, lda


def member_sector_money(bio: str, finance: dict, lobbying: dict, fec_ids: dict, lda_ids: dict) -> dict:
    pac, lob, givers = 0.0, 0.0, {}
    for c in (finance.get(bio) or {}).get("top_committees") or []:
        co = fec_ids.get(c.get("committee_id"))
        if co:
            pac += float(c.get("amount") or 0)
            givers[co] = givers.get(co, 0) + float(c.get("amount") or 0)
    for it in lobbying.get("by_member", {}).get(bio, []):
        co = lda_ids.get(str(it.get("registrant_id")))
        if co:
            lob += float(it.get("amount") or 0)
            givers[co] = givers.get(co, 0) + float(it.get("amount") or 0)
    known = bool((finance.get(bio) or {}).get("top_committees") is not None and bio in finance)
    return {"pac": pac, "lobby": lob, "total": pac + lob, "finance_known": known,
            "givers": sorted(({"company": k, "amount": v} for k, v in givers.items()), key=lambda g: -g["amount"])}


def _median(xs):
    return statistics.median(xs) if xs else None


def build(members: list[dict], finance: dict, lobbying: dict, votes: dict, sectors: dict,
          research: dict, reviews: dict) -> list[dict]:
    by_id = {m["bioguide"]: m for m in members}
    positions = votes.get("positions", {})
    chains: dict[str, dict] = {}
    for rule in RULES:
        fec_ids, lda_ids = sector_index(sectors, rule["sector"])
        for v in votes.get("votes", []):
            if not rule["test"].search(v.get("title") or ""):
                continue
            bill = v.get("bill") or v["id"]
            cid = f"{rule['id']}-{v.get('congress')}-{bill}".lower()
            ch = chains.setdefault(cid, {
                "id": cid, "rule": rule["id"], "rule_name": rule["name"], "why": rule["why"],
                "yes_means": rule["yes_means"], "no_means": rule["no_means"], "sector": rule["sector"],
                "bill": bill, "congress": v.get("congress"), "title": v.get("title"),
                "country": country_of(v.get("title")), "votes": []})
            pos = positions.get(v["id"], {})
            ch["votes"].append({"id": v["id"], "date": (v.get("date") or "")[:10], "question": v.get("question"),
                                "result": v.get("result"), "url": v.get("url"),
                                "yeas": sum(1 for p in pos.values() if ledger.side(p) == "Y"),
                                "nays": sum(1 for p in pos.values() if ledger.side(p) == "N"),
                                "_pos": pos})

    out = []
    for ch in chains.values():
        ch["votes"].sort(key=lambda x: (x["date"], x["id"]))
        decisive = ch["votes"][-1]                      # the last recorded vote on the resolution
        ch["date"] = decisive["date"]
        rows, split = [], {}
        fec_ids, lda_ids = sector_index(sectors, ch["sector"])
        for bio, p in decisive.pop("_pos").items():
            s = ledger.side(p)
            m = by_id.get(bio)
            party = ledger.caucus_party(m) if m else None
            key = (party or "?", s or "-")
            split[key] = split.get(key, 0) + 1
            if not m:
                continue                                 # no longer serving; counted in the tally only
            money = member_sector_money(bio, finance, lobbying, fec_ids, lda_ids)
            rows.append({"bioguide": bio, "name": m["name"], "party": m["party"], "caucus": party, "state": m["state"],
                         "position": p, "side": s, **money})
        for x in ch["votes"]:
            x.pop("_pos", None)
        ch["members"] = sorted(rows, key=lambda r: (r["side"] or "Z", -r["total"]))
        ch["party_split"] = {f"{p}-{s}": n for (p, s), n in sorted(split.items())}
        ch["money_pattern"] = _pattern(rows)
        r = (research.get("resolutions") or {}).get(ch["bill"].upper().replace(" ", "").replace(".", ""), {})
        ch["mechanism"] = r or None
        ch["outcomes"] = [o for o in research.get("outcomes", []) if o.get("country") == ch["country"]]
        rv = reviews.get(ch["id"]) or {}
        ch["status"] = "approved" if rv.get("approved_by") else "draft"
        ch["review"] = rv
        out.append(ch)
    out.sort(key=lambda c: (c["date"], c["id"]), reverse=True)
    return out


def _pattern(rows: list[dict]) -> dict:
    """Median arms-industry money for each side of the vote, within each party: an associated-grade
    reading. Comparing inside a party keeps the party line itself from masquerading as a money effect.
    Lobbyist money (LD-203) is known for every member; PAC money only once FEC data is fetched, and a
    member whose FEC data is missing is left out of the PAC median rather than counted as zero."""
    out = {}
    for party in sorted({r["caucus"] for r in rows if r["caucus"]}):
        cell = {}
        for side in ("Y", "N"):
            group = [r for r in rows if r["caucus"] == party and r["side"] == side]
            known = [r["pac"] for r in group if r["finance_known"]]
            cell[side] = {"n": len(group),
                          "lobby_median": _median([r["lobby"] for r in group]),
                          "lobby_any": sum(1 for r in group if r["lobby"] > 0),
                          "pac_n": len(known), "pac_median": _median(known)}
        out[party] = cell
    return out


def for_site(members, finance, lobbying, votes, data_root: Path) -> list[dict]:
    """Chains with everything a page needs: reviewed research where it exists, otherwise the AI
    drafter's output, clearly marked as an unreviewed draft."""
    data_root = Path(data_root)
    research = load_json(data_root / "chains" / "research.json", {})
    out = build(members, finance, lobbying, votes, load_json(data_root / "sectors.json", {}), research,
                load_json(data_root / "chains" / "reviews.json", {}))
    for ch in out:
        ch["research_by"] = "research" if ch["mechanism"] or ch["outcomes"] else None
        d = load_json(data_root / "chains" / "drafts" / f"{ch['id']}.json", None)
        if d and not ch["research_by"]:
            ch["mechanism"] = d.get("mechanism")
            ch["outcomes"] = d.get("outcomes", [])
            ch["research_by"] = "ai-draft"
            ch["draft_meta"] = d.get("_drafted", {})
        ch["research_meta"] = research.get("_meta", {})
    return out
