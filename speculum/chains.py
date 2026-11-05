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
import logging
import re
import statistics
from pathlib import Path

from . import ledger

log = logging.getLogger("speculum.chains")

HERE = Path(__file__).parent
# Two separate questions about every link. The evidence grade says how strong the finding is on its own
# terms; the tie says how it relates to this sale. A forensic finding about earlier deliveries of the
# same weapon can be Documented as evidence and still not be tied to this sale.
GRADES = {  # "none_found" is also used: research looked and found no credible record, itself a finding
    "documented": "The finding rests on direct evidence: forensic identification, an official record or a court document.",
    "estimated": "An official or peer-reviewed estimate, such as a casualty count or a famine classification.",
    "associated": "An assessment, allegation or pattern without direct evidence for the specific claim.",
}
TIES = {
    "this_sale": "Weapons or items from this sale",
    "same_type": "The same kind of weapon from earlier US deliveries, not this sale",
    "wider": "The wider record on the recipient; not tied to this sale",
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
        "yes_means": "advance a resolution to block the sale",
        "no_means": "decline to advance it",
        "why": "Congress can block a notified foreign arms sale only by passing a joint resolution of "
               "disapproval (Arms Export Control Act, 22 U.S.C. 2776) through both chambers and having it "
               "signed by the President or passed over a veto by two-thirds of each chamber. A motion to "
               "discharge brings the resolution out of committee for a vote; it does not block the sale by "
               "itself. Where the Secretary of State certifies an emergency, the review period is waived and the "
               "sale can proceed at once, though Congress can still pass a resolution against it. Presidents of "
               "both parties have used the emergency authority: Congress passed resolutions against emergency "
               "sales to Saudi Arabia and the UAE in 2019, and the President vetoed them; in December 2023 the "
               "Secretary of State used it for tank ammunition for Israel.",
        "why_sources": [
            {"title": "Trump Vetoes Challenge to Arab Arms Sales", "url": "https://www.armscontrol.org/act/2019-09/news/trump-vetoes-challenge-arab-arms-sales", "publisher": "Arms Control Association", "date": "2019-09"},
            {"title": "The State Department OKs the sale of tank ammunition to Israel in a deal bypassing Congress review", "url": "https://www.click2houston.com/news/politics/2023/12/09/the-state-department-oks-the-sale-of-tank-ammunition-to-israel-in-a-deal-bypassing-congress-review/", "publisher": "AP", "date": "2023-12-09"},
        ],
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


def member_sector_money(bio: str, finance: dict, lobbying: dict, fec_ids: dict, lda_ids: dict,
                        upto: str | None = None, since: str | None = None) -> dict:
    """Arms-industry money to one member. LD-203 items are dated, so with `upto` only contributions
    made on or before that date (the vote) count. FEC committee givers are cycle totals with no date
    per gift here, so they cannot be cut at the vote and are labeled as such."""
    pac, lob, givers = 0.0, 0.0, {}
    for c in (finance.get(bio) or {}).get("top_committees") or []:
        co = fec_ids.get(c.get("committee_id"))
        if co:
            pac += float(c.get("amount") or 0)
            givers[co] = givers.get(co, 0) + float(c.get("amount") or 0)
    for it in lobbying.get("by_member", {}).get(bio, []):
        co = lda_ids.get(str(it.get("registrant_id")))
        d = (it.get("date") or "")[:10]
        if co and (not upto or d <= upto) and (not since or d >= since):
            lob += float(it.get("amount") or 0)
            givers[co] = givers.get(co, 0) + float(it.get("amount") or 0)
    known = bool((finance.get(bio) or {}).get("top_committees") is not None and bio in finance)
    return {"pac": pac, "lobby": lob, "total": pac + lob, "finance_known": known,
            "givers": sorted(({"company": k, "amount": v} for k, v in givers.items()), key=lambda g: -g["amount"])}


def _median(xs):
    return statistics.median(xs) if xs else None


def build(members: list[dict], finance: dict, lobbying: dict, votes: dict, sectors: dict,
          research: dict, reviews: dict, committees: dict | None = None,
          advocacy_cfg: dict | None = None, advocacy_data: dict | None = None,
          former_members: dict | None = None) -> list[dict]:
    from .sources.committees import on_defense_committee
    by_id = {m["bioguide"]: m for m in members}
    former = {**{k: v for k, v in (votes.get("people") or {}).items()}, **(former_members or {})}
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
                "id": cid, "rule": rule["id"], "rule_name": rule["name"], "why": rule["why"], "why_sources": rule.get("why_sources", []),
                "yes_means": rule["yes_means"], "no_means": rule["no_means"], "sector": rule["sector"],
                "bill": bill, "congress": v.get("congress"), "title": v.get("title"),
                "country": country_of(v.get("title")), "votes": []})
            pos = positions.get(v["id"], {})
            ch["votes"].append({"id": v["id"], "date": (v.get("date") or "")[:10], "question": v.get("question"),
                                "result": v.get("result"), "url": v.get("url"),
                                "yeas": sum(1 for p in pos.values() if ledger.side(p) == "Y"),
                                "nays": sum(1 for p in pos.values() if ledger.side(p) == "N"),
                                "_pos": pos})

    # LD-203 window: from the first day of the earliest reporting year collected through the vote date.
    # Items dated outside the reporting years (typos such as 2029, or late reports of older gifts) are left out.
    yrs = [int(y) for y in (lobbying.get("years") or []) if str(y).isdigit()]
    window_start = f"{min(yrs)}-01-01" if yrs else None
    out = []
    for ch in chains.values():
        ch["window_start"] = window_start
        ch["votes"].sort(key=lambda x: (x["date"], x["id"]))
        decisive = ch["votes"][-1]                      # the last recorded vote on the resolution
        ch["date"] = decisive["date"]
        rows, split = [], {}
        fec_ids, lda_ids = sector_index(sectors, ch["sector"])
        decisive_pos = decisive.pop("_pos")
        for bio, p in decisive_pos.items():
            s = ledger.side(p)
            m = by_id.get(bio)
            party = ledger.caucus_party(m) if m else (former.get(bio) or {}).get("party")
            key = (party or "?", s or ("P" if p == "Present" else "-"))
            split[key] = split.get(key, 0) + 1
            if not m:
                continue                                 # no longer serving; counted in the tally only
            money = member_sector_money(bio, finance, lobbying, fec_ids, lda_ids, upto=ch["date"], since=window_start)
            rows.append({"bioguide": bio, "name": m["name"], "party": m["party"], "caucus": party, "state": m["state"],
                         "position": p, "side": s, "defense_seats": on_defense_committee(bio, committees), **money})
        for x in ch["votes"]:
            x.pop("_pos", None)
        ch["members"] = sorted(rows, key=lambda r: (r["side"] or "Z", r["name"]))   # never ranked by amount
        ch["former"] = sorted(((former.get(b) or {}).get("name") or b) for b in decisive_pos if b not in by_id)
        ch["former_detail"] = sorted(({"name": (former.get(b) or {}).get("name") or b, "party": (former.get(b) or {}).get("party"),
                                       "side": ledger.side(p)} for b, p in decisive_pos.items() if b not in by_id),
                                     key=lambda x: x["name"])
        ch["unknown_party"] = sum(n for (p, s), n in split.items() if p == "?")
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
                          and (not o.get("applies_to") or bkey in o["applies_to"])
                          and (not o.get("applies_to_wider") or bkey in o["applies_to_wider"])]
        order = {"documented": 0, "estimated": 1, "associated": 2, "none_found": 3}
        notified = (r or {}).get("notified")
        placed = []
        for o in ch["outcomes"]:
            when = o.get("date") or (o.get("source") or {}).get("date") or ""
            rel = ("before this sale was notified" if notified and when and when < notified
                   else "before the vote" if when and when <= ch["date"] else "after the vote" if when else "")
            general = bool(o.get("scope_note"))          # e.g. a general assessment of US-made arms
            if (o.get("scope_note_by") or {}).get(bkey):   # a note about this sale only; placement unchanged
                o = {**o, "scope_note": o["scope_note_by"][bkey]}
            if o.get("scope_note_same"):
                o = {**o, "scope_note": o["scope_note_same"]}
                general = False
            scope = ("context" if o.get("context_only") else
                     "wider" if general else
                     "same_type" if o.get("applies_to") and bkey in o["applies_to"] else "wider")
            prio = (o.get("priority") or {}).get(bkey, 50)
            placed.append({**o, "_order": prio * 10 + order.get(o.get("grade"), 4), "when": when, "when_rel": rel, "scope": scope,
                           "group_label": (research.get("_meta") or {}).get("groups", {}).get(o.get("group"))})
        ch["outcomes"] = [o for o in placed if o["scope"] != "context"]
        ch["genocide_note"] = (research.get("_meta") or {}).get("israel_genocide_note") if ch["country"] == "Israel" else None
        ch["context"] = ([o for o in placed if o["scope"] == "context"] +
                         [x for x in research.get("context", []) if x.get("country") == ch["country"]
                          and (not x.get("applies_to") or bkey in x["applies_to"])])
        _contractor_money(ch, sectors)
        _advocacy(ch, by_id, finance, advocacy_cfg or {}, advocacy_data)
        ch["review"] = reviews.get(ch["id"]) or {}
        set_status(ch)                                   # set again in for_site once drafts are merged
        out.append(ch)
    # Votes held the same day are mostly the same senators: say how much they overlap, so a repeated
    # money gap is not read as an independent confirmation.
    for ch in out:
        sides = {r["bioguide"]: r["side"] for r in ch["members"] if r["side"]}
        ch["siblings"] = []
        for other in out:
            if other is ch or other["date"] != ch["date"]:
                continue
            o = {r["bioguide"]: r["side"] for r in other["members"] if r["side"]}
            both = [b for b in sides if b in o]
            same = sum(1 for b in both if sides[b] == o[b])
            ch["siblings"].append({"bill": other["bill"], "id": other["id"], "both": len(both), "same": same,
                                   "verdicts": {p: cell["verdict"] for p, cell in other["money_pattern"].items()}})
        ch["same_recipient"] = sorted({o["date"] for o in out if o["country"] == ch["country"]})
        ch["same_recipient_n"] = sum(1 for o in out if o["country"] == ch["country"])
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
        for alias, parent in (("atk tactical systems", "northrop grumman"), ("orbital atk", "northrop grumman"),
                              ("general atomics aeronautical", "general atomics")):
            low = low.replace(alias, parent)    # subsidiaries named in notices, matched to their parent
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
            st = _stats([r["contractor"] for r in group])
            cell[side]["contractor_median"] = st["median"] if hits else None
            cell[side]["contractor_any"] = st["any"]


def _advocacy(ch: dict, by_id: dict, finance: dict, cfg: dict, data: dict | None) -> None:
    """Money from organizations that took a public side on this bill, both sides by one rule."""
    from .sources import advocacy
    bkey = ch["bill"].upper().replace(" ", "").replace(".", "")
    sides = {"block": [], "proceed": []}
    ch["advocacy_after"], ch["advocacy_noside"] = [], []
    for org in cfg.get("organizations", []):
        sd = org.get("sides", {}).get(bkey)
        pos = _position_text(org, bkey)
        if sd and bkey in org.get("after_only", []):
            ch["advocacy_after"].append({"name": org["name"], "side": sd, "position": pos})
            continue
        if not sd and pos and "not found" not in pos and "not retrieved" not in pos:
            ch["advocacy_noside"].append({"name": org["name"], "position": pos})
        if sd in sides:
            sides[sd].append({"name": org["name"], "pacs": org.get("pacs", []), "super_pacs": org.get("super_pacs", []),
                              "includes": org.get("includes", []),
                              "evidence": org.get("evidence", []), "position": _position_text(org, bkey)})
    ch["advocacy_orgs"] = sides
    ch["advocacy_meta"] = cfg.get("_meta", {})
    if not data or not (sides["block"] or sides["proceed"]):
        ch["advocacy_pattern"] = None
        return
    vd = cfg.get("vote_dates", {}).get(bkey)
    if vd != ch["date"]:
        log.warning("advocacy vote date for %s is %s in data/advocacy.json but %s in the roll call", bkey, vd, ch["date"])
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


MIN_N = 10        # smallest group a median is shown for
GAP = 2.0         # medians this far apart (ratio), with both groups at least MIN_N, count as a gap


def _stats(xs: list[float]) -> dict:
    xs = sorted(xs)
    return {"n": len(xs), "median": _median(xs) if len(xs) >= MIN_N else None, "median_any": _median(xs),
            "low": xs[0] if xs else None, "high": xs[-1] if xs else None, "any": sum(1 for x in xs if x > 0),
            "each": xs}


def _pattern(rows: list[dict]) -> dict:
    """Arms-industry money for each side of the vote, within each party: an associated-grade reading.
    Comparing inside a party keeps the party line from masquerading as a money effect. A median is
    shown only for groups of at least MIN_N senators; each group shows its size, range and how many
    received anything. PAC money counts only members whose FEC data has been collected (unknown is
    not zero). The verdict follows one fixed rule for every chain."""
    out = {}
    for party in sorted({r["caucus"] for r in rows if r["caucus"]}):
        cell = {}
        for side in ("Y", "N"):
            group = [r for r in rows if r["caucus"] == party and r["side"] == side]
            known = [r["pac"] for r in group if r["finance_known"]]
            lob = _stats([r["lobby"] for r in group])
            cell[side] = {"n": len(group), "lobby": lob, "lobby_median": lob["median"], "lobby_any": lob["any"],
                          "pac_n": len(known), "pac": _stats(known), "pac_median": _stats(known)["median"]}
        y, n = cell["Y"], cell["N"]
        if not y["n"] or not n["n"]:
            verdict = "none"            # everyone in the party voted the same way
        elif y["n"] < MIN_N or n["n"] < MIN_N:
            verdict = "small"
        else:
            a, b = y["lobby"]["median_any"] or 0, n["lobby"]["median_any"] or 0
            hi, lo = max(a, b), min(a, b)
            verdict = ("gap_no" if b > a else "gap_yes") if (lo == 0 and hi > 0) or (lo and hi / lo >= GAP) else "no_difference"
        cell["verdict"] = verdict
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
                load_json(data_root / "advocacy.json", {}), advocacy_data,
                {k: v for k, v in load_json(data_root / "former_members.json", {}).items() if not k.startswith("_")})
    for ch in out:
        ch["research_by"] = "research" if ch["mechanism"] or ch["outcomes"] else None
        d = load_json(data_root / "chains" / "drafts" / f"{ch['id']}.json", None)
        if d and not ch["research_by"]:
            ch["mechanism"] = d.get("mechanism")
            ch["outcomes"] = d.get("outcomes", [])
            ch["research_by"] = "ai-draft"
            ch["draft_meta"] = d.get("_drafted", {})
        ch["research_meta"] = research.get("_meta", {})
        set_status(ch)
    return out


# What a reviewer signs off on: the research and the framing, not the nightly figures. Money totals,
# vote tallies and patterns are left out, so they can update every night without undoing a review.
_REVIEWED = ("mechanism", "outcomes", "context", "genocide_note", "advocacy_orgs", "advocacy_after",
             "advocacy_noside", "advocacy_meta", "research_by", "draft_meta")


def fingerprint(ch: dict) -> str:
    """A short hash of everything in a chain a reviewer approves. Any change to it lapses the approval."""
    import hashlib
    blob = json.dumps({k: ch.get(k) for k in _REVIEWED}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def set_status(ch: dict) -> None:
    """approved: a person reviewed this exact research and takes responsibility for it. verified: an
    independent AI checker opened every source and found each claim matches. draft: neither yet.
    An approval given to earlier research has lapsed: the chain is shown as awaiting review again."""
    rv = ch.get("review") or {}
    ch["fingerprint"] = fingerprint(ch)
    approved = bool(rv.get("approved_by")) and rv.get("research") == ch["fingerprint"]
    ch["lapsed"] = rv if rv.get("approved_by") and not approved else None
    ch["status"] = "approved" if approved else "verified" if rv.get("ai_verified") else "draft"
