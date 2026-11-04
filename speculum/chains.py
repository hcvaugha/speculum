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
GRADES = {  # "none_found" is also used: research looked and found no credible record, itself a finding
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
          research: dict, reviews: dict, committees: dict | None = None,
          advocacy_cfg: dict | None = None, advocacy_data: dict | None = None) -> list[dict]:
    from .sources.committees import on_defense_committee
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
                         "position": p, "side": s, "defense_seats": on_defense_committee(bio, committees), **money})
        for x in ch["votes"]:
            x.pop("_pos", None)
        ch["members"] = sorted(rows, key=lambda r: (r["side"] or "Z", -r["total"]))
        ch["party_split"] = {f"{p}-{s}": n for (p, s), n in sorted(split.items())}
        ch["money_pattern"] = _pattern(ch["members"])
        # Control: the same comparison without senators on Armed Services, Foreign Relations or the
        # Defense appropriations subcommittee, who draw defense money whatever they vote.
        ch["committee_control"] = (_pattern([r for r in ch["members"] if not r["defense_seats"]])
                                   if committees and committees.get("seats") else None)
        ch["defense_committees"] = sorted(set((committees or {}).get("defense_codes", {}).values()))
        bkey = ch["bill"].upper().replace(" ", "").replace(".", "")
        r = (research.get("resolutions") or {}).get(bkey, {})
        ch["mechanism"] = r or None
        # An outcome belongs to a chain when it concerns the recipient and either names no specific
        # sale or names this one.
        ch["outcomes"] = [o for o in research.get("outcomes", []) if o.get("country") == ch["country"]
                          and (not o.get("applies_to") or bkey in o["applies_to"])]
        order = {"documented": 0, "estimated": 1, "associated": 2, "none_found": 3}
        ch["outcomes"] = [{**o, "_order": order.get(o.get("grade"), 4)} for o in ch["outcomes"]]
        ch["context"] = [x for x in research.get("context", []) if x.get("country") == ch["country"]]
        _contractor_money(ch, sectors)
        _advocacy(ch, by_id, finance, advocacy_cfg or {}, advocacy_data)
        rv = reviews.get(ch["id"]) or {}
        # approved: a person checked it and takes responsibility. verified: an independent AI checker
        # opened every source and found each claim matches. draft: neither yet.
        ch["status"] = "approved" if rv.get("approved_by") else ("verified" if rv.get("ai_verified") else "draft")
        ch["review"] = rv
        out.append(ch)
    out.sort(key=lambda c: (c["date"], c["id"]), reverse=True)
    return out


def _company_key(name: str) -> str:
    n = re.sub(r"\(.*?\)", " ", name.lower())
    n = re.sub(r"\b(the|company|corp|corporation|inc|international|technologies|group|holdings|systems)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _contractor_money(ch: dict, sectors: dict) -> None:
    """Money from the companies named as principal contractors for this sale: the firms that build
    what was sold. Matched by company name against the sector list; the match is shown, not hidden."""
    named = (ch.get("mechanism") or {}).get("contractors") or []
    cos = sectors.get(ch["sector"], {}).get("companies", [])
    hits = set()
    for c in named:
        low = c.lower().replace("raytheon", "rtx")
        for co in cos:
            k = _company_key(co["name"])
            if k and re.search(r"\b" + re.escape(k) + r"\b", low):
                hits.add(co["name"])
    ch["contractor_companies"] = sorted(hits)
    for r in ch["members"]:
        r["contractor"] = sum(g["amount"] for g in r["givers"] if g["company"] in hits)
    for party, cell in ch["money_pattern"].items():
        for side in ("Y", "N"):
            group = [r for r in ch["members"] if r["caucus"] == party and r["side"] == side]
            cell[side]["contractor_median"] = _median([r["contractor"] for r in group]) if hits else None
            cell[side]["contractor_any"] = sum(1 for r in group if r["contractor"] > 0)


def _advocacy(ch: dict, by_id: dict, finance: dict, cfg: dict, data: dict | None) -> None:
    """Money from organizations that took a public side on this bill, both sides by one rule."""
    from .sources import advocacy
    bkey = ch["bill"].upper().replace(" ", "").replace(".", "")
    sides = {"block": [], "proceed": []}
    for org in cfg.get("organizations", []):
        sd = org.get("sides", {}).get(bkey)
        if sd in sides:
            sides[sd].append({"name": org["name"], "pacs": org.get("pacs", []), "super_pacs": org.get("super_pacs", []),
                              "includes": org.get("includes", []),
                              "evidence": org.get("evidence", []), "position": _position_text(org, bkey)})
    ch["advocacy_orgs"] = sides
    ch["advocacy_meta"] = cfg.get("_meta", {})
    if not data or not (sides["block"] or sides["proceed"]):
        ch["advocacy_pattern"] = None
        return
    for r in ch["members"]:
        r["adv"] = advocacy.member_totals(r["bioguide"], by_id[r["bioguide"]], finance, cfg, data, bkey)
    out = {}
    for party in sorted({r["caucus"] for r in ch["members"] if r["caucus"]}):
        cell = {}
        for side in ("Y", "N"):
            group = [r for r in ch["members"] if r["caucus"] == party and r["side"] == side]
            known = [r for r in group if r["adv"]["known"]]
            cell[side] = {"n": len(group), "n_known": len(known),
                          "proceed_median": _median([r["adv"]["proceed"] for r in known]),
                          "proceed_any": sum(1 for r in known if r["adv"]["proceed"] > 0),
                          "block_median": _median([r["adv"]["block"] for r in known]),
                          "block_any": sum(1 for r in known if r["adv"]["block"] > 0),
                          "ie_support": sum(r["adv"]["ie_support"] for r in group),
                          "ie_oppose": sum(r["adv"]["ie_oppose"] for r in group)}
        out[party] = cell
    ch["advocacy_pattern"] = out
    ch["advocacy_complete"] = all(r["adv"]["complete"] for r in ch["members"])


def _position_text(org: dict, bkey: str) -> str:
    pos = org.get("positions", {})
    for k, v in pos.items():
        if bkey in k.upper().replace(".", "").replace(" ", "").replace("/", "/SJRES").split("/"):
            return v
    return ""


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


def for_site(members, finance, lobbying, votes, data_root: Path, committees: dict | None = None,
             advocacy_data: dict | None = None) -> list[dict]:
    """Chains with everything a page needs: reviewed research where it exists, otherwise the AI
    drafter's output, clearly marked as an unreviewed draft."""
    data_root = Path(data_root)
    research = load_json(data_root / "chains" / "research.json", {})
    out = build(members, finance, lobbying, votes, load_json(data_root / "sectors.json", {}), research,
                load_json(data_root / "chains" / "reviews.json", {}), committees,
                load_json(data_root / "advocacy.json", {}), advocacy_data)
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
