"""Command line: `python -m speculum snapshot` then `python -m speculum build`."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive import RawArchive
from .http import Fetcher
from .snapshot import DAY_ZERO, Snapshot

log = logging.getLogger("speculum")
ROOT = Path(os.environ.get("SPECULUM_DATA", "data"))


def today_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def cmd_snapshot(args) -> int:
    from .sources import congress, fec, lda, legislators, senate

    run_date = args.date or today_et()
    keys = {"congress": os.environ.get("CONGRESS_API_KEY"), "fec": os.environ.get("FEC_API_KEY"),
            "lda": os.environ.get("LDA_API_KEY")}
    missing = [k for k in ("congress", "fec") if not keys[k]]
    if missing:
        log.error("missing API keys: %s (set CONGRESS_API_KEY and FEC_API_KEY)", ", ".join(missing))
        return 2

    archive = RawArchive(ROOT / "raw", run_date)
    fetch = Fetcher(archive, min_interval={
        "api.congress.gov": 0.8,      # 5,000 requests/hour per key
        "api.open.fec.gov": 3.7,      # 1,000 requests/hour per key
        "lda.gov": 1.0,
        "www.senate.gov": 0.5,
        "unitedstates.github.io": 0.2,
    })
    snap = Snapshot(ROOT / "snapshots", run_date)
    errors: dict[str, str] = {}

    members = legislators.fetch_members(fetch)
    snap.write("members.json", members)
    log.info("members: %d", len(members))
    try:
        from .sources import committees
        snap.write("committees.json", committees.fetch_committees(fetch))
    except Exception as exc:  # a control, not a source of record: never sink the night over it
        log.exception("committees failed")
        errors["committees"] = f"{type(exc).__name__}: {exc}"

    def step(name, fn):
        if args.only and name not in args.only:
            return None
        try:
            return fn()
        except Exception as exc:  # one failing source must not sink the night's snapshot
            log.exception("%s failed", name)
            errors[name] = f"{type(exc).__name__}: {exc}"
            return None

    # Time budget: GitHub stops a job after 6 hours. Finance is the slowest source (FEC allows
    # 1,000 requests an hour), so it runs last and stops early to leave time to commit.
    import time
    deadline = time.monotonic() + 60 * float(os.environ.get("SPECULUM_BUDGET_MINUTES", "290"))
    votes = step("votes", lambda: _votes(fetch, keys, members, snap, errors))
    lobbying = step("lobbying", lambda: lda.fetch_lobbying(fetch, keys["lda"], members, run_date))
    if lobbying is not None:
        snap.write("lobbying.json", lobbying)

    def _spend():
        from .sources import lobbyspend
        return lobbyspend.fetch_spend(fetch, keys["lda"], run_date, previous=_latest(snap, "spend.json"),
                                      deadline=deadline - 150 * 60)   # leave advocacy and finance time
    spend = step("spend", _spend)
    if spend is not None:
        snap.write("spend.json", spend)
    def _advocacy():
        from .sources import advocacy
        from .chains import load_json
        cfg = load_json(ROOT / "advocacy.json", {})
        return advocacy.fetch_advocacy(fetch, keys["fec"], cfg, run_date, previous=_latest(snap, "advocacy.json"),
                                       deadline=deadline - 60 * 60)   # leave finance at least an hour
    adv = step("advocacy", _advocacy)
    if adv is not None:
        snap.write("advocacy.json", adv)
    finance = step("finance", lambda: fec.fetch_finance(fetch, keys["fec"], members, run_date,
                                                         previous=_latest(snap, "finance.json"),
                                                         top_n=args.top_committees, deadline=deadline))
    partial = None
    if finance is not None:
        partial = finance.pop("_partial", None)
        snap.write("finance.json", finance)

    votes = votes or snap.read("votes.json.gz", {"votes": []})
    finance = finance or snap.read("finance.json", {})
    lobbying = lobbying or snap.read("lobbying.json", {"by_member": {}})
    coverage = sorted(f["coverage_end"] for f in finance.values() if f.get("coverage_end"))
    vote_dates = sorted(v["date"] for v in votes.get("votes", []) if v.get("date"))
    files = sum(1 for _ in archive.manifest.open()) if archive.manifest.exists() else 0
    meta = {
        "date": run_date,
        "day_zero": DAY_ZERO,
        "note": "Day Zero baseline" if run_date == DAY_ZERO else "",
        "counts": {
            "members": len(members),
            "house": sum(1 for m in members if m["chamber"] == "house"),
            "senate": sum(1 for m in members if m["chamber"] == "senate"),
            "votes": len(votes.get("votes", [])),
            "finance": len(finance),
            "lobbying_items": sum(len(v) for v in lobbying.get("by_member", {}).values()),
            "lobbying_unmatched": len(lobbying.get("unmatched_honorees", [])),
        },
        "fec_through": coverage[-1] if coverage else None,
        "votes_through": vote_dates[-1][:10] if vote_dates else None,
        "raw_files": files,
        "calls": fetch.calls,
        "errors": errors,
        "finance_partial": partial,
    }
    if args.only:
        # A partial rerun fills in sources for a night already saved: keep what that night recorded
        # about the sources this run did not touch.
        prior = snap.read("meta.json", {}) or {}
        skipped = [k for k in ("votes", "finance", "lobbying", "spend", "advocacy") if k not in args.only]
        meta["errors"] = {**{k: v for k, v in (prior.get("errors") or {}).items()
                             if any(k == s or k.startswith(s + "_") for s in skipped)}, **errors}
        if "finance" not in args.only:
            meta["finance_partial"] = prior.get("finance_partial")
        meta["raw_files"] += prior.get("raw_files") or 0
        meta["calls"] += prior.get("calls") or 0
        meta["note"] = prior.get("note", meta["note"])
    snap.write("meta.json", meta)
    try:
        from . import tide
        from .snapshot import all_dates
        earlier = [d for d in all_dates(ROOT / "snapshots") if d < run_date]
        snap.write("tide.json", tide.build_edition(snap, Snapshot(ROOT / "snapshots", earlier[-1]) if earlier else None))
    except Exception:
        log.exception("tide edition failed")
    log.info("snapshot %s done: %s", run_date, json.dumps(meta["counts"]))
    if errors:
        log.warning("sources with errors: %s", ", ".join(errors))
    return 0


def _votes(fetch, keys, members, snap, errors):
    from .sources import congress, senate

    previous = _latest(snap, "votes.json.gz")
    empty = {"votes": [], "positions": {}}

    def chamber(name, fn):
        # Each chamber stands alone: a refusal from one site must not throw away the other's votes.
        try:
            return fn()
        except Exception as exc:
            log.exception("%s votes failed", name)
            errors[f"votes_{name}"] = f"{type(exc).__name__}: {exc}"
            keep = [v for v in (previous or {}).get("votes", []) if v.get("chamber") == name]
            return {"votes": keep, "positions": {v["id"]: previous["positions"].get(v["id"], {}) for v in keep}} if keep else empty

    house = chamber("house", lambda: congress.fetch_house_votes(fetch, keys["congress"], previous))
    sen = chamber("senate", lambda: senate.fetch_senate_votes(fetch, members, previous))
    merged = {"votes": house["votes"] + sen["votes"], "positions": {**house["positions"], **sen["positions"]},
              "people": sen.get("people") or (previous or {}).get("people", {})}
    # A back-dated run (e.g. stamping Day Zero after the fact) keeps only roll calls held by that date.
    if snap.date < today_et():
        keep = [v for v in merged["votes"] if (v.get("date") or "")[:10] <= snap.date]
        ids = {v["id"] for v in keep}
        merged = {"votes": keep, "positions": {k: p for k, p in merged["positions"].items() if k in ids}, "people": merged.get("people", {})}
    snap.write("votes.json.gz", merged)
    return merged


def _latest(snap: Snapshot, name: str):
    """This run's file if a rerun already wrote it, else the most recent earlier snapshot's.
    Lets each night fetch only what changed."""
    current = snap.read(name)
    if current:
        return current
    root = snap.dir.parent
    for d in sorted((p.name for p in root.iterdir() if p.is_dir() and p.name < snap.date), reverse=True):
        prior = Snapshot(root, d).read(name)
        if prior:
            return prior
    return None


def cmd_build(args) -> int:
    from .site.build import build

    result = build(ROOT / "snapshots", Path(args.out))
    log.info("built site: %s", result)
    return 0


def cmd_chains(args) -> int:
    from . import chains, interpret
    from .snapshot import latest

    snap = latest(ROOT / "snapshots")
    if snap is None:
        log.error("no snapshot yet")
        return 1
    from .sources.lda import clean
    from .sources.fec import clean_finance
    from .sources.senate import clean_votes
    out = chains.for_site(snap.read("members.json", []), clean_finance(snap.read("finance.json", {}), snap.read("members.json", [])), clean(snap.read("lobbying.json", {}), snap.read("members.json", [])),
                          clean_votes(snap.read("votes.json.gz", {"votes": [], "positions": {}})), ROOT)
    log.info("chains: %d (%d with reviewed research)", len(out), sum(1 for c in out if c["research_by"] == "research"))
    if args.check:
        log.info("drafter check: %s", interpret.check())
        return 0
    if args.draft:
        done = interpret.draft_missing(out, ROOT / "chains" / "drafts", limit=args.limit)
        log.info("drafted: %s", ", ".join(done) or "none")
    return 0


def cmd_approve(args) -> int:
    """Record a person's approval of one chain, tied to the research as it stands now."""
    import json
    from . import chains
    from .snapshot import latest
    from .sources.lda import clean
    from .sources.fec import clean_finance
    from .sources.senate import clean_votes

    snap = latest(ROOT / "snapshots")
    if snap is None:
        log.error("no snapshot yet")
        return 1
    members = snap.read("members.json", [])
    out = chains.for_site(members, clean_finance(snap.read("finance.json", {}), members), clean(snap.read("lobbying.json", {}), members),
                          clean_votes(snap.read("votes.json.gz", {"votes": [], "positions": {}})), ROOT)
    want = args.chain.lower().replace(".", "").replace(" ", "")
    hits = [c for c in out if c["id"] == want or c["id"].endswith("-" + want)]
    if len(hits) != 1:
        log.error("no single chain matches %r; chains: %s", args.chain, ", ".join(c["id"] for c in out))
        return 1
    ch = hits[0]
    path = ROOT / "chains" / "reviews.json"
    reviews = chains.load_json(path, {})
    entry = {k: v for k, v in (reviews.get(ch["id"]) or {}).items() if k not in ("approved_by", "date", "note", "research")}
    if not args.revoke:
        entry.update({"approved_by": args.by, "date": today_et(), "research": ch["fingerprint"]})
        if args.note:
            entry["note"] = args.note
    reviews[ch["id"]] = entry
    path.write_text(json.dumps(reviews, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("%s %s", "revoked" if args.revoke else f"approved by {args.by}:", ch["id"])
    return 0


def cmd_verify(args) -> int:
    """Read readers' disputes, then check new, changed and disputed claims against their sources."""
    from . import verify
    if not args.no_disputes:
        verify.ingest_disputes(ROOT)
    verify.run(ROOT, limit=args.limit)
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="speculum")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="fetch every source and write today's snapshot")
    s.add_argument("--date", help="override the snapshot date (YYYY-MM-DD)")
    s.add_argument("--only", nargs="*", choices=["votes", "finance", "lobbying", "spend", "advocacy"])
    s.add_argument("--top-committees", type=int, default=50, help="committee givers kept per member (pages show the top 10)")
    b = sub.add_parser("build", help="render the site from the latest snapshot")
    b.add_argument("--out", default="dist")
    c = sub.add_parser("chains", help="build consequence chains; with --draft, ask the AI drafter for missing research")
    c.add_argument("--draft", action="store_true")
    c.add_argument("--check", action="store_true", help="one small live request to prove the API key works")
    c.add_argument("--limit", type=int, default=3, help="most chains to draft in one run")
    a = sub.add_parser("approve", help="record a person's approval of one chain's current research")
    a.add_argument("chain", help="chain id or bill, e.g. sjres32")
    a.add_argument("--by", default="the editor", help="name shown on the page")
    a.add_argument("--note", help="optional note shown with the approval")
    a.add_argument("--revoke", action="store_true", help="withdraw the approval")
    v = sub.add_parser("verify", help="check new, changed and disputed claims against their sources")
    v.add_argument("--limit", type=int, default=40, help="most claims to check in one run")
    v.add_argument("--no-disputes", action="store_true", help="skip reading new disputes from Cloudflare")
    args = p.parse_args(argv)
    return {"snapshot": cmd_snapshot, "build": cmd_build, "chains": cmd_chains, "approve": cmd_approve,
            "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
