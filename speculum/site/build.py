"""Render the public holding page and the private preview site from the latest snapshot.

dist/public/    speculum.vote          (holding page only)
dist/preview/   preview.speculum.vote  (Chamber, member Ledgers, Day Zero, Method; behind login)
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import ledger as ledger_mod
from .. import tide as tide_mod
from .. import interests as interests_mod
from .. import chains as chains_mod
from ..snapshot import DAY_ZERO, Snapshot, all_dates, latest

HERE = Path(__file__).parent
PARTY_NAMES = {"D": "Democrat", "R": "Republican", "I": "Independent", "L": "Libertarian", None: "Independent"}


def money(x) -> str:
    x = float(x or 0)
    if x < 0:
        return "−" + money(-x)
    if abs(x) >= 1e6:
        return f"${x / 1e6:,.1f}M"
    if abs(x) >= 1e4:
        return f"${x / 1e3:,.0f}K"
    return f"${x:,.0f}"


def pct(x) -> str:
    return f"{100 * float(x):.0f}%"


def day_number(d: str) -> int:
    return (date.fromisoformat(d) - date.fromisoformat(DAY_ZERO)).days + 1


def billname(code: str) -> str:
    """SJRES54 -> S.J.Res. 54, HR1234 -> H.R. 1234."""
    m = re.match(r"^(HCONRES|SCONRES|HJRES|SJRES|HRES|SRES|HR|S)\s*(\d+)$", (code or "").upper().replace(".", "").replace(" ", ""))
    if not m:
        return code or ""
    names = {"HCONRES": "H.Con.Res.", "SCONRES": "S.Con.Res.", "HJRES": "H.J.Res.", "SJRES": "S.J.Res.",
             "HRES": "H.Res.", "SRES": "S.Res.", "HR": "H.R.", "S": "S."}
    return f"{names[m.group(1)]} {m.group(2)}"


def env() -> Environment:
    e = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
    e.filters["billname"] = billname
    e.filters["money"] = money
    e.filters["pct"] = pct
    return e


def seat(m: dict, long: bool = False) -> str:
    if m["chamber"] == "senate":
        return m["state"] if not long else f"{m['state']}, Senate"
    d = m.get("district")
    d = "AL" if d in (0, "0", None, "") else str(d)
    return f"{m['state']}-{d}" if not long else (f"{m['state']} at-large" if d == "AL" else f"{m['state']} district {d}")


def build(snapshots_root: Path, out: Path, raw_root: Path | None = None) -> dict:
    snap = latest(snapshots_root)
    E = env()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    pub, prev = out / "public", out / "preview"
    for d in (pub / "static", prev / "static", prev / "member"):
        d.mkdir(parents=True, exist_ok=True)
    for d in (pub, prev):
        shutil.copy(HERE / "static" / "style.css", d / "static" / "style.css")

    (pub / "index.html").write_text(E.get_template("holding.html").render(), encoding="utf-8")
    (pub / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (prev / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    if snap is None:
        (prev / "index.html").write_text("<p>No snapshot yet.</p>", encoding="utf-8")
        return {"members": 0}

    members = snap.read("members.json", [])
    from ..sources.fec import clean_finance
    finance = clean_finance(snap.read("finance.json", {}), members)
    from ..sources.senate import clean_votes
    votes = clean_votes(snap.read("votes.json.gz", {"votes": [], "positions": {}}))
    from ..sources.lda import clean
    lobbying = clean(snap.read("lobbying.json", {"by_member": {}}), snap.read("members.json", []))
    meta = snap.read("meta.json", {})
    ledgers = ledger_mod.build(members, finance, votes, lobbying.get("by_member", {}))

    common = {"snapshot_date": snap.date, "day_number": day_number(snap.date)}
    rows = []
    for m in members:
        L = ledgers[m["bioguide"]]
        rows.append({
            "bioguide": m["bioguide"], "name": m["name"], "sort_name": m.get("sort_name") or m["name"],
            "party": m["party"], "chamber": m["chamber"], "seat": seat(m),
            "raised": L["funding"]["total"] if L["funding"] else None,
            "small": L["funding"]["parts"]["small"] if L["funding"] else None,
            "pac": L["funding"]["parts"]["pac"] if L["funding"] else None,
            "lobby": L["lobbyist_total"], "unity": L["unity"], "attend": L["attendance"],
        })
    rows.sort(key=lambda r: r["sort_name"])
    (prev / "index.html").write_text(E.get_template("chamber.html").render(
        root="", page="chamber", rows=rows, members=members,
        fec_through=meta.get("fec_through"), votes_through=meta.get("votes_through"), **common), encoding="utf-8")

    # Index each member's recent votes, newest first.
    vlist = sorted(votes.get("votes", []), key=lambda v: (v.get("date") or "", v["id"]), reverse=True)
    positions = votes.get("positions", {})
    by_id = {m["bioguide"]: m for m in members}
    majority = _party_majorities(vlist, positions, by_id)
    tmpl = E.get_template("member.html")
    lobby_years = lobbying.get("years_label")
    for m in members:
        bio = m["bioguide"]
        recent = []
        for v in vlist:
            p = positions.get(v["id"], {}).get(bio)
            if p is None:
                continue
            s = ledger_mod.side(p)
            maj = majority.get(v["id"], {})
            party = ledger_mod.caucus_party(m)
            dissent = bool(maj.get("unity") and s and party in maj and s != maj[party])
            recent.append({**v, "position": p, "side": s, "dissent": dissent})
            if len(recent) >= 30:
                break
        mm = {**m, "party_name": PARTY_NAMES.get(m["party"], m["party"]), "seat_long": seat(m, long=True),
              "caucus": ledger_mod.caucus_party(m)}
        (prev / "member" / f"{bio}.html").write_text(tmpl.render(slugify=_interest_slug,
            root="../", page="member", m=mm, L=ledgers[bio], recent=recent,
            honors=sorted(lobbying.get("honors", {}).get(bio, []), key=lambda c: -float(c.get("amount") or 0)),
            parts=ledger_mod.FUNDING_PARTS, lobby_years=lobby_years, party_names=PARTY_NAMES, **common), encoding="utf-8")

    history = []
    for d in all_dates(snapshots_root):
        smeta = Snapshot(snapshots_root, d).read("meta.json", {})
        history.append({"date": d, "day": day_number(d), "files": smeta.get("raw_files", "—"),
                        "calls": smeta.get("calls", "—"), "note": smeta.get("note", "")})
    history.reverse()
    (prev / "day-zero.html").write_text(E.get_template("dayzero.html").render(
        root="", page="dayzero", meta=_meta_defaults(meta), history=history, **common), encoding="utf-8")
    _build_tide(E, snapshots_root, prev, members, common)
    _build_interests(E, prev, members, finance, lobbying, common)
    _build_chains(E, snapshots_root.parent, prev, members, finance, lobbying, votes, common)
    (prev / "method.html").write_text(E.get_template("method.html").render(root="", page="method", **common), encoding="utf-8")
    # The readings only: itemized contributions live on each member's page and in the raw archive
    # (with them, this file passed Cloudflare's 25 MB limit on the first full night).
    slim = {bio: {**{k: v for k, v in L.items() if k != "lobbyist_items"}, "lobbyist_count": len(L.get("lobbyist_items") or [])}
            for bio, L in ledgers.items()}
    (prev / "ledgers.json").write_text(json.dumps({"date": snap.date, "ledgers": slim}, default=str), encoding="utf-8")
    return {"members": len(members), "votes": len(vlist)}


def _build_interests(E, prev: Path, members, finance, lobbying, common) -> None:
    all_ = interests_mod.build(members, finance, lobbying)
    by_id = {m["bioguide"]: m for m in members}
    rows = sorted(all_.values(), key=lambda e: -e["total"])
    (prev / "interest").mkdir(exist_ok=True)
    (prev / "interests.html").write_text(E.get_template("interests.html").render(
        root="", page="interests", rows=rows, **common), encoding="utf-8")
    tmpl = E.get_template("interest.html")
    for slug, e in all_.items():
        (prev / "interest" / f"{slug}.html").write_text(tmpl.render(
            root="../", page="interests", e=e, all=all_, by_id=by_id, seat=seat, party_names=PARTY_NAMES, **common), encoding="utf-8")


def _build_chains(E, data_root: Path, prev: Path, members, finance, lobbying, votes, common) -> None:
    from ..snapshot import latest as _latest
    snap = _latest(data_root / "snapshots")
    chains = chains_mod.for_site(members, finance, lobbying, votes, data_root,
                                 committees=snap.read("committees.json", None) if snap else None,
                                 advocacy_data=snap.read("advocacy.json", None) if snap else None)
    (prev / "chain").mkdir(exist_ok=True)
    (prev / "dependents.html").write_text(E.get_template("dependents.html").render(
        root="", page="dependents", chains=chains, grades=chains_mod.GRADES,
        approved=sum(1 for c in chains if c["status"] == "approved"), **common), encoding="utf-8")
    tmpl = E.get_template("chain.html")
    for c in chains:
        (prev / "chain" / f"{c['id']}.html").write_text(tmpl.render(
            root="../", page="dependents", c=c, party_names=PARTY_NAMES,
            lobby_years=lobbying.get("years_label", ""), **common), encoding="utf-8")


def _build_tide(E, snapshots_root: Path, prev: Path, members: list[dict], common: dict) -> None:
    """One edition per snapshot: the stored tide.json if the nightly run wrote one, else built now."""
    dates = all_dates(snapshots_root)
    names = {m["bioguide"]: f"{m['name']} ({m['party']}-{m['state']})" for m in members}
    (prev / "tide").mkdir(exist_ok=True)
    tmpl = E.get_template("tide.html")
    for i, d in enumerate(dates):
        cur = Snapshot(snapshots_root, d)
        ed = cur.read("tide.json") or tide_mod.build_edition(cur, Snapshot(snapshots_root, dates[i - 1]) if i else None)
        ctx = dict(page="tide", ed=ed, ed_day=day_number(d), archive=list(reversed(dates)), names=names, **common)
        (prev / "tide" / f"{d}.html").write_text(tmpl.render(root="../", **ctx), encoding="utf-8")
        if d == dates[-1]:
            (prev / "tide.html").write_text(tmpl.render(root="", **ctx), encoding="utf-8")


def _interest_slug(prefix: str, key) -> str:
    import re
    return f"{prefix}-{re.sub(r'[^A-Za-z0-9]+', '-', str(key)).strip('-')[:80]}"


def _party_majorities(vlist, positions, by_id):
    out = {}
    for v in vlist:
        tally: dict[str, dict[str, int]] = {}
        for bio, p in positions.get(v["id"], {}).items():
            m = by_id.get(bio)
            s = ledger_mod.side(p)
            if m and s:
                t = tally.setdefault(ledger_mod.caucus_party(m), {"Y": 0, "N": 0})
                t[s] += 1
        maj = {k: ("Y" if t["Y"] > t["N"] else "N") for k, t in tally.items() if t["Y"] != t["N"]}
        maj["unity"] = "D" in maj and "R" in maj and maj["D"] != maj["R"]
        out[v["id"]] = maj
    return out


def _meta_defaults(meta: dict) -> dict:
    counts = {"members": 0, "house": 0, "senate": 0, "votes": 0, "finance": 0, "lobbying_items": 0, "lobbying_unmatched": 0}
    counts.update(meta.get("counts", {}))
    return {**meta, "counts": counts}
