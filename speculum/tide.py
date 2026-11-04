"""The Tide: a daily edition of short, neutral stories drawn from what changed in the record.

Each edition compares one snapshot with the one before it. With no earlier snapshot (Day Zero),
it covers the roll calls of the preceding 30 days. Every story is built from fixed templates so
the same event reads the same way whoever it involves, and every story links to its sources.

Stories are drafts until a person approves them: the blueprint requires an editor's sign-off
before anything is published outside the private preview.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from . import ledger

PARTY_WORD = {"D": "Democrat", "R": "Republican", "I": "Independent"}
WINDOW_DAYS = 30
MAX_STORIES = 14


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _money(x: float) -> str:
    if x < 0:
        return "−" + _money(-x)
    if abs(x) >= 1e6:
        return f"${x / 1e6:,.1f} million"
    return f"${x:,.0f}"


def _vote_label(v: dict) -> str:
    """What the vote was about, for a headline: the Senate's own title, else the bill, else the question."""
    if v.get("chamber") == "senate" and v.get("title"):
        return v["title"]
    if v.get("bill"):
        return v["bill"]
    return (v.get("question") or "a roll call").strip().lower().removeprefix("on ")


def _tally(v: dict, pos: dict, by_id: dict):
    t = defaultdict(lambda: {"Y": 0, "N": 0})
    total = {"Y": 0, "N": 0}
    for bio, p in pos.items():
        s = ledger.side(p)
        m = by_id.get(bio)
        if not s:
            continue
        total[s] += 1
        if m:
            t[ledger.caucus_party(m)][s] += 1
    maj = {k: ("Y" if c["Y"] > c["N"] else "N") for k, c in t.items() if c["Y"] != c["N"]}
    return t, total, maj


def _vote_stories(votes: list[dict], positions: dict, by_id: dict) -> list[dict]:
    stories = []
    for v in votes:
        pos = positions.get(v["id"], {})
        if not pos:
            continue
        t, total, maj = _tally(v, pos, by_id)
        margin = abs(total["Y"] - total["N"])
        label = _vote_label(v)
        chamber = "Senate" if v["chamber"] == "senate" else "House"
        split = "D" in maj and "R" in maj and maj["D"] != maj["R"]
        breakers = []
        if split:
            for bio, p in pos.items():
                m = by_id.get(bio)
                s = ledger.side(p)
                party = ledger.caucus_party(m) if m else None
                if m and s and party in maj and s != maj[party]:
                    breakers.append(m)
        sources = [{"label": f"{chamber} roll call {v.get('number', '')}".strip(), "url": v.get("url")}]
        if v.get("bill_url"):
            sources.append({"label": v.get("bill") or "Bill", "url": v["bill_url"]})
        maj_words = "; ".join(f"most {PARTY_WORD[k]}s voted {'yes' if maj[k] == 'Y' else 'no'}"
                              for k in ("D", "R") if k in maj)
        facts = [f"Question: {(v.get('question') or 'Roll call').strip()}.",
                 f"Result: {v.get('result') or 'recorded'}, {total['Y']} to {total['N']}."]
        if maj_words:
            facts.append(maj_words[0].upper() + maj_words[1:] + ".")
        if breakers:
            by_party = defaultdict(list)
            for m in sorted(breakers, key=lambda m: m["sort_name"]):
                by_party[ledger.caucus_party(m)].append(m)
            counts = ", ".join(_plural(len(ms), PARTY_WORD.get(p, p)) for p, ms in sorted(by_party.items()))
            stories.append({
                "kind": "Broke ranks", "date": v.get("date"), "vote_id": v["id"],
                "headline": f"{_plural(len(breakers), 'member')} voted against their party on {label}",
                "dek": f"In the {chamber}, {counts} voted against most of their own party on a vote that split the parties.",
                "facts": facts, "members": [m["bioguide"] for m in breakers],
                "member_names": [f"{m['name']} ({m['party']}-{m['state']})" for m in sorted(breakers, key=lambda m: m['sort_name'])],
                "sources": sources, "score": 10 + 2 * len(breakers) + (8 if margin <= 5 else 0),
            })
        elif margin <= 5 and total["Y"] + total["N"] > 0:
            stories.append({
                "kind": "Close vote", "date": v.get("date"), "vote_id": v["id"],
                "headline": f"{label}: decided by {_plural(margin, 'vote')}",
                "dek": f"The {chamber} margin was {_plural(margin, 'vote')}.",
                "facts": facts, "members": [], "member_names": [], "sources": sources, "score": 9 - margin,
            })
    return stories


def _lobbying_story(cur: dict, prev: dict | None, by_id: dict) -> dict | None:
    old = set()
    if prev:
        for items in prev.get("by_member", {}).values():
            old.update(c.get("filing_uuid") for c in items)
    fresh = defaultdict(float)
    count = 0
    for bio, items in cur.get("by_member", {}).items():
        for c in items:
            if prev is None or c.get("filing_uuid") not in old:
                fresh[bio] += float(c.get("amount") or 0)
                count += 1
    if prev is None or not fresh:
        return None
    top = sorted(((b, a) for b, a in fresh.items() if a > 0 and b in by_id), key=lambda x: -x[1])[:10]
    if not top:
        return None
    total = sum(fresh.values())
    return {
        "kind": "Lobbyist money", "date": None,
        "headline": f"New lobbyist contribution reports list {_money(total)} to {_plural(len(fresh), 'member')}",
        "dek": "Registered lobbyists and their organizations file twice-yearly reports of contributions to federal officeholders.",
        "facts": [f"{by_id[b]['name']} ({by_id[b]['party']}-{by_id[b]['state']}): {_money(a)}" for b, a in top]
                 + [f"{_plural(count, 'contribution')} in newly posted reports."],
        "members": [b for b, _ in top], "member_names": [],
        "sources": [{"label": "LD-203 reports, lda.gov", "url": "https://lda.gov/system/public/"}], "score": 12,
    }


def _finance_story(cur: dict, prev: dict | None, by_id: dict) -> dict | None:
    if prev is None:
        return None
    moved = []
    for bio, f in cur.items():
        p = prev.get(bio)
        if not p or f.get("coverage_end") == p.get("coverage_end") or bio not in by_id:
            continue
        moved.append((bio, f.get("receipts", 0) - p.get("receipts", 0), f.get("coverage_end")))
    if not moved:
        return None
    moved.sort(key=lambda x: -x[1])
    return {
        "kind": "Campaign filings", "date": None,
        "headline": f"{_plural(len(moved), 'member')} filed new campaign finance reports",
        "dek": "Largest increases in total receipts since the previous edition.",
        "facts": [f"{by_id[b]['name']} ({by_id[b]['party']}-{by_id[b]['state']}): +{_money(d)}, through {end}" for b, d, end in moved[:8] if d > 0],
        "members": [b for b, _, _ in moved[:8]], "member_names": [],
        "sources": [{"label": "FEC candidate totals", "url": "https://www.fec.gov/data/"}], "score": 8,
    }


def build_edition(cur, prev=None) -> dict:
    """cur and prev are Snapshot objects (prev may be None)."""
    members = cur.read("members.json", [])
    by_id = {m["bioguide"]: m for m in members}
    votes = cur.read("votes.json.gz", {"votes": [], "positions": {}})
    pvotes = prev.read("votes.json.gz", {"votes": []}) if prev else None
    if pvotes is not None:
        seen = {v["id"] for v in pvotes.get("votes", [])}
        window = [v for v in votes.get("votes", []) if v["id"] not in seen]
        since = prev.date
    else:
        start = (date.fromisoformat(cur.date) - timedelta(days=WINDOW_DAYS)).isoformat()
        window = [v for v in votes.get("votes", []) if start <= (v.get("date") or "")[:10] <= cur.date]
        since = start
    stories = _vote_stories(window, votes.get("positions", {}), by_id)
    from .sources.lda import clean
    for s in (_lobbying_story(clean(cur.read("lobbying.json", {}), cur.read("members.json", [])),
                               clean(prev.read("lobbying.json", {}), prev.read("members.json", [])) if prev else None, by_id),
              _finance_story(cur.read("finance.json", {}), prev.read("finance.json") if prev else None, by_id)):
        if s:
            stories.append(s)
    stories.sort(key=lambda s: (-s["score"], s.get("date") or ""))
    for i, s in enumerate(stories[:MAX_STORIES]):
        s["id"] = f"{cur.date}-{i + 1:02d}"
        s["status"] = "draft"
    return {
        "date": cur.date, "since": since, "first": prev is None,
        "roll_calls": len(window), "stories": stories[:MAX_STORIES], "more": max(0, len(stories) - MAX_STORIES),
    }
