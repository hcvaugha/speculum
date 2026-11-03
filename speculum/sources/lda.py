"""Lobbyist contributions (LD-203) from the lda.gov API, matched to members by honoree name.

LD-203 reports list contributions that registered lobbyists and lobbying organizations (and
their PACs) made to, or in honor of, federal officeholders. The honoree is free text such as
"Sen. Ed Markey", so matching is by name: last name, then first name or nickname, then the
chamber title. Anything ambiguous is left unmatched and listed, never guessed.

Amended reports supersede the original for the same filer and period, so only the most
recently posted version of each report is counted.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date

BASE = "https://lda.gov/api/v1"
COUNTED_TYPES = {"feca", "he", "me"}   # campaign contributions, honorary and meeting expenses
TITLES = re.compile(r"\b(sen|senator|rep|representative|congressman|congresswoman|congressmember|hon|honorable|"
                    r"the|leader|speaker|whip|chairman|chairwoman|chair|ranking|member|dr|mr|mrs|ms|jr|sr|ii|iii|iv)\b\.?")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]+", " ", s.lower()).strip()


def _chamber_hint(raw: str) -> str | None:
    r = raw.lower()
    if re.search(r"\b(sen|senator)\b", r):
        return "senate"
    if re.search(r"\b(rep|representative|congressman|congresswoman|congressmember)\b", r):
        return "house"
    return None


class HonoreeMatcher:
    def __init__(self, members: list[dict]):
        self.by_last: dict[str, list[dict]] = defaultdict(list)
        for m in members:
            last = _norm(m.get("last") or m["name"].split()[-1])
            self.by_last[last].append(m)
            for part in last.split():           # compound surnames, e.g. "Van Hollen", "Lujan"
                if part != last and len(part) > 3:
                    self.by_last[part].append(m)

    def match(self, honoree: str) -> dict | None:
        chamber = _chamber_hint(honoree)
        words = [w for w in TITLES.sub(" ", _norm(honoree)).split() if len(w) > 1]
        if not words:
            return None
        candidates: dict[str, dict] = {}
        for i in range(len(words)):             # try "van hollen", then "hollen", etc.
            key = " ".join(words[i:])
            for m in self.by_last.get(key, []):
                candidates[m["bioguide"]] = m
            if candidates:
                break
        pool = list(candidates.values())
        if chamber:
            pool = [m for m in pool if m["chamber"] == chamber] or pool
        if len(pool) > 1:
            firsts = set(words[:-1])
            narrowed = [m for m in pool if _norm(m.get("first") or "") in firsts or _norm(m.get("nickname") or "") in firsts]
            if not narrowed:
                initials = {w[0] for w in firsts}
                narrowed = [m for m in pool if (_norm(m.get("first") or "x")[:1] in initials)]
            pool = narrowed
        return pool[0] if len(pool) == 1 else None


def _years(today: date) -> list[int]:
    start = today.year - 1 if today.year % 2 == 0 else today.year   # the current Congress's years
    return list(range(start, today.year + 1))


def _fetch_reports(fetch, key: str | None, year: int) -> list[dict]:
    headers = {"Authorization": f"Token {key}"} if key else None
    reports, page = [], 1
    while True:
        data = fetch.get_json("lda", f"contributions_{year}_p{page:04d}.json", f"{BASE}/contributions/",
                              params={"filing_year": year, "page": page, "page_size": 25}, headers=headers)
        reports.extend(data.get("results", []))
        if not data.get("next"):
            return reports
        page += 1


def _latest_versions(reports: list[dict]) -> list[dict]:
    """Keep the most recently posted report per filer and period (amendments replace originals)."""
    best: dict[tuple, dict] = {}
    for r in reports:
        filer = (r.get("registrant") or {}).get("id"), (r.get("lobbyist") or {}).get("id")
        k = (*filer, r.get("filing_year"), r.get("filing_period"))
        if k not in best or (r.get("dt_posted") or "") > (best[k].get("dt_posted") or ""):
            best[k] = r
    return list(best.values())


def fetch_lobbying(fetch, key: str | None, members: list[dict], run_date: str) -> dict:
    today = date.fromisoformat(run_date)
    if not key:
        fetch.min_interval["lda.gov"] = 4.2            # anonymous limit: 15 requests per minute
    else:
        fetch.min_interval["lda.gov"] = 0.55           # keyed limit: 120 requests per minute
    years = _years(today)
    reports = []
    for y in years:
        reports.extend(_fetch_reports(fetch, key, y))
    return match_reports(_latest_versions(reports), members, years)


def match_reports(reports: list[dict], members: list[dict], years: list[int]) -> dict:
    matcher = HonoreeMatcher(members)
    by_member: dict[str, list[dict]] = defaultdict(list)
    registrants: dict[str, dict] = {}
    unmatched: dict[str, float] = defaultdict(float)
    for r in reports:
        reg = r.get("registrant") or {}
        filer = reg.get("name") or ""
        reg_id = str(reg.get("id")) if reg.get("id") is not None else None
        if reg_id:
            entry = registrants.setdefault(reg_id, {"id": reg_id, "name": filer.title(), "description": reg.get("description"),
                                                    "pacs": []})
            for pac in r.get("pacs") or []:
                if pac and pac not in entry["pacs"]:
                    entry["pacs"].append(pac)
        lob = r.get("lobbyist") or {}
        # Filings leave fields blank more often than the schema suggests; never let one sink the night.
        lob_name = " ".join(str(x).strip().title() for x in (lob.get("first_name"), lob.get("last_name")) if x and str(x).strip())
        reported_by = f"{lob_name} ({filer.title()})" if lob_name else (filer.title() or "Unnamed filer")
        for it in r.get("contribution_items") or []:
            if it.get("contribution_type") not in COUNTED_TYPES:
                continue
            honoree = it.get("honoree_name") or ""
            m = matcher.match(honoree)
            try:
                amount = float(it.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if not m:
                if _chamber_hint(honoree):
                    unmatched[honoree] += amount
                continue
            by_member[m["bioguide"]].append({
                "date": it.get("date"), "amount": amount, "type": it.get("contribution_type_display"),
                "contributor": (it.get("contributor_name") or "").title(), "payee": (it.get("payee_name") or "").title(),
                "honoree": honoree, "reported_by": reported_by, "registrant_id": reg_id, "filing_uuid": r.get("filing_uuid"),
                "url": r.get("filing_document_url"), "period": r.get("filing_period_display"),
            })
    return {
        "years": years,
        "years_label": f"{years[0]}–{years[-1]}" if len(years) > 1 else str(years[0]),
        "reports": len(reports),
        "by_member": dict(by_member),
        "registrants": registrants,
        "unmatched_honorees": sorted(({"honoree": h, "amount": a} for h, a in unmatched.items()), key=lambda x: -x["amount"])[:500],
    }
