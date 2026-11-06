"""Findings: plain statements computed from the record by fixed rules, the same every night.

Every finding here is a measure chosen in advance, not a selection of what looks interesting: the list,
the rule behind each one and its scope are published on the Method page, and each statement links to the
data it comes from. A finding states a number and what it counts. It does not say what the number means.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

YES = {"Yea", "Aye", "Yes", "Guilty"}
NO = {"Nay", "No", "Not Guilty"}
CHAMBER = {"house": "House", "senate": "Senate"}


def vote_kind(question: str | None) -> str:
    q = question or ""
    if "Nomination" in q:
        return "nominations"
    if any(s in q for s in ("Passage", "Suspend the Rules and Pass", "Joint Resolution", "Agreeing to the Resolution",
                            "Concur", "Conference Report", "Veto", "Overrid")):
        return "bills"
    if "Amendment" in q:
        return "amendments"
    return "procedure"


def party_agreement(votes: dict, members: list[dict]) -> dict:
    """For each chamber: roll calls where both parties' majorities took the same side, and where at least 90%
    of each party voted together on opposite sides. Quorum calls and votes where a party cast no yea or nay
    are left out. Party is the member's party in the record (members' current party; senators' party at the vote)."""
    party = {m["bioguide"]: m["party"] for m in members}
    for k, p in (votes.get("people") or {}).items():
        party.setdefault(k, (p or {}).get("party"))
    positions = votes.get("positions") or {}
    out: dict = defaultdict(lambda: defaultdict(Counter))
    dates = []
    for v in votes.get("votes") or []:
        if v.get("type") == "Quorum":
            continue
        c = {"D": Counter(), "R": Counter()}
        for bio, pos in (positions.get(v["id"]) or {}).items():
            p = party.get(bio)
            if p in c:
                c[p]["y" if pos in YES else "n" if pos in NO else "o"] += 1
        if not all(c[p]["y"] + c[p]["n"] for p in c):
            continue
        side = {p: "y" if c[p]["y"] > c[p]["n"] else "n" if c[p]["n"] > c[p]["y"] else "tie" for p in c}
        same = side["D"] == side["R"] != "tie"
        solid = all(max(c[p]["y"], c[p]["n"]) / (c[p]["y"] + c[p]["n"]) >= 0.9 for p in c)
        for kind in ("all", vote_kind(v.get("question"))):
            t = out[v["chamber"]][kind]
            t["n"] += 1
            t["same"] += same
            t["solid_opposed"] += (not same) and solid
            t["solid_same"] += same and solid
        dates.append((v.get("date") or "")[:10])
    return {"chambers": {ch: {k: dict(t) for k, t in kinds.items()} for ch, kinds in out.items()},
            "from": min(dates) if dates else None, "through": max(dates) if dates else None}


def _pct(a, b):
    return a / b if b else 0.0


def build(members: list[dict], votes: dict, money: dict, chains: list[dict] | None, bills: dict | None = None) -> dict:
    F = []

    # How they vote
    pa = party_agreement(votes, members)
    for ch in ("house", "senate"):
        t = pa["chambers"].get(ch, {})
        a, b = t.get("all", {}), t.get("bills", {})
        if not a.get("n"):
            continue
        F.append({"id": f"agree-{ch}", "group": "How they vote", "head": _pct(a["same"], a["n"]), "fmt": "pct",
                  "text": f"of {CHAMBER[ch]} roll calls had a majority of Democrats and a majority of Republicans on the same side "
                          f"({a['same']:,} of {a['n']:,}).",
                  "sub": (f"On passing bills and resolutions: {_pct(b['same'], b['n']):.0%} ({b['same']:,} of {b['n']:,}). " if b.get("n") else "")
                         + f"On {_pct(a['solid_opposed'], a['n']):.0%} of roll calls, at least 90% of each party voted together, on opposite sides.",
                  "scope": f"Roll calls {pa['from']} to {pa['through']}, quorum calls left out.",
                  "link": "chamber.html", "link_label": "Every member's votes"})

    # Where the money comes from
    ov = money.get("overlap_all") or {}
    if ov.get("orgs"):
        top = money.get("overlap_top100") or {}
        F.append({"id": "shared-funders", "group": "Where the money comes from", "head": _pct(ov["money_from_both"], ov["money"]), "fmt": "pct",
                  "text": "of the money given to sitting members by organizations that lobby came from organizations that gave to members of both parties.",
                  "sub": f"{ov['both']:,} of {ov['orgs']:,} organizations gave to both parties; {ov['only_D']:,} gave only to Democrats and {ov['only_R']:,} only to Republicans. "
                         f"Of the 100 that gave the most, {top.get('both', 0)} gave to both.",
                  "scope": f"Federal campaign contributions reported on LD-203, {money.get('lda_years') or ''}.",
                  "link": "funders.html", "link_label": "Do the parties share funders?"})
    camp = money.get("campaign") or {}
    rows = []
    for ch in ("senate", "house"):
        for p in ("D", "R"):
            c = camp.get(f"{ch}-{p}")
            if c and c.get("receipts"):
                rows.append((ch, p, c))
    if rows:
        def share(c, k):
            tot = c["small"] + c["large"] + c["pac"] + c["party_cmte"] + c["self"] + c["other"]
            return _pct(c[k], tot)
        name = {"D": "Democrats", "R": "Republicans"}
        F.append({"id": "small-gifts", "group": "Where the money comes from", "head": None, "fmt": None,
                  "text": "Share of campaign receipts from small gifts (under $200): "
                          + "; ".join(f"{CHAMBER[ch]} {name[p]} {share(c, 'small'):.0%}" for ch, p, c in rows) + ".",
                  "sub": "From PACs and other committees: " + "; ".join(f"{CHAMBER[ch]} {name[p]} {share(c, 'pac'):.0%}" for ch, p, c in rows) + ".",
                  "scope": "Each sitting member's principal campaign committee, this cycle (FEC).",
                  "link": "money.html", "link_label": "Where campaign money comes from"})
    areas = [a for a in money.get("areas") or [] if a["key"] in _AREA_KEYS and (a["given"]["D"] + a["given"]["R"])]
    seat_d = money.get("d_seat_share")
    if areas and seat_d:
        over = [a for a in areas if a["given"]["R"] / (a["given"]["D"] + a["given"]["R"]) > 1 - seat_d]
        lo = min(areas, key=lambda a: a["given"]["R"] / (a["given"]["D"] + a["given"]["R"]))
        hi = max(areas, key=lambda a: a["given"]["R"] / (a["given"]["D"] + a["given"]["R"]))
        r = lambda a: a["given"]["R"] / (a["given"]["D"] + a["given"]["R"])
        F.append({"id": "areas-vs-seats", "group": "Where the money comes from", "head": None, "fmt": None,
                  "text": f"In {len(over)} of {len(areas)} issue areas, Republican members received a larger share of the money from organizations that lobby "
                          f"than their share of the two parties' seats ({1 - seat_d:.1%}).",
                  "sub": f"The Republican share runs from {r(lo):.1%} ({lo['name']}) to {r(hi):.1%} ({hi['name']}). "
                         "The party with the majority chairs every committee.",
                  "scope": "Organizations placed in issue areas by what they lobby on (LD-2).",
                  "link": "money.html", "link_label": "Money by issue area"})
    if money.get("orgs"):
        o = money["orgs"][0]
        dr = o["D"] + o["R"]
        F.append({"id": "largest-giver", "group": "Where the money comes from", "head": o["total"], "fmt": "money",
                  "text": f"to sitting members from {o['name']}, more than from any other organization that lobbies.",
                  "sub": f"It gave to {o['members']} members: {_pct(o['D'], dr):.0%} of the money to Democrats, {_pct(o['R'], dr):.0%} to Republicans.",
                  "scope": f"LD-203, {money.get('lda_years') or ''}.",
                  "link": "funders.html", "link_label": "The 100 largest"})

    # Who lobbies
    if money.get("spend_total"):
        sp = sorted((a for a in money.get("areas") or [] if a.get("spent")), key=lambda a: -a["spent"])
        q = money.get("spend_quarters") or []
        F.append({"id": "lobbying-spend", "group": "Who lobbies", "head": money["spend_total"], "fmt": "money",
                  "text": f"reported spent lobbying Congress and the executive branch, {q[0]} to {q[-1]}." if q else "reported spent lobbying.",
                  "sub": ("Largest issue areas: " + ", ".join(f"{a['name'].lower()} ({_money(a['spent'])})" for a in sp[:3]) + ".") if sp else "",
                  "scope": "Quarterly lobbying reports (LD-2); an organization lobbying for itself is counted once.",
                  "link": "money.html", "link_label": "What organizations spend lobbying"})

    # Who lobbies bills: every organization that named a bill with a page, counted once
    if bills and bills.get("bills"):
        from .lobbysectors import GROUPS, GROUP_ORDER
        seen = {}
        for b in bills["bills"]:
            for o in b["orgs"]:
                seen.setdefault((o["name"] or "").lower(), o["sector"])
        g = Counter(GROUPS[s] for s in seen.values())
        n = sum(g.values())
        top = bills["bills"][0]
        F.append({"id": "bill-lobbyists", "group": "Who lobbies", "head": _pct(g.get("Business", 0), n), "fmt": "pct",
                  "text": f"of the {n:,} organizations that named bills on lobbying reports are businesses or business groups.",
                  "sub": "; ".join(f"{k.lower()} {_pct(g[k], n):.0%}" for k in GROUP_ORDER[1:] if g.get(k)).capitalize()
                         + f". The most lobbied bill, {top['label']}, was named by {top['n_orgs']:,} organizations; "
                         f"{len(top['former']):,} of the lobbyists who named it list a former government post.",
                  "scope": "Quarterly lobbying reports (LD-2) since 2025; sectors from each organization's own description of its business.",
                  "link": "bills.html", "link_label": "Who lobbies each bill"})

    # What followed
    sales = [c for c in chains or [] if c.get("votes")]
    if sales:
        vs = [(c, v) for c in sales for v in c["votes"]]
        passed = [1 for c, v in vs if (v.get("result") or "").lower().startswith(("passed", "agreed", "joint resolution passed"))]
        best = max(vs, key=lambda cv: cv[1].get("yeas") or 0)
        F.append({"id": "arms-votes", "group": "What followed", "head": len(vs), "fmt": "int",
                  "text": f"Senate votes since 2025 on resolutions to block a foreign arms sale. {len(passed) or 'None'} passed.",
                  "sub": f"The most votes to advance one: {best[1].get('yeas')} ({billname(best[0].get('bill', ''))}, {best[1].get('date')}). "
                         "Each decision is traced to what followed in The Dependents.",
                  "scope": "Decisions chosen by fixed rules, not by topic or party.",
                  "link": "dependents.html", "link_label": "The Dependents"})
    return {"findings": F, "groups": list(dict.fromkeys(f["group"] for f in F)), "agreement": pa}


_AREA_KEYS = {"defense-foreign", "health", "finance", "tax-budget", "energy-environment", "tech-media",
              "transport-housing", "trade-industry-ag", "labor-social", "government"}


def billname(code: str) -> str:
    m = re.match(r"^(HCONRES|SCONRES|HJRES|SJRES|HRES|SRES|HR|S)\s*(\d+)$", (code or "").upper().replace(".", "").replace(" ", ""))
    names = {"HCONRES": "H.Con.Res.", "SCONRES": "S.Con.Res.", "HJRES": "H.J.Res.", "SJRES": "S.J.Res.",
             "HRES": "H.Res.", "SRES": "S.Res.", "HR": "H.R.", "S": "S."}
    return f"{names[m.group(1)]} {m.group(2)}" if m else (code or "")


def _money(x: float) -> str:
    return f"${x / 1e9:.2f}B" if x >= 1e9 else f"${x / 1e6:,.0f}M"
