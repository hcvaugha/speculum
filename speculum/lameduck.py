"""Lame-duck watch: members who will not return in January, measured against their own Day Zero.

Who counts comes from data/lameduck.json, a sourced list (retiring, running for another office,
lost a primary, lost the general election), reviewed like every other hand-kept list. The reading is
each member's own record before Day Zero and after it, on the same measures as the Ledger: party
unity and attendance. Comparing a member with themselves keeps the reading about change, not about
where they started.
"""
from __future__ import annotations

import re

from . import ledger
from .snapshot import DAY_ZERO

REASONS = {
    "retiring": "Retiring",
    "other_office": "Ran for another office",
    "lost_primary": "Lost primary",
    "lost_general": "Lost re-election",
    "resigning_early": "Leaving early",
    "appointed": "Appointed; seat on the ballot",
}
MIN_AFTER = 5     # party-splitting votes after Day Zero before a change is shown


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def match(entries: list[dict], members: list[dict]) -> list[dict]:
    """Attach each listed member to the roster: by bioguide, else by surname, state and chamber."""
    by_id = {m["bioguide"]: m for m in members}
    out = []
    for e in entries:
        m = by_id.get(e.get("bioguide") or "")
        if not m:
            words = [w for w in (e.get("name") or "").replace(",", " ").split()
                     if _norm(w) not in ("jr", "sr", "ii", "iii", "iv")]
            last = _norm(words[-1]) if words else ""
            cands = [x for x in members if x["state"] == e.get("state") and x["chamber"] == e.get("chamber")
                     and last and (_norm(x.get("last")) == last or last in _norm(x.get("last")))]
            if len(cands) > 1 and e.get("district") not in (None, ""):
                want = "0" if str(e["district"]).upper() in ("AL", "0") else str(e["district"])
                cands = [x for x in cands if str(x.get("district") or 0) == want]
            m = cands[0] if len(cands) == 1 else None
        out.append({**e, "bioguide": m["bioguide"] if m else None, "member": m})
    return out


def build(members: list[dict], votes: dict, entries: list[dict]) -> list[dict]:
    split = lambda keep: {"votes": [v for v in votes.get("votes", []) if keep((v.get("date") or "")[:10])],
                          "positions": votes.get("positions", {})}
    before = ledger.party_unity(members, split(lambda d: d <= DAY_ZERO))
    after = ledger.party_unity(members, split(lambda d: d > DAY_ZERO))
    rows = []
    for e in match(entries, members):
        if not e["bioguide"]:
            continue
        b, a = before.get(e["bioguide"], {}), after.get(e["bioguide"], {})
        shown = (a.get("unity_votes") or 0) >= MIN_AFTER
        rows.append({**e, "reason_label": REASONS.get(e.get("reason"), e.get("reason")),
                     "unity_before": b.get("unity"), "unity_votes_before": b.get("unity_votes", 0),
                     "attend_before": b.get("attendance"),
                     "unity_after": a.get("unity") if shown else None, "unity_votes_after": a.get("unity_votes", 0),
                     "attend_after": a.get("attendance") if (a.get("eligible") or 0) >= MIN_AFTER else None,
                     "change": (a["unity"] - b["unity"]) if shown and b.get("unity") is not None else None})
    rows.sort(key=lambda r: (r["member"]["chamber"], r["member"].get("sort_name") or r["member"]["name"]))
    return rows
