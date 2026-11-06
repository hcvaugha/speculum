"""Who lobbied each bill: the organizations naming it on their lobbying reports, by sector, the lobbyists who
list a former government post, the roll calls on it, and the money those organizations gave the members who
voted each way.

Rules (published on the Method page):
- A bill is "lobbied" by an organization when a quarterly lobbying report (LD-2) for that organization names
  the bill in its description of the lobbying. Reports do not say whether the organization was for or against.
- Organizations are counted once per bill, by name, across all quarters; sectors come from speculum/lobbysectors.
- Money: federal campaign contributions (LD-203) from organizations that lobbied the bill and file their own
  lobbying reports (so their registrant is the organization itself), to the members who voted yea or nay on each
  roll call. Every contribution reported since 2025 counts, whether before or after the vote. Contributions by
  lobbying firms are not counted here, since a firm works for many clients.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from . import lobbysectors as S
from .findings import YES, NO, billname

MIN_ORGS_FOR_PAGE = 5       # a page for every bill named by at least this many organizations, and every voted bill lobbied at all


def norm(bill: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (bill or "").upper())


def _congress(label: str) -> int:
    y = int(label[:4])
    return (y - 1789) // 2 + 1


QUARTER_END = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}


from .sources.lobbyspend import no_post as lobbyspend_no_post


def collect(spend: dict | None, info: dict | None = None) -> dict:
    """Merge the quarters: per (congress, bill) the organizations, report count, quarters and former officials.
    A mention in a quarter that ended before the bill was introduced refers to an earlier Congress's bill of the
    same number, so it is left out (when the introduction date is known)."""
    out: dict = {}
    info = (info or {}).get("bills", {})
    for label, q in sorted((spend or {}).get("quarters", {}).items()):
        cg = _congress(label)
        qend = f"{label[:4]}-{QUARTER_END[int(label[-1])]}"
        orgs, people = q.get("orgs") or {}, q.get("people") or {}
        for b, v in (q.get("bills") or {}).items():
            intro = (info.get(f"{cg}-{b}") or {}).get("introduced")
            if intro and intro > qend:
                continue
            e = out.setdefault((cg, b), {"orgs": {}, "reports": 0, "quarters": [], "former": {}})
            for k in v.get("orgs") or []:
                if k in orgs and k not in e["orgs"]:
                    e["orgs"][k] = orgs[k]
                elif k in orgs and orgs[k][2] and not e["orgs"][k][2]:
                    e["orgs"][k] = orgs[k]
            for lid in v.get("former") or []:
                if lid in people and not lobbyspend_no_post(people[lid][1] if len(people[lid]) > 1 else ""):
                    e["former"][lid] = people[lid]
            e["reports"] += v.get("reports", 0)
            e["quarters"].append(label)
    return out


def wanted(spend: dict | None, votes: dict | None, top: int = 600) -> list[tuple[int, str]]:
    """Bills whose titles the pages need: the most lobbied, and voted bills without a title."""
    c = collect(spend)
    ranked = [k for k, v in sorted(c.items(), key=lambda kv: (-len(kv[1]["orgs"]), kv[0])) if len(v["orgs"]) >= MIN_ORGS_FOR_PAGE][:top]
    voted = {(int(v.get("congress") or 0), norm(v.get("bill"))) for v in (votes or {}).get("votes") or []
             if v.get("bill") and not v.get("title")}
    return list(dict.fromkeys(ranked + sorted(k for k in voted if k[0])))


def build(spend: dict | None, votes: dict, members: list[dict], lobbying: dict, info: dict | None) -> dict:
    lobbied = collect(spend, info)
    info = (info or {}).get("bills", {})
    party = {m["bioguide"]: m["party"] for m in members}
    names = {m["bioguide"]: m.get("name") for m in members}
    for k, p in (votes.get("people") or {}).items():
        party.setdefault(k, (p or {}).get("party"))

    # Roll calls by bill
    by_bill = defaultdict(list)
    titles = {}
    for v in votes.get("votes") or []:
        if not v.get("bill") or v.get("type") == "Quorum":
            continue
        k = (int(v.get("congress") or 0), norm(v["bill"]))
        by_bill[k].append(v)
        if v.get("title") and k not in titles:
            titles[k] = v["title"]

    # LD-203 contributions by member, by registrant
    given = defaultdict(lambda: defaultdict(float))
    for bio, rows in (lobbying.get("by_member") or {}).items():
        for r in rows:
            if (r.get("type") or "").upper() == "FECA" and r.get("amount") and r.get("registrant_id"):
                given[bio][str(r["registrant_id"])] += float(r["amount"])

    positions = votes.get("positions") or {}
    pages = []
    for key, e in lobbied.items():
        n = len(e["orgs"])
        if n < MIN_ORGS_FOR_PAGE and not (key in by_bill and n):
            continue
        cg, b = key
        meta = info.get(f"{cg}-{b}") or {}
        if meta.get("missing") and key not in by_bill:
            continue                                   # a number that is not a bill (a typo in a filing)
        sec = Counter(o[1] for o in e["orgs"].values())
        grp = Counter()
        for s, c in sec.items():
            grp[S.GROUPS[s]] += c
        regs = {o[2] for o in e["orgs"].values() if o[2]}
        rolls = []
        for v in sorted(by_bill.get(key, []), key=lambda x: x.get("date") or ""):
            pos = positions.get(v["id"]) or {}
            t = {"D": Counter(), "R": Counter(), "I": Counter()}
            money = {"Y": 0.0, "N": 0.0}
            got = {"Y": 0, "N": 0}
            nvote = {"Y": 0, "N": 0}
            for bio, p in pos.items():
                side = "Y" if p in YES else "N" if p in NO else None
                pt = party.get(bio)
                if pt in t:
                    t[pt][side or "O"] += 1
                if side:
                    nvote[side] += 1
                    amt = sum(given[bio].get(r, 0.0) for r in regs) if regs else 0.0
                    money[side] += amt
                    got[side] += amt > 0
            rolls.append({"id": v["id"], "chamber": v["chamber"], "date": (v.get("date") or "")[:10], "question": v.get("question"),
                          "result": v.get("result"), "url": v.get("url"),
                          "party": {p: dict(c) for p, c in t.items() if sum(c.values())},
                          "money": {s: round(x, 2) for s, x in money.items()}, "got": got, "n": nvote})
        orgs = sorted(({"name": o[0], "sector": o[1], "own": bool(o[2])} for o in e["orgs"].values()),
                      key=lambda o: (S.ORDER.index(o["sector"]), (o["name"] or "").lower()))
        pages.append({
            "congress": cg, "bill": b, "label": billname(b), "slug": f"{cg}-{b.lower()}",
            "title": titles.get(key) or meta.get("title"), "info": meta,
            "n_orgs": n, "reports": e["reports"], "quarters": e["quarters"],
            "sectors": [{"key": s, "name": S.NAMES[s], "group": S.GROUPS[s], "n": sec[s]} for s in S.ORDER if sec.get(s)],
            "groups": [{"name": g, "n": grp[g]} for g in S.GROUP_ORDER if grp.get(g)],
            "business_share": round(grp.get("Business", 0) / n, 4) if n else None,
            "orgs": orgs, "former": sorted(({"name": p[0], "post": p[1]} for p in e["former"].values()), key=lambda x: x["name"]),
            "rolls": rolls, "money_orgs": len(regs),
        })
    pages.sort(key=lambda p: (-p["n_orgs"], p["congress"], p["bill"]))   # ties in a fixed order, whatever order the file lists them in
    return {"bills": pages, "min_orgs": MIN_ORGS_FOR_PAGE,
            "quarters": sorted((spend or {}).get("quarters", {})),
            "by_slug": {p["slug"]: p for p in pages},
            "voted_slugs": {(p["congress"], p["bill"]): p["slug"] for p in pages}}
