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
        "blurb": "Senate votes on blocking a foreign arms sale.",
        "name": "Foreign arms sales",
        "test": re.compile(r"disapproval of the proposed (foreign military sale|export|direct commercial sale|transfer)"
                           r"[^.]*defense articles", re.I),
        "sector": "arms",
        "money": "sector",              # money from a fixed list of companies (data/sectors.json)
        "template": "chain.html",
        "area": "defense-foreign",
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
    {
        "id": "rules-overturned",
        "blurb": "Votes on overturning a federal agency's rule.",
        "name": "Regulations overturned",
        # Every resolution under the Congressional Review Act with a recorded vote, whoever sponsored it and
        # whatever the rule was about. Titles follow the form fixed by 5 U.S.C. 802(a).
        "test": re.compile(r"disapproval under chapter 8 of title 5", re.I),
        "sector": None,
        "money": "lobbied",             # money from organizations that reported lobbying on this resolution
        "template": "chain_rule.html",
        "yes_means": "advance or pass the resolution overturning the rule",
        "no_means": "oppose it",
        "why": "Congress can overturn a rule issued by a federal agency with a joint resolution of disapproval "
               "under the Congressional Review Act (5 U.S.C. 801-808). The resolution must pass both chambers and "
               "be signed by the President, or passed over a veto by two-thirds of each chamber. A rule overturned "
               "this way has no force or effect, and it may not be reissued in substantially the same form, nor a "
               "substantially similar rule issued, unless a law enacted afterward specifically authorizes it. In "
               "the Senate, a resolution taken up within 60 session days of the rule's submission or publication "
               "follows an expedited procedure in which debate is limited to 10 hours.",
        "why_sources": [
            {"title": "5 U.S. Code 801: Congressional review", "url": "https://www.law.cornell.edu/uscode/text/5/801", "publisher": "LII, Cornell Law School", "date": None},
            {"title": "5 U.S. Code 802: Congressional disapproval procedure", "url": "https://www.law.cornell.edu/uscode/text/5/802", "publisher": "LII, Cornell Law School", "date": None},
        ],
        "subject": lambda t: rule_parts(t)[0],
        "page": {
            "subject_label": "Agency", "heading": "overturning a rule of the {subject}", "fixed_article": True, "measure": "resolution",
            "record_title": "What the rule did", "record_noun": "the record of the rule",
            "unresearched": "What the rule did and what followed have not been researched yet.",
            "main": "required",
            "pair": [["benefits", "The agency's estimate of benefits"], ["costs", "The agency's estimate of costs"],
                     "Both from the agency's own analysis of the rule. They are estimates made when the rule was written, not records of what happened."],
            "lines": [["agency_reasons", "Stated purpose (the agency)"], ["sponsor_reasons", "Stated reasons for the resolution (sponsors and supporters)"]],
            "status_label": "What became of the rule", "estimate_example": "the agency's own analysis",
            "decision": "Congress's vote on whether to overturn this rule",
        },
    },
    {
        "id": "reconciliation",
        "blurb": "Votes on budget reconciliation bills.",
        "name": "Budget reconciliation",
        # Every reconciliation bill: its title is fixed as "to provide for reconciliation pursuant to" the budget resolution.
        "test": re.compile(r"to provide for reconciliation pursuant to", re.I),
        "sector": None, "money": "lobbied", "template": "chain_rule.html", "area": "tax-budget",
        "yes_means": "advance or pass the bill",
        "no_means": "oppose it",
        "why": "A budget resolution can direct committees to recommend changes in law to spending, revenues or the "
               "debt limit; a reconciliation bill carries out those instructions (2 U.S.C. 641). In the Senate, "
               "debate on a reconciliation bill, its amendments and related motions is limited to 20 hours, and a "
               "provision that does not produce a change in outlays or revenues can be struck as extraneous on a "
               "point of order (2 U.S.C. 644). Like any bill, it becomes law only when passed by both chambers and "
               "signed by the President, or passed over a veto by two-thirds of each chamber.",
        "why_sources": [
            {"title": "2 U.S. Code 641: Reconciliation", "url": "https://www.law.cornell.edu/uscode/text/2/641", "publisher": "LII, Cornell Law School", "date": None},
            {"title": "2 U.S. Code 644: Extraneous matter in reconciliation legislation", "url": "https://www.law.cornell.edu/uscode/text/2/644", "publisher": "LII, Cornell Law School", "date": None},
            {"title": "Constitution of the United States, Article I", "url": "https://www.law.cornell.edu/constitution/articlei", "publisher": "LII, Cornell Law School", "date": None},
        ],
        "subject": lambda t: (re.search(r"pursuant to (?:title [IVXLC\d]+ of )?(.+?)\.?$", re.sub(r"\s+", " ", t or "").strip()) or [None, None])[1],
        "page": {
            "subject_label": "Budget resolution", "heading": "budget reconciliation under {subject}", "measure": "bill",
            "record_title": "What the bill did", "record_noun": "the record of the bill",
            "unresearched": "What the bill did and what followed have not been researched yet.",
            "main": "summary",
            "pair": [["budget_effect", "The Congressional Budget Office's estimate of the effect on deficits"],
                     ["distribution", "The Congressional Budget Office's estimate of effects by household income"],
                     "Both from the Congressional Budget Office. They are estimates made when the bill was considered, not records of what happened."],
            "lines": [["sponsor_reasons", "Stated reasons (supporters)"], ["opponent_reasons", "Stated objections (opponents)"]],
            "status_label": "What became of the bill", "estimate_example": "a Congressional Budget Office estimate",
            "decision": "Congress's votes on this reconciliation bill",
        },
    },
    {
        "id": "emergencies",
        "blurb": "Votes on ending a national emergency, most of them declared to impose tariffs.",
        "name": "Ending national emergencies",
        # Every resolution to end a national emergency under the National Emergencies Act (50 U.S.C. 1622).
        "test": re.compile(r"terminating the national emergency declared", re.I),
        "sector": None, "money": "lobbied", "template": "chain_rule.html",
        "area": lambda t: "energy-environment" if re.search(r"\benergy\b", t or "", re.I) else "trade-industry-ag",
        "yes_means": "advance or pass the resolution ending the emergency",
        "no_means": "oppose it",
        "why": "Under the National Emergencies Act, a national emergency declared by the President ends when a joint "
               "resolution terminating it is enacted into law (50 U.S.C. 1622). While an emergency lasts, each "
               "chamber is to meet every six months to consider a vote on ending it, and a resolution to end it is to "
               "be reported by committee within fifteen calendar days of referral and voted on within three calendar "
               "days after that, unless the chamber decides otherwise by a recorded vote. As a joint resolution, it "
               "takes effect only if signed by the President or passed over a veto by two-thirds of each chamber. "
               "Several of the emergencies here were declared to impose duties on imports, as the resolutions' titles state.",
        "why_sources": [
            {"title": "50 U.S. Code 1622: National emergencies", "url": "https://www.law.cornell.edu/uscode/text/50/1622", "publisher": "LII, Cornell Law School", "date": None},
            {"title": "Constitution of the United States, Article I", "url": "https://www.law.cornell.edu/constitution/articlei", "publisher": "LII, Cornell Law School", "date": None},
        ],
        "subject": lambda t: (re.search(r"national emergency declared (.+?)\.?$", re.sub(r"\s+", " ", t or "").strip()) or [None, None])[1],
        "page": {
            "subject_label": "Emergency", "heading": "ending the national emergency declared {subject}", "measure": "resolution",
            "record_title": "What the emergency did", "record_noun": "the record of the emergency",
            "unresearched": "What the emergency did and what followed have not been researched yet.",
            "main": "declared",
            "pair": None,
            "lines": [["estimates", "Official estimates of its effects"], ["executive_reasons", "Stated basis (the President)"],
                      ["sponsor_reasons", "Stated reasons for the resolution (sponsors and supporters)"]],
            "status_label": "What became of the emergency", "estimate_example": "an official estimate of a tariff's effects",
            "decision": "Congress's vote on whether to end this emergency",
        },
    },
    {
        "id": "war-powers",
        "blurb": "Votes on directing US forces out of hostilities.",
        "name": "War powers",
        # Every resolution or bill directing the removal of US forces from hostilities (War Powers Resolution,
        # 50 U.S.C. 1544(c), and 50 U.S.C. 1546a).
        "test": re.compile(r"(?:remove|removal of) United States Armed Forces from hostilities", re.I),
        "sector": None, "money": "lobbied", "template": "chain_rule.html", "area": "defense-foreign",
        "yes_means": "advance or pass the resolution directing removal of the forces",
        "no_means": "oppose it",
        "why": "Under the War Powers Resolution, the President must end a use of United States Armed Forces in "
               "hostilities within sixty days of reporting it, unless Congress has declared war or specifically "
               "authorized it, has extended the period by law, or cannot meet because of an armed attack; the period "
               "can be extended by up to thirty days for unavoidable military necessity (50 U.S.C. 1544(b)). Section "
               "5(c) provides that forces engaged in hostilities abroad without a declaration of war or specific "
               "statutory authorization shall be removed if Congress so directs by concurrent resolution (50 U.S.C. "
               "1544(c)). A joint resolution or bill requiring their removal receives expedited consideration in the "
               "Senate (50 U.S.C. 1546a); like any joint resolution, it takes effect only if signed by the President "
               "or passed over a veto by two-thirds of each chamber.",
        "why_sources": [
            {"title": "50 U.S. Code 1544: Congressional action", "url": "https://www.law.cornell.edu/uscode/text/50/1544", "publisher": "LII, Cornell Law School", "date": None},
            {"title": "50 U.S. Code 1546a: Expedited procedures for certain joint resolutions and bills", "url": "https://www.law.cornell.edu/uscode/text/50/1546a", "publisher": "LII, Cornell Law School", "date": None},
            {"title": "Constitution of the United States, Article I", "url": "https://www.law.cornell.edu/constitution/articlei", "publisher": "LII, Cornell Law School", "date": None},
        ],
        "subject": lambda t: (re.search(r"hostilities (?:within or )?(?:against|with|in) (?:the )?(.+?)(?: that | which |,|\.?$)",
                                        re.sub(r"\s+", " ", t or "").strip()) or [None, None])[1],
        "page": {
            "subject_label": "Hostilities with", "heading": "removing US forces from hostilities with {subject}",
            "heading_none": "removing US forces from hostilities", "measure": "resolution",
            "record_title": "The hostilities", "record_noun": "the record of the hostilities",
            "unresearched": "The hostilities and what followed have not been researched yet.",
            "main": "hostilities",
            "pair": None,
            "lines": [["executive_reasons", "Stated legal basis (the President)"],
                      ["sponsor_reasons", "Stated reasons for the resolution (sponsors and supporters)"]],
            "status_label": "What became of it", "estimate_example": "an official casualty or cost estimate",
            "decision": "Congress's vote on whether to direct removal of these forces",
        },
    },
]

# Where a rule's agency sits among The Money's issue areas (speculum/money.py), for "What followed".
AGENCY_AREAS = [
    (r"Environmental Protection|Land Management|Ocean Energy|Energy Efficiency|Department of Energy|Fish and Wildlife"
     r"|National Park|Interior|Forest Service|Bureau of Safety and Environmental", "energy-environment"),
    (r"Consumer Financial|Comptroller of the Currency|Federal Reserve|Federal Deposit|Securities and Exchange"
     r"|Commodity Futures|National Credit Union", "finance"),
    (r"Internal Revenue|Treasury", "tax-budget"),
    (r"Health and Human Services|Food and Drug|Medicare|Centers for Disease", "health"),
    (r"Communications Commission", "tech-media"),
    (r"Highway Traffic|Transportation|Housing", "transport-housing"),
    (r"Labor|Education|Veterans", "labor-social"),
    (r"Agriculture|Commerce", "trade-industry-ag"),
]

_AGENCY = re.compile(r"rule submitted by (?:the )?(.+?)(?:,)? (?:relating to|regarding|with respect to)\b", re.I)
_RULE_NAME = re.compile(r"(?:relating to|regarding|with respect to) (.+?)\.?$", re.I)


def rule_parts(title: str) -> tuple[str | None, str | None]:
    """The agency and the rule's name, from a resolution title in the form 5 U.S.C. 802(a) fixes."""
    t = re.sub(r"\s+", " ", title or "").strip()
    a, r = _AGENCY.search(t), _RULE_NAME.search(t)
    name = r.group(1).strip().strip('"\u201c\u201d').strip() if r else None
    return (a.group(1).strip() if a else None), (name or None)


def measures(votes: dict) -> list[tuple[int, str]]:
    """Every measure a Dependents rule selects, by its own title (Senate roll calls carry one). Their bill
    details (final status: became law or not) are fetched each night ahead of anything else."""
    from .bills import norm
    out = []
    for v in (votes or {}).get("votes") or []:
        t = v.get("title") or ""
        if v.get("bill") and any(r["test"].search(t) for r in RULES):
            k = (int(v.get("congress") or 0), norm(v["bill"]))
            if k[0] and k not in out:
                out.append(k)
    return out


def area_of(agency: str | None) -> str:
    for pat, area in AGENCY_AREAS:
        if agency and re.search(pat, agency, re.I):
            return area
    return "government"

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
          former_members: dict | None = None, spend: dict | None = None, bill_info: dict | None = None,
          lobbying_records: dict | None = None) -> list[dict]:
    from .sources.committees import on_defense_committee
    from . import bills as bills_mod
    by_id = {m["bioguide"]: m for m in members}
    info = (bill_info or {}).get("bills", {})
    lobbied = bills_mod.collect(spend, bill_info) if spend else {}

    def title_of(v: dict) -> str:
        # Senate roll calls carry the measure's title; House roll calls get it from Congress.gov bill data.
        return v.get("title") or (info.get(f"{v.get('congress')}-{bills_mod.norm(v.get('bill'))}") or {}).get("title") or ""
    former = {**{k: v for k, v in (votes.get("people") or {}).items()}, **(former_members or {})}
    positions = votes.get("positions", {})
    chains: dict[str, dict] = {}
    for rule in RULES:
        for v in votes.get("votes", []):
            title = title_of(v)
            if not rule["test"].search(title):
                continue
            # One chain per measure: the House writes "HJRES 61" and the Senate "HJRES61" for the same resolution.
            bill = (bills_mod.norm(v.get("bill")) if rule["money"] == "lobbied" else v.get("bill")) or v["id"]
            cid = f"{rule['id']}-{v.get('congress')}-{bill}".lower()
            ch = chains.setdefault(cid, {
                "id": cid, "rule": rule["id"], "rule_name": rule["name"], "why": rule["why"], "why_sources": rule.get("why_sources", []),
                "yes_means": rule["yes_means"], "no_means": rule["no_means"], "sector": rule["sector"],
                "bill": bill, "congress": v.get("congress"), "title": title,
                "country": country_of(title) if rule["money"] == "sector" else None, "votes": [],
                "_rule": rule})
            if rule["money"] == "lobbied" and "page" not in ch:
                import html
                ch["title"] = html.unescape(title)          # Senate titles carry entities such as "&#38;"
                pg = rule["page"]
                ch["page"], ch["subject"] = pg, rule["subject"](ch["title"])
                subj = ch["subject"]
                if subj and not pg.get("fixed_article") and re.search(r"\bthe " + re.escape(subj), ch["title"]):
                    subj = "the " + subj                      # as the title itself says it
                ch["heading"] = pg["heading"].format(subject=subj) if subj else pg.get("heading_none")
                if rule["id"] == "rules-overturned":
                    ch["agency"], ch["rule_title"] = rule_parts(ch["title"])
                ch["lede"] = ch.get("rule_title") or ch["title"]
            pos = positions.get(v["id"], {})
            ch["votes"].append({"id": v["id"], "chamber": v.get("chamber"), "date": (v.get("date") or "")[:10], "question": v.get("question"),
                                "result": v.get("result"), "url": v.get("url"),
                                "yeas": sum(1 for p in pos.values() if ledger.side(p) == "Y"),
                                "nays": sum(1 for p in pos.values() if ledger.side(p) == "N"),
                                "_pos": pos})

    # LD-203 window: from the first day of the earliest reporting year collected through the vote date.
    # Items dated outside the reporting years (typos such as 2029, or late reports of older gifts) are left out.
    tag_claims(research, advocacy_cfg or {})
    current = lobbying

    def lob_for(congress) -> tuple[dict, str | None]:
        """The contributions read for a decision: its own Congress's finished record once that Congress has
        closed, otherwise the current reading. The window starts with that record's first reporting year."""
        lob = (lobbying_records or {}).get(int(congress or 0)) or current
        yrs = [int(y) for y in (lob.get("years") or []) if str(y).isdigit()]
        return lob, (f"{min(yrs)}-01-01" if yrs else None)
    out = []
    for ch in chains.values():
        rule = ch.pop("_rule")
        lobbying, window_start = lob_for(ch.get("congress"))
        area = rule.get("area")
        ch["template"] = rule["template"]
        ch["area"] = area(ch["title"]) if callable(area) else area or area_of(ch.get("agency"))
        ch["window_start"] = window_start
        ch["votes"].sort(key=lambda x: (x["date"], x["id"]))
        if rule["money"] == "lobbied":
            out.append(_build_rule_chain(ch, by_id, former, finance, lobbying, research, reviews, window_start,
                                         lobbied, info))
            continue
        for x in ch["votes"]:
            x.pop("chamber", None)                       # arms votes are Senate votes; the page names the chamber
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
            if other is ch or other["date"] != ch["date"] or other["rule"] != ch["rule"]:
                continue
            o = {r["bioguide"]: r["side"] for r in other["members"] if r["side"]}
            both = [b for b in sides if b in o]
            same = sum(1 for b in both if sides[b] == o[b])
            ch["siblings"].append({"bill": other["bill"], "id": other["id"], "both": len(both), "same": same,
                                   "verdicts": {p: cell["verdict"] for p, cell in other["money_pattern"].items()}})
        if ch["rule"] == "arms-sales":
            ch["same_recipient"] = sorted({o["date"] for o in out if o["rule"] == ch["rule"] and o["country"] == ch["country"]})
            ch["same_recipient_n"] = sum(1 for o in out if o["rule"] == ch["rule"] and o["country"] == ch["country"])
        else:
            ch["same_recipient"], ch["same_recipient_n"] = [], 0
    out.sort(key=lambda c: (c["date"], c["id"]), reverse=True)
    return out


def _build_rule_chain(ch: dict, by_id: dict, former: dict, finance: dict, lobbying: dict, research: dict,
                      reviews: dict, window_start: str | None, lobbied: dict, info: dict) -> dict:
    """A regulation overturned (or kept) under the Congressional Review Act. Both chambers vote on these,
    so the money around the vote is read for each chamber's last recorded vote. The money is from the
    organizations whose own quarterly reports (LD-2) name this resolution, to members, through the vote date
    (LD-203). The reports do not say which side an organization took."""
    bkey = ch["bill"]
    meta = info.get(f"{ch['congress']}-{bkey}") or {}
    ch["latest_action"] = meta.get("latest_action")
    la = (meta.get("latest_action") or {})
    la_text = la.get("text") if isinstance(la, dict) else str(la or "")
    ch["enacted"] = bool(re.search(r"became public law|public law no", la_text or "", re.I))
    ch["latest_action_text"] = la_text or None
    ch["bill_url"] = meta.get("url")
    e = lobbied.get((int(ch["congress"] or 0), bkey)) or {"orgs": {}, "reports": 0, "quarters": []}
    from . import lobbysectors as S
    ch["lobbied_orgs"] = sorted(({"name": o[0], "sector": S.NAMES.get(o[1], o[1]), "own": bool(o[2])} for o in e["orgs"].values()),
                                key=lambda o: (o["name"] or "").lower())
    from collections import Counter
    groups = Counter(S.GROUPS.get(o[1], "Not described") for o in e["orgs"].values())
    ch["lobbied_groups"] = [{"name": g, "n": groups[g]} for g in S.GROUP_ORDER if groups.get(g)]
    ch["lobbied_reports"], ch["lobbied_quarters"] = e.get("reports", 0), e.get("quarters", [])
    lda_ids = {str(o[2]): o[0] for o in e["orgs"].values() if o[2]}
    ch["chambers"] = []
    for chamber in ("house", "senate"):
        vs = [x for x in ch["votes"] if x.get("chamber") == chamber]
        if not vs:
            continue
        last = vs[-1]
        rows, split = [], {}
        for bio, p in last["_pos"].items():
            s = ledger.side(p)
            m = by_id.get(bio)
            party = ledger.caucus_party(m) if m else (former.get(bio) or {}).get("party")
            key = (party or "?", s or ("P" if p == "Present" else "-"))
            split[key] = split.get(key, 0) + 1
            if not m:
                continue
            money = member_sector_money(bio, finance, lobbying, {}, lda_ids, upto=last["date"], since=window_start)
            rows.append({"bioguide": bio, "name": m["name"], "party": m["party"], "caucus": party, "state": m["state"],
                         "position": p, "side": s, **money})
        rows.sort(key=lambda r: (r["side"] or "Z", r["name"]))
        ch["chambers"].append({"chamber": chamber, "vote": {k: v for k, v in last.items() if k != "_pos"},
                               "members": rows, "party_split": {f"{p}-{s}": n for (p, s), n in sorted(split.items())},
                               "unknown_party": sum(n for (p, s), n in split.items() if p == "?"),
                               "money_pattern": _pattern(rows)})
    for x in ch["votes"]:
        x.pop("_pos", None)
    ch["date"] = ch["votes"][-1]["date"]
    last_chamber = ch["chambers"][-1] if ch["chambers"] else {"members": [], "money_pattern": {}}
    for c in ch["chambers"]:
        if c["vote"]["id"] == ch["votes"][-1]["id"]:
            last_chamber = c
    # Shared fields the site and Findings read on every chain
    ch["members"], ch["money_pattern"] = last_chamber["members"], last_chamber["money_pattern"]
    ch["former"], ch["former_detail"], ch["unknown_party"] = [], [], 0
    ch["party_split"] = last_chamber.get("party_split", {})
    ch["committee_control"], ch["defense_committees"], ch["contractor_companies"] = None, [], []
    ch["advocacy_orgs"], ch["advocacy_after"], ch["advocacy_noside"], ch["advocacy_meta"] = {"block": [], "proceed": []}, [], [], {}
    ch["advocacy_pattern"] = None
    r = (research.get("resolutions") or {}).get(bkey, {})
    ch["mechanism"] = r or None
    order = {"documented": 0, "estimated": 1, "associated": 2, "none_found": 3}
    placed = []
    for o in research.get("outcomes", []):
        if o.get("country") or bkey not in (o.get("applies_to") or []):
            continue
        when = o.get("date") or (o.get("source") or {}).get("date") or ""
        rel = "before the vote" if when and when <= ch["date"] else "after the vote" if when else ""
        placed.append({**o, "_order": order.get(o.get("grade"), 4), "when": when, "when_rel": rel,
                       "scope": "context" if o.get("context_only") else "this_rule", "group_label": None})
    ch["outcomes"] = [o for o in placed if o["scope"] != "context"]
    ch["context"] = ([o for o in placed if o["scope"] == "context"] +
                     [x for x in research.get("context", []) if not x.get("country") and bkey in (x.get("applies_to") or [])])
    ch["genocide_note"] = None
    ch["review"] = reviews.get(ch["id"]) or {}
    set_status(ch)
    return ch


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
            sides[sd].append({"name": org["name"], "_fp": org.get("_fp"), "pacs": org.get("pacs", []), "super_pacs": org.get("super_pacs", []),
                              "includes": org.get("includes", []), "mapping_note": org.get("mapping_note"),
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
             advocacy_data: dict | None = None, spend: dict | None = None, bill_info: dict | None = None) -> list[dict]:
    """Chains with everything a page needs: reviewed research where it exists, otherwise the AI
    drafter's output, clearly marked as an unreviewed draft."""
    data_root = Path(data_root)
    research = load_json(data_root / "chains" / "research.json", {})
    from .sources.lda import clean
    records = {}
    for p in sorted((data_root / "records").glob("lobbying-*.json")) if (data_root / "records").exists() else []:
        try:
            records[int(p.stem.split("-")[1])] = clean(load_json(p, {}), members)
        except (ValueError, IndexError):
            continue
    checks = load_json(data_root / "chains" / "checks.json", {}).get("checks", {})
    disputes = load_json(data_root / "chains" / "disputes.json", {})
    out = build(members, finance, lobbying, votes, load_json(data_root / "sectors.json", {}), research,
                load_json(data_root / "chains" / "reviews.json", {}), committees,
                load_json(data_root / "advocacy.json", {}), advocacy_data,
                {k: v for k, v in load_json(data_root / "former_members.json", {}).items() if not k.startswith("_")},
                spend=spend, bill_info=bill_info, lobbying_records=records)
    for ch in out:
        ch["research_by"] = "research" if ch["mechanism"] or ch["outcomes"] else None
        d = load_json(data_root / "chains" / "drafts" / f"{ch['id']}.json", None)
        if d and not ch["research_by"]:
            ch["mechanism"] = d.get("mechanism")
            ch["outcomes"] = d.get("outcomes", [])
            for o in ch["outcomes"] + ([ch["mechanism"]] if ch["mechanism"] else []):
                o["_fp"] = claim_fp(o)
            ch["research_by"] = "ai-draft"
            ch["draft_meta"] = d.get("_drafted", {})
        ch["research_meta"] = research.get("_meta", {})
        set_status(ch)
        apply_checks(ch, checks, disputes)
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


# ---------------------------------------------------------------------------------------------------
# Automatic publication. Every piece of research (an outcome, a context item, a sale's record, an
# organization's stated position) is a claim with a fingerprint of its content. A claim reaches the
# public site only when an independent checker has opened its sources and found that they support it,
# no reader's dispute against it is waiting to be checked, and, for outcomes, its evidence is graded
# documented or estimated (or is a finding that nothing was found). Any edit changes the fingerprint
# and sends the claim back through the check. No person has to approve anything.

PUBLIC_GRADES = {"documented", "estimated", "none_found"}
_PRESENTATION = {"priority", "group"}          # ordering only; changing them does not need a new check


def claim_fp(obj: dict) -> str:
    import hashlib
    body = {k: v for k, v in obj.items() if not k.startswith("_") and k not in _PRESENTATION}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def tag_claims(research: dict, advocacy_cfg: dict) -> None:
    for o in research.get("outcomes", []) + research.get("context", []):
        o["_fp"] = claim_fp(o)
    for r in (research.get("resolutions") or {}).values():
        r["_fp"] = claim_fp(r)
    for org in advocacy_cfg.get("organizations", []):
        org["_fp"] = claim_fp(org)


def all_claims(research: dict, advocacy_cfg: dict, drafts: list[dict] | None = None) -> list[dict]:
    """Every claim the checker must pass, with what it says and where it says it came from. AI drafts
    for chains without research are claims too: they publish only once they pass the same check."""
    tag_claims(research, advocacy_cfg)
    research = {**research, "outcomes": list(research.get("outcomes", [])),
                "resolutions": dict(research.get("resolutions") or {})}
    for i, d in enumerate(drafts or []):
        research["outcomes"] += d.get("outcomes", [])
        if d.get("mechanism"):
            research["resolutions"][f"draft-{i}"] = d["mechanism"]
        for o in d.get("outcomes", []) + ([d["mechanism"]] if d.get("mechanism") else []):
            o["_fp"] = claim_fp(o)
    out = []
    for kind in ("outcomes", "context"):
        for o in research.get(kind, []):
            srcs = [o["source"]] + list(o.get("also") or []) if o.get("source") else list(o.get("also") or [])
            out.append({"fp": o["_fp"], "kind": kind[:-1] if kind == "outcomes" else "context", "grade": o.get("grade"),
                        "label": (o.get("claim") or "")[:90], "item": {k: v for k, v in o.items() if not k.startswith("_")},
                        "sources": srcs})
    for key, r in (research.get("resolutions") or {}).items():
        srcs = list(r.get("sources") or []) + [h["source"] for h in r.get("history", []) if h.get("source")]
        rec = ("rule record" if r.get("rule") or r.get("agency") else
               "sale record" if any(k in r for k in ("items", "contractors", "notification", "notified")) else "decision record")
        what = {"rule record": "what the rule did", "sale record": "what the sale contained"}.get(rec, "what the decision changed")
        out.append({"fp": r["_fp"], "kind": rec, "grade": None, "label": f"{r.get('bill', key)}: {what}",
                    "item": {k: v for k, v in r.items() if not k.startswith("_")}, "sources": srcs})
    for org in advocacy_cfg.get("organizations", []):
        out.append({"fp": org["_fp"], "kind": "stated position", "grade": None, "label": f"{org['name']}: stated positions",
                    "item": {k: org.get(k) for k in ("name", "sides", "positions", "evidence", "after_only", "pacs", "super_pacs")},
                    "sources": list(org.get("evidence") or [])})
    return out


def _passes(fp: str | None, checks: dict, open_fps: set) -> tuple[bool, dict | None, bool]:
    c = checks.get(fp) if fp else None
    held_by_dispute = fp in open_fps and not (c or {}).get("dispute_upheld")
    return bool(c and c.get("result") == "supported" and not held_by_dispute), c, held_by_dispute


def apply_checks(ch: dict, checks: dict, disputes: dict) -> None:
    open_fps = {d.get("fp") for d in (disputes or {}).get("open", [])}
    held = {"unchecked": 0, "failed": 0, "disputed": 0, "weak": 0}

    def mark(item: dict, graded: bool) -> None:
        ok, c, disputed = _passes(item.get("_fp"), checks, open_fps)
        item["_check"], item["_disputed"] = c, disputed
        weak = graded and item.get("grade") not in PUBLIC_GRADES
        item["_public"] = ok and not weak
        if not item["_public"]:
            held["disputed" if disputed else "unchecked" if not c else "failed" if c.get("result") != "supported" else "weak"] += 1

    for o in ch.get("outcomes") or []:
        mark(o, graded=True)
    for x in ch.get("context") or []:
        mark(x, graded=bool(x.get("grade")))
    mx = ch.get("mechanism") or {}
    ch["mechanism_public"], ch["mechanism_check"], ch["mechanism_disputed"] = _passes(mx.get("_fp"), checks, open_fps)
    orgs = [o for side in (ch.get("advocacy_orgs") or {}).values() for o in side]
    for o in orgs:
        o["_public"], o["_check"], o["_disputed"] = _passes(o.get("_fp"), checks, open_fps)
    ch["advocacy_public"] = all(o["_public"] for o in orgs)
    ch["held"] = held
    ch["auto_public"] = bool(ch["mechanism_public"])
