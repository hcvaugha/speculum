"""Where the money goes: the site-wide readings behind the Money and Shared funders pages.

Three measures, each from one kind of filing, each the same for every member and party:
  1. Campaign money by source (FEC totals for each member's principal campaign committee, this cycle).
  2. Money from organizations that lobby (LD-203 reports of federal campaign contributions by lobbying
     registrants, their PACs and lobbyists, since 2025), placed in issue areas by what each organization
     lobbies on (its LD-2 reports) and followed to members of each party.
  3. What organizations spend lobbying (LD-2 reports since 2025), by issue area.
No measure states a conclusion. Shares are shown beside each party's share of seats, so a reader can tell
how money divides from how seats divide.
"""
from __future__ import annotations

import re
from collections import defaultdict

# Broad issue areas. Every LDA general issue code belongs to exactly one; the grouping is published on
# the Method page and each area page lists the codes it contains.
AREAS = {
    "defense-foreign": ("Defense, security and foreign affairs", ["DEF", "HOM", "INT", "FOR", "VET", "AER", "DIS"]),
    "health": ("Health and medicine", ["HCR", "MMM", "MED", "PHA", "ALC", "TOB"]),
    "finance": ("Finance, banking and insurance", ["FIN", "BAN", "BNK", "INS", "ACC", "MON", "RET", "CDT"]),
    "tax-budget": ("Taxes, budget and economy", ["TAX", "BUD", "ECN", "SMB", "UNM"]),
    "energy-environment": ("Energy, environment and natural resources", ["ENG", "FUE", "ENV", "CAW", "NAT", "UTI", "WAS", "MAR"]),
    "tech-media": ("Technology, telecommunications and media", ["CPI", "SCI", "TEC", "COM", "MIA", "CPT", "ADV"]),
    "transport-housing": ("Transportation, infrastructure and housing", ["TRA", "AVI", "AUT", "RRR", "ROD", "TRU", "URB", "HOU", "RES", "POS"]),
    "trade-industry-ag": ("Trade, industry and agriculture", ["TRD", "MAN", "CHM", "AGR", "FOO", "BEV", "APP", "TOU", "GAM", "SPO", "ART", "ANI", "TOR"]),
    "labor-social": ("Labor, education, justice and social policy", ["LBR", "EDU", "WEL", "FAM", "IMM", "CIV", "REL", "IND", "CON", "CSP", "LAW", "FIR"]),
    "government": ("Government operations", ["GOV", "DOC"]),
}
CODE_AREA = {c: k for k, (_, codes) in AREAS.items() for c in codes}
FIRMS = "lobbying-firms"
UNPLACED = "unplaced"
AREA_NAMES = {**{k: v[0] for k, v in AREAS.items()},
              FIRMS: "Lobbying firms (contributions from firms that lobby for many clients)",
              UNPLACED: "Not placed (no lobbying report with issue areas found)"}
PARTIES = ("D", "R", "I")


def area_of_code(code: str) -> str:
    return CODE_AREA.get(code, "government")


def _registrant_mix(spend: dict) -> dict[str, dict]:
    """Each registrant's issue areas across all quarters, as shares summing to 1; firms flagged."""
    acc: dict[str, dict] = {}
    for q in (spend or {}).get("quarters", {}).values():
        for rid, r in (q.get("registrants") or {}).items():
            a = acc.setdefault(rid, {"name": r.get("name"), "own": 0, "for_clients": 0, "areas": defaultdict(float)})
            a["own"] += r.get("own", 0)
            a["for_clients"] += r.get("for_clients", 0)
            for code, w in (r.get("issues") or {}).items():
                a["areas"][area_of_code(code)] += w
    out = {}
    for rid, a in acc.items():
        tot = sum(a["areas"].values())
        firm = a["for_clients"] > a["own"]
        out[rid] = {"name": a["name"], "firm": firm,
                    "areas": {} if firm or not tot else {k: v / tot for k, v in a["areas"].items()}}
    return out


def seats(members: list[dict]) -> dict:
    s = {"house": defaultdict(int), "senate": defaultdict(int), "all": defaultdict(int)}
    for m in members:
        s[m["chamber"]][m["party"]] += 1
        s["all"][m["party"]] += 1
    return {k: dict(v) for k, v in s.items()}


def campaign_sources(members: list[dict], finance: dict) -> dict:
    """FEC receipts by source, per party and chamber, for members with a principal campaign committee."""
    keys = ("small", "large", "pac", "party_cmte", "self")
    out = {}
    for ch in ("house", "senate"):
        for p in PARTIES:
            rows = [finance[m["bioguide"]] for m in members if m["chamber"] == ch and m["party"] == p and m["bioguide"] in finance]
            if not rows:
                continue
            tot = {k: round(sum((r.get("party" if k == "party_cmte" else k) or 0) for r in rows), 2) for k in keys}
            receipts = round(sum((r.get("receipts") or 0) for r in rows), 2)
            tot["other"] = round(max(0.0, receipts - sum(tot.values())), 2)
            out[f"{ch}-{p}"] = {"chamber": ch, "party": p, "members": len(rows), "receipts": receipts, **tot,
                                "through": max((r.get("coverage_end") or "") for r in rows)}
    return out


def _feca(lobbying: dict, members: list[dict]):
    """Every LD-203 federal campaign contribution to a sitting member: (registrant id, name, member, amount)."""
    by_id = {m["bioguide"]: m for m in members}
    for bio, rows in (lobbying.get("by_member") or {}).items():
        m = by_id.get(bio)
        if not m:
            continue
        for r in rows:
            if (r.get("type") or "").upper() != "FECA" or not r.get("amount"):
                continue
            yield str(r.get("registrant_id") or ""), r.get("reported_by") or "", m, float(r["amount"])


def build(members: list[dict], finance: dict, lobbying: dict, spend: dict | None, chains: list[dict] | None = None,
          set_aside: list[dict] | None = None) -> dict:
    from .sources.lobbyspend import name_key
    aside = {name_key(x["client"]) for x in set_aside or []}
    mix = _registrant_mix(spend or {})
    st = seats(members)
    two = st["all"].get("D", 0) + st["all"].get("R", 0)

    # 2. Money from organizations that lobby, by issue area and party
    area_party: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    orgs: dict[str, dict] = {}
    for rid, who, m, amt in _feca(lobbying, members):
        mx = mix.get(rid)
        name = (mx or {}).get("name") or re.sub(r"\s*\(.*\)$", "", who) or "Unnamed filer"
        o = orgs.setdefault(rid or name, {"id": rid, "name": name, "D": 0.0, "R": 0.0, "I": 0.0, "members": set(),
                                         "firm": bool(mx and mx["firm"]), "areas": (mx or {}).get("areas") or {}})
        o[m["party"]] += amt
        o["members"].add(m["bioguide"])
        if not mx:
            area_party[UNPLACED][m["party"]] += amt
        elif mx["firm"] or not mx["areas"]:
            area_party[FIRMS if mx["firm"] else UNPLACED][m["party"]] += amt
        else:
            for a, share in mx["areas"].items():
                area_party[a][m["party"]] += amt * share

    # 3. Lobbying spending by issue area (all quarters fetched)
    quarters = sorted((spend or {}).get("quarters", {}).items())
    spend_area: dict[str, float] = defaultdict(float)
    spend_codes: dict[str, dict] = {}
    top_clients: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: {"name": "", "amount": 0.0}))
    aside_total = 0.0
    for label, q in quarters:
        for code, v in (q.get("issues") or {}).items():
            spend_area[area_of_code(code)] += v["amount"]
            e = spend_codes.setdefault(code, {"name": v["name"], "amount": 0.0, "area": area_of_code(code)})
            e["amount"] += v["amount"]
        for c in q.get("top_clients") or []:
            if name_key(c["name"]) in aside:          # published on the Method page, with the filings
                aside_total += c["amount"]
                for code, amt in (c.get("issues") or {}).items():
                    spend_area[area_of_code(code)] -= amt
                    spend_codes[code]["amount"] -= amt
                continue
            for code, amt in (c.get("issues") or {}).items():
                t = top_clients[area_of_code(code)][c["id"]]
                t["name"], t["amount"] = c["name"], t["amount"] + amt

    # Decisions traced in The Dependents, by area (arms sales are defense and foreign affairs; an overturned
    # regulation goes under its agency's area)
    traced = defaultdict(list)
    for c in chains or []:
        if not c.get("research_by"):
            continue                                     # only decisions actually followed to a record
        traced[c.get("area") or "defense-foreign"].append({"id": c["id"], "bill": c.get("bill"), "country": c.get("country"),
                                                          "title": c.get("title"), "date": c.get("date"),
                                                          "agency": c.get("agency"), "rule_title": c.get("rule_title")})

    areas = []
    for key in list(AREAS) + [FIRMS, UNPLACED]:
        ap = area_party.get(key, {})
        tot = sum(ap.values())
        dr = ap.get("D", 0) + ap.get("R", 0)
        areas.append({"key": key, "name": AREA_NAMES[key], "codes": AREAS.get(key, (None, []))[1],
                      "given": {p: round(ap.get(p, 0), 2) for p in PARTIES}, "given_total": round(tot, 2),
                      "d_share": round(ap.get("D", 0) / dr, 4) if dr else None,
                      "spent": round(spend_area.get(key, 0), 2),
                      "top_clients": sorted(({"id": k, **v} for k, v in top_clients.get(key, {}).items()),
                                            key=lambda x: -x["amount"])[:15],
                      "traced": traced.get(key, [])})

    # Shared funders: organizations that gave to members of both parties
    org_list = []
    for o in orgs.values():
        tot = o["D"] + o["R"] + o["I"]
        dr = o["D"] + o["R"]
        org_list.append({"id": o["id"], "name": o["name"], "D": round(o["D"], 2), "R": round(o["R"], 2), "I": round(o["I"], 2),
                         "total": round(tot, 2), "members": len(o["members"]), "firm": o["firm"],
                         "d_share": round(o["D"] / dr, 4) if dr else None, "both": o["D"] > 0 and o["R"] > 0,
                         "area": max(o["areas"], key=o["areas"].get) if o["areas"] else (FIRMS if o["firm"] else UNPLACED)})
    org_list.sort(key=lambda x: -x["total"])

    def overlap(rows):
        n = len(rows)
        both = [r for r in rows if r["both"]]
        money = sum(r["D"] + r["R"] for r in rows)
        return {"orgs": n, "both": len(both), "only_D": sum(1 for r in rows if r["D"] > 0 and r["R"] == 0),
                "only_R": sum(1 for r in rows if r["R"] > 0 and r["D"] == 0),
                "money": round(money, 2), "money_from_both": round(sum(r["D"] + r["R"] for r in both), 2)}

    THRESHOLD = 10_000
    big = [o for o in org_list if o["D"] + o["R"] >= THRESHOLD]
    hist = [0] * 10
    for o in big:
        hist[min(9, int(o["d_share"] * 10))] += 1
    top100 = org_list[:100]
    return {
        "seats": st, "d_seat_share": round(st["all"].get("D", 0) / two, 4) if two else None,
        "campaign": campaign_sources(members, finance),
        "areas": areas,
        "spend_codes": sorted(({"code": k, **v} for k, v in spend_codes.items()), key=lambda x: -x["amount"]),
        "spend_total": round(sum(q.get("total", 0) for _, q in quarters) - aside_total, 2),
        "spend_placed": round(sum(spend_area.values()), 2),
        "set_aside": [{**x, "amount": None} for x in set_aside or []], "set_aside_total": round(aside_total, 2),
        "spend_quarters": [label for label, q in quarters],
        "spend_partial": [label for label, q in quarters if not q.get("complete")],
        "given_total": round(sum(a["given_total"] for a in areas), 2),
        "lda_years": lobbying.get("years_label") or "",
        "orgs": org_list[:300],
        "overlap_all": overlap(org_list), "overlap_big": overlap(big), "overlap_top100": overlap(top100),
        "threshold": THRESHOLD, "hist": hist, "hist_n": len(big),
    }
