"""Interest pages: the other side of the lens. Every committee that gives to members of Congress
(PACs, party committees, other campaigns) and every lobbying organization that reports
contributions to them, read the same way: who they fund, how much, and how it splits by party.

Two sources feed it, kept distinct:
  committees   FEC itemized receipts to each member's principal campaign committee
               (the largest givers collected per member, so totals are a floor, not a census)
  registrants  lobbying organizations and their lobbyists, from LD-203 contribution reports

A registrant is linked to an FEC committee only when one of the PAC names it lists on its LD-203
reports matches the committee's name after normalizing; the link is labelled as a name match.
"""
from __future__ import annotations

import re

from .names import is_placeholder, org_name
from collections import defaultdict

from . import ledger

TYPE_LABEL = {"PAC": "PAC", "PTY": "Party committee", "CCM": "Candidate committee", "COM": "Committee",
              "ORG": "Organization", "CAN": "Candidate", None: "Committee"}
_DROP = re.compile(r"\b(the|inc|incorporated|corp|corporation|co|llc|of|and|for|fund)\b")


def norm_name(name: str) -> str:
    s = (name or "").lower().replace("&", " and ")
    s = s.replace("political action committee", "pac")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = _DROP.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _party_split(rows: list[dict], by_id: dict) -> dict:
    split = defaultdict(float)
    for r in rows:
        m = by_id.get(r["bioguide"])
        if m:
            split[ledger.caucus_party(m)] += r["amount"]
    total = sum(v for v in split.values() if v > 0)
    return {p: (v / total if total else 0.0) for p, v in split.items()}


def build(members: list[dict], finance: dict, lobbying: dict) -> dict:
    by_id = {m["bioguide"]: m for m in members}
    committees: dict[str, dict] = {}
    for bio, fin in finance.items():
        for c in fin.get("top_committees") or []:
            key = c.get("committee_id") or c.get("name")
            if not key:
                continue
            e = committees.setdefault(key, {"key": key, "kind": "committee", "committee_id": c.get("committee_id"),
                                            "name": org_name(c.get("name") or key), "type": TYPE_LABEL.get(c.get("entity_type"), "Committee"),
                                            "rows": []})
            e["rows"].append({"bioguide": bio, "amount": float(c.get("amount") or 0), "count": c.get("count") or 0,
                              "cycle_label": fin.get("cycle_label")})

    registrants: dict[str, dict] = {}
    reg_meta = lobbying.get("registrants", {})
    for bio, items in lobbying.get("by_member", {}).items():
        for it in items:
            rid = it.get("registrant_id")
            if not rid:
                continue
            meta = reg_meta.get(rid, {})
            e = registrants.setdefault(rid, {"key": rid, "kind": "registrant", "name": org_name(meta.get("name") or it.get("reported_by") or ""),
                                             "type": "Lobbying organization", "description": meta.get("description"),
                                             "pacs": [p for p in meta.get("pacs", []) if not is_placeholder(p)], "rows": [], "items": []})
            e["items"].append({**it, "bioguide": bio})
    for e in registrants.values():
        per = defaultdict(lambda: {"amount": 0.0, "count": 0})
        for it in e["items"]:
            per[it["bioguide"]]["amount"] += float(it.get("amount") or 0)
            per[it["bioguide"]]["count"] += 1
        e["rows"] = [{"bioguide": b, **v} for b, v in per.items()]

    # Link lobbying organizations to their PACs by name.
    by_norm = defaultdict(list)
    for c in committees.values():
        by_norm[norm_name(c["name"])].append(c)
    for r in registrants.values():
        for pac in r["pacs"]:
            for c in by_norm.get(norm_name(pac), []):
                r.setdefault("linked", []).append(c["key"])
                c.setdefault("linked", []).append(r["key"])

    out = {}
    for prefix, group in (("c", committees), ("r", registrants)):
        for e in group.values():
            e["rows"].sort(key=lambda r: -r["amount"])
            e["total"] = sum(r["amount"] for r in e["rows"])
            e["members"] = sum(1 for r in e["rows"] if r["amount"] > 0)
            e["split"] = _party_split(e["rows"], by_id)
            e["slug"] = f"{prefix}-{re.sub(r'[^A-Za-z0-9]+', '-', str(e['key'])).strip('-')[:80]}"
            e["chambers"] = {ch: sum(r["amount"] for r in e["rows"] if by_id.get(r["bioguide"], {}).get("chamber") == ch)
                             for ch in ("house", "senate")}
            out[e["slug"]] = e
    for e in out.values():
        e["linked_slugs"] = [f"{'r' if e['kind'] == 'committee' else 'c'}-{re.sub(r'[^A-Za-z0-9]+', '-', str(k)).strip('-')[:80]}"
                             for k in dict.fromkeys(e.get("linked", []))]
    return out
