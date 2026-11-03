"""Render the public holding page and the private preview site from the latest snapshot.

dist/public/    speculum.vote          (holding page only)
dist/preview/   preview.speculum.vote  (Chamber, member Ledgers, Day Zero, Method; behind login)
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import ledger as ledger_mod
from ..snapshot import DAY_ZERO, Snapshot, all_dates, latest

HERE = Path(__file__).parent
PARTY_NAMES = {"D": "Democrat", "R": "Republican", "I": "Independent", "L": "Libertarian", None: "Independent"}


def money(x) -> str:
    x = float(x or 0)
    if abs(x) >= 1e6:
        return f"${x / 1e6:,.1f}M"
    if abs(x) >= 1e4:
        return f"${x / 1e3:,.0f}K"
    return f"${x:,.0f}"


def pct(x) -> str:
    return f"{100 * float(x):.0f}%"


def day_number(d: str) -> int:
    return (date.fromisoformat(d) - date.fromisoformat(DAY_ZERO)).days + 1


def env() -> Environment:
    e = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
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
    finance = snap.read("finance.json", {})
    votes = snap.read("votes.json.gz", {"votes": [], "positions": {}})
    lobbying = snap.read("lobbying.json", {"by_member": {}})
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
        (prev / "member" / f"{bio}.html").write_text(tmpl.render(
            root="../", page="member", m=mm, L=ledgers[bio], recent=recent,
            parts=ledger_mod.FUNDING_PARTS, lobby_years=lobby_years, party_names=PARTY_NAMES, **common), encoding="utf-8")

    history = []
    for d in all_dates(snapshots_root):
        smeta = Snapshot(snapshots_root, d).read("meta.json", {})
        history.append({"date": d, "day": day_number(d), "files": smeta.get("raw_files", "—"),
                        "calls": smeta.get("calls", "—"), "note": smeta.get("note", "")})
    history.reverse()
    (prev / "day-zero.html").write_text(E.get_template("dayzero.html").render(
        root="", page="dayzero", meta=_meta_defaults(meta), history=history, **common), encoding="utf-8")
    (prev / "method.html").write_text(E.get_template("method.html").render(root="", page="method", **common), encoding="utf-8")
    (prev / "ledgers.json").write_text(json.dumps({"date": snap.date, "ledgers": ledgers}, default=str), encoding="utf-8")
    return {"members": len(members), "votes": len(vlist)}


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
