"""Render the public holding page and the private preview site from the latest snapshot.

dist/public/    speculum.vote          (holding page only)
dist/preview/   preview.speculum.vote  (Chamber, member Ledgers, Day Zero, Method; behind login)
"""
from __future__ import annotations

import json
import re

from ..names import org_name
import shutil
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import ledger as ledger_mod
from .. import tide as tide_mod
from .. import interests as interests_mod
from .. import chains as chains_mod
from ..snapshot import DAY_ZERO, Snapshot, all_dates, latest, read_newest

HERE = Path(__file__).parent
PARTY_NAMES = {"D": "Democrat", "R": "Republican", "I": "Independent", "L": "Libertarian", None: "Independent"}


def money(x) -> str:
    x = float(x or 0)
    if x < 0:
        return "−" + money(-x)
    if abs(x) >= 1e9:
        return f"${x / 1e9:,.2f}B"
    if abs(x) >= 1e6:
        return f"${x / 1e6:,.1f}M"
    if abs(x) >= 1e4:
        return f"${x / 1e3:,.0f}K"
    return f"${x:,.0f}"


def pct(x) -> str:
    return f"{100 * float(x):.0f}%"


def day_number(d: str) -> int:
    return (date.fromisoformat(d) - date.fromisoformat(DAY_ZERO)).days + 1


def longdate(iso: str) -> str:
    """2026-11-06 -> Friday, November 6, 2026."""
    try:
        d = date.fromisoformat((iso or "")[:10])
    except ValueError:
        return iso or ""
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def shortdate(iso: str) -> str:
    """2026-04-15 -> Apr 15, 2026."""
    try:
        d = date.fromisoformat((iso or "")[:10])
    except ValueError:
        return iso or ""
    return f"{d:%b} {d.day}, {d.year}"


from ..findings import billname  # noqa: E402  (SJRES54 -> S.J.Res. 54, HR1234 -> H.R. 1234)


def env() -> Environment:
    e = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
    e.filters["billname"] = billname
    e.filters["longdate"] = longdate
    e.filters["billnorm"] = lambda b: re.sub(r"[^A-Z0-9]", "", (b or "").upper())
    e.globals["bill_slugs"] = set()
    e.filters["shortdate"] = shortdate
    e.filters["money"] = money
    e.filters["pct"] = pct
    e.globals["css_v"] = __import__("hashlib").sha256((HERE / "static" / "style.css").read_bytes()).hexdigest()[:10]
    e.filters["org"] = org_name
    e.filters["num"] = lambda x: f"{x:,}" if isinstance(x, (int, float)) and not isinstance(x, bool) else ("—" if x is None else x)
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
    (pub / "signups.html").write_text(E.get_template("signups.html").render(), encoding="utf-8")
    (pub / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (prev / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    if snap is None:
        (prev / "index.html").write_text("<p>No snapshot yet.</p>", encoding="utf-8")
        return {"members": 0}

    mode = (chains_mod.load_json(snapshots_root.parent / "site.json", {}) or {}).get("public", "holding")
    result = _render_site(E, prev, snapshots_root, snap, public=False)
    if mode == "launch":
        for f in pub.iterdir():
            if f.is_file():
                f.unlink()
        (pub / "member").mkdir(exist_ok=True)
        _render_site(E, pub, snapshots_root, snap, public=True)
        (pub / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
        # The owner's list of readers' requests stays at speculum.vote/signups after launch.
        (pub / "signups.html").write_text(E.get_template("signups.html").render(), encoding="utf-8")
    result["public"] = mode
    return result


def _render_site(E, prev: Path, snapshots_root: Path, snap, public: bool) -> dict:
    """The full site into one directory. public=True is speculum.vote after launch: no preview
    banner, indexable, and nothing a reviewer has not approved (no Dependents, no unapproved
    Tide stories)."""
    members = snap.read("members.json", [])
    from ..sources.fec import clean_finance
    finance = clean_finance(read_newest(snapshots_root, "finance.json", snap.date, {}), members)
    from ..sources.senate import clean_votes
    votes = clean_votes(read_newest(snapshots_root, "votes.json.gz", snap.date, {"votes": [], "positions": {}}))
    from ..sources.congress import page_url
    for v in votes.get("votes", []):
        v["url"] = page_url(v.get("url"))          # link readers to the vote's page, not the data file
    from ..sources.lda import clean
    lobbying = clean(read_newest(snapshots_root, "lobbying.json", snap.date, {"by_member": {}}), members)
    meta = snap.read("meta.json", {})
    ledgers = ledger_mod.build(members, finance, votes, lobbying.get("by_member", {}))

    site_cfg = chains_mod.load_json(snapshots_root.parent / "site.json", {}) or {}
    common = {"snapshot_date": snap.date, "day_number": day_number(snap.date), "public": public,
              "show_dependents": (not public) or bool(site_cfg.get("dependents_public", True)),
              "launched": site_cfg.get("public") == "launch"}
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
    from .. import money as money_mod
    spend = read_newest(snapshots_root, "spend.json", snap.date)
    mb = money_mod.build(members, finance, lobbying, spend, set_aside=_set_aside(snapshots_root))
    bd = _build_bills(E, prev, spend, votes, members, lobbying, read_newest(snapshots_root, "billinfo.json", snap.date), common)
    (prev / "chamber.html").write_text(E.get_template("chamber.html").render(
        root="", page="chamber", rows=rows, members=members, mb=mb,
        fec_through=max((f.get("coverage_end") or "" for f in finance.values()), default=None) or meta.get("fec_through"),
        votes_through=meta.get("votes_through"), **common), encoding="utf-8")

    # Index each member's recent votes, newest first.
    vlist = sorted(votes.get("votes", []), key=lambda v: (v.get("date") or "", v["id"]), reverse=True)
    positions = votes.get("positions", {})
    by_id = {m["bioguide"]: m for m in members}
    majority = _party_majorities(vlist, positions, by_id)
    tmpl = E.get_template("member.html")
    from .. import lameduck as _ld
    leaving = {e["bioguide"]: e for e in _ld.match(chains_mod.load_json(snapshots_root.parent / "lameduck.json", {"members": []}).get("members", []), members) if e["bioguide"]}
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
            root="../", page="member", m=mm, L=ledgers[bio], recent=recent, leaving=leaving.get(bio),
            leaving_label=_ld.REASONS.get((leaving.get(bio) or {}).get("reason"), ""),
            honors=sorted(lobbying.get("honors", {}).get(bio, []), key=lambda c: -float(c.get("amount") or 0)),
            parts=ledger_mod.FUNDING_PARTS, lobby_years=lobby_years, party_names=PARTY_NAMES, **common), encoding="utf-8")

    history = []
    for d in all_dates(snapshots_root):
        smeta = Snapshot(snapshots_root, d).read("meta.json", {})
        history.append({"date": d, "day": day_number(d), "files": smeta.get("raw_files", "—"),
                        "calls": smeta.get("calls", "—"), "note": smeta.get("note", "")})
    history.reverse()
    (prev / "day-zero.html").write_text(E.get_template("dayzero.html").render(
        root="", page="dayzero", meta=_meta_defaults(Snapshot(snapshots_root, DAY_ZERO).read("meta.json", meta) if DAY_ZERO in all_dates(snapshots_root) else meta), history=history, **common), encoding="utf-8")
    timeline = _build_tide(E, snapshots_root, prev, members, common, public)
    _build_interests(E, prev, members, finance, lobbying, common)
    chains = None
    if common["show_dependents"]:
        chains = _build_chains(E, snapshots_root.parent, prev, members, finance, lobbying, votes, common, public)
    money_data = _build_money(E, snapshots_root, snap.date, prev, members, finance, lobbying, chains, common)
    from .. import findings as findings_mod
    fd = findings_mod.build(members, votes, money_data, chains, bd)
    (prev / "findings.html").write_text(E.get_template("findings.html").render(root="", page="findings", f=fd, **common), encoding="utf-8")
    # The front page: what Speculum is, a few of the night's findings, where to go, and the latest stories.
    (prev / "index.html").write_text(E.get_template("home.html").render(
        root="", page="home", f=fd, timeline=timeline or [], n_members=len(members), n_votes=len(votes.get("votes", [])),
        n_bills=len(bd["bills"]), n_chains=sum(1 for c in chains or [] if c.get("research_by")), mb=mb, **common), encoding="utf-8")
    _build_pages(E, snapshots_root.parent, prev, common)
    from .. import lameduck as lameduck_mod
    ld = chains_mod.load_json(snapshots_root.parent / "lameduck.json", {"members": []})
    ld_rows = lameduck_mod.build(members, votes, ld.get("members", []))
    (prev / "lameduck.html").write_text(E.get_template("lameduck.html").render(
        root="", page="lameduck", rows=ld_rows, as_of=(ld.get("as_of") or "")[:10] or "—",
        min_after=lameduck_mod.MIN_AFTER, **common), encoding="utf-8")
    from .. import money as money_mod
    (prev / "method.html").write_text(E.get_template("method.html").render(root="", page="method", money_areas=money_mod.AREAS, set_aside=_set_aside(snapshots_root),
        sector_rules=__import__("speculum.lobbysectors", fromlist=["RULES"]).RULES,
        sector_names=__import__("speculum.lobbysectors", fromlist=["NAMES"]).NAMES,
        sector_groups=__import__("speculum.lobbysectors", fromlist=["GROUPS"]).GROUPS,
        bill_min_orgs=__import__("speculum.bills", fromlist=["MIN_ORGS_FOR_PAGE"]).MIN_ORGS_FOR_PAGE, **common), encoding="utf-8")
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


def public_view(c: dict) -> dict:
    """A chain as the public site shows it: only the items that passed the source check, nothing held."""
    v = dict(c)
    v["outcomes"] = [o for o in c.get("outcomes") or [] if o.get("_public")]
    v["context"] = [x for x in c.get("context") or [] if x.get("_public")]
    if not c.get("advocacy_public"):
        v["advocacy_pattern"], v["advocacy_withheld"] = None, True
    return v


def _build_chains(E, data_root: Path, prev: Path, members, finance, lobbying, votes, common, public: bool = False) -> list:
    from ..snapshot import latest as _latest
    snap = _latest(data_root / "snapshots")
    chains = chains_mod.for_site(members, finance, lobbying, votes, data_root,
                                 committees=read_newest(data_root / "snapshots", "committees.json", snap.date) if snap else None,
                                 advocacy_data=read_newest(data_root / "snapshots", "advocacy.json", snap.date) if snap else None,
                                 spend=read_newest(data_root / "snapshots", "spend.json", snap.date) if snap else None,
                                 bill_info=read_newest(data_root / "snapshots", "billinfo.json", snap.date) if snap else None)
    if public:   # the public site shows what has passed the source check (see chains.apply_checks)
        chains = [public_view(c) for c in chains if c["status"] == "approved" or c.get("auto_public")]
    (prev / "chain").mkdir(exist_ok=True)
    (prev / "dependents.html").write_text(E.get_template("dependents.html").render(
        root="", page="dependents", chains=chains, grades=chains_mod.GRADES,
        groups=[(r["name"], g) for r in chains_mod.RULES for g in [[c for c in chains if c["rule"] == r["id"]]] if g],
        approved=sum(1 for c in chains if c["status"] == "approved"), **common), encoding="utf-8")
    for c in chains:
        tmpl = E.get_template(c.get("template") or "chain.html")
        (prev / "chain" / f"{c['id']}.html").write_text(tmpl.render(
            root="../", page="dependents", c=c, party_names=PARTY_NAMES, min_n=chains_mod.MIN_N, gap=int(chains_mod.GAP),
            lobby_years=lobbying.get("years_label", ""), **common), encoding="utf-8")
    return chains


def _set_aside(snapshots_root: Path) -> list[dict]:
    p = Path(snapshots_root).parent / "lobbying_set_aside.json"
    return json.loads(p.read_text())["set_aside"] if p.exists() else []


GROUP_COLORS = {"Business": "#c9a24a", "Labor unions": "#9fc3e6", "Advocacy and nonprofits": "#8fb39a",
                "Education and research": "#a58fc4", "Governments": "#6f8fae", "Not described": "#4a4540"}


def _build_bills(E, prev: Path, spend, votes, members, lobbying, info, common) -> dict:
    """Who lobbies each bill (see speculum/bills.py)."""
    from .. import bills as bills_mod
    bd = bills_mod.build(spend, votes, members, lobbying, info)
    E.globals["bill_slugs"] = set(bd["by_slug"])
    (prev / "bill").mkdir(exist_ok=True)
    (prev / "bills.html").write_text(E.get_template("bills.html").render(
        root="", page="bills", b=bd, group_colors=GROUP_COLORS, slugs_json=json.dumps(sorted(bd["by_slug"])), **common), encoding="utf-8")
    tmpl = E.get_template("bill.html")
    for x in bd["bills"]:
        (prev / "bill" / f"{x['slug']}.html").write_text(tmpl.render(root="../", page="bills", x=x, group_colors=GROUP_COLORS, **common), encoding="utf-8")
    return bd


def _build_money(E, snapshots_root: Path, date: str, prev: Path, members, finance, lobbying, chains, common) -> dict:
    """Where the money goes, and Shared funders (see speculum/money.py)."""
    from .. import money as money_mod
    spend = read_newest(snapshots_root, "spend.json", date)
    data = money_mod.build(members, finance, lobbying, spend, chains, set_aside=_set_aside(snapshots_root))
    have = {p.stem for p in (prev / "interest").glob("*.html")}
    for o in data["orgs"]:
        o["name"] = org_name(o["name"] or "")
        o["page"] = _interest_slug("r", o["id"]) if o["id"] and _interest_slug("r", o["id"]) in have else None
    for a in data["areas"]:
        for c in a["top_clients"]:
            c["name"] = org_name(c["name"] or "")
    data["has_spend"] = bool(spend)
    (prev / "money").mkdir(exist_ok=True)
    ctx = {"d": data, "area_names": money_mod.AREA_NAMES, "party_names": PARTY_NAMES}
    (prev / "money.html").write_text(E.get_template("money.html").render(root="", page="money", **ctx, **common), encoding="utf-8")
    (prev / "funders.html").write_text(E.get_template("funders.html").render(root="", page="funders", **ctx, **common), encoding="utf-8")
    tmpl = E.get_template("money_area.html")
    for a in data["areas"]:
        orgs = [o for o in data["orgs"] if o["area"] == a["key"]][:40]
        (prev / "money" / f"{a['key']}.html").write_text(tmpl.render(root="../", page="money", a=a, orgs=orgs, **ctx, **common), encoding="utf-8")
    return data


def _build_tide(E, snapshots_root: Path, prev: Path, members: list[dict], common: dict, public: bool = False) -> list:
    """One edition per snapshot: the stored tide.json if the nightly run wrote one, else built now."""
    dates = all_dates(snapshots_root)
    approvals = {k: v for k, v in chains_mod.load_json(snapshots_root.parent / "tide" / "approvals.json", {}).items()
                 if not k.startswith("_")}
    names = {m["bioguide"]: f"{m['name']} ({m['party']}-{m['state']})" for m in members}
    (prev / "tide").mkdir(exist_ok=True)
    tmpl = E.get_template("tide.html")
    glass = _glass_by_edition(chains_mod.load_json(snapshots_root.parent / "tide" / "glass.json", {}), dates)
    eds = []
    for i, d in enumerate(dates):
        cur = Snapshot(snapshots_root, d)
        ed = cur.read("tide.json") or tide_mod.build_edition(cur, Snapshot(snapshots_root, dates[i - 1]) if i else None)
        from ..sources.congress import page_url
        for s in ed.get("stories") or []:
            for src in s.get("sources") or []:
                src["url"] = page_url(src.get("url"))
        eds.append((d, cur, _apply_approvals(ed, approvals.get(d, {}), public)))
    # The record so far: every edition, newest first, with its headlines, for the timeline on each page.
    timeline = [{"date": d, "day": day_number(d), "founding": d == DAY_ZERO, "roll_calls": ed.get("roll_calls"),
                 "glass": bool(glass.get(d)),
                 "stories": [{"id": s["id"], "kind": s.get("kind"), "headline": s.get("headline"), "date": (s.get("date") or "")[:10],
                              "status": s.get("status")} for s in ed.get("stories") or []]}
                for d, cur, ed in reversed(eds)]
    for d, cur, ed in eds:
        ctx = dict(page="tide", ed=ed, ed_day=day_number(d), archive=list(reversed(dates)), names=names, timeline=timeline,
                   latest=dates[-1], day_zero=DAY_ZERO, fs=_founding_stats(cur) if d == DAY_ZERO else None, glass=glass.get(d), **common)
        (prev / "tide" / f"{d}.html").write_text(tmpl.render(root="../", **ctx), encoding="utf-8")
        if d == dates[-1]:
            (prev / "tide.html").write_text(tmpl.render(root="", **ctx), encoding="utf-8")
    return timeline


def _glass_by_edition(cfg: dict, dates: list[str]) -> dict:
    """Across the Glass: each weekly entry goes in the first edition dated on or after its date and
    before the next entry's date, so a missed night moves it to the next edition rather than losing it."""
    entries = sorted(cfg.get("entries", []), key=lambda e: e["date"])
    out = {}
    for i, e in enumerate(entries):
        until = entries[i + 1]["date"] if i + 1 < len(entries) else "9999"
        hit = next((d for d in sorted(dates) if e["date"] <= d < until), None)
        if hit:
            out[hit] = e
    return out


def _apply_approvals(ed: dict, marks: dict, public: bool) -> dict:
    """Stories publish automatically: they are built from the night's filings by fixed templates, the
    same way for everyone. data/tide/approvals.json ({date: {story id: "held"}}) can hold one back,
    for instance while a correction is checked; the preview still shows it, marked."""
    stories = []
    for st in ed.get("stories", []):
        status = "held" if marks.get(st.get("id")) == "held" else "published"
        if public and status == "held":
            continue
        stories.append({**st, "status": status})
    return {**ed, "stories": stories}


def _build_pages(E, data_root: Path, prev: Path, common: dict) -> None:
    corrections = chains_mod.load_json(data_root / "corrections.json", {"entries": []})
    entries = sorted(corrections.get("entries", []), key=lambda e: e.get("date", ""), reverse=True)
    (prev / "about.html").write_text(E.get_template("about.html").render(root="", page="about", **common), encoding="utf-8")
    (prev / "join.html").write_text(E.get_template("join.html").render(root="", page="join", **common), encoding="utf-8")
    (prev / "corrections.html").write_text(E.get_template("corrections.html").render(
        root="", page="corrections", entries=entries, contact=corrections.get("contact"), **common), encoding="utf-8")


def _founding_stats(snap: Snapshot) -> dict:
    """The figures on No. 1's front page, read from the Day Zero snapshot itself, cleaned the same
    way as every other page."""
    from ..sources.lda import clean
    members = snap.read("members.json", [])
    lob = clean(snap.read("lobbying.json", {"by_member": {}}), members)
    meta = snap.read("meta.json", {})
    items = [i for v in lob.get("by_member", {}).values() for i in v]
    votes = snap.read("votes.json.gz", {"votes": []}).get("votes", [])
    from ..sources.fec import clean_finance
    interests = interests_mod.build(members, clean_finance(snap.read("finance.json", {}), members), lob)
    return {"members": len(members), "house": sum(1 for m in members if m["chamber"] == "house"),
            "senate": sum(1 for m in members if m["chamber"] == "senate"), "votes": len(votes),
            "votes_through": meta.get("votes_through") or "—", "lobby_items": len(items),
            "lobby_total": sum(float(i.get("amount") or 0) for i in items), "lobby_years": lob.get("years_label", ""),
            "interests": len(interests), "raw_files": meta.get("raw_files") or 0}


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
