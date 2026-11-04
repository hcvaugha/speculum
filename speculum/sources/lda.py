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


NICK = {  # common short forms, both directions; the member's own official name tokens are always accepted
    "bob": "robert", "rob": "robert", "bobby": "robert", "bill": "william", "will": "william", "billy": "william",
    "mike": "michael", "jim": "james", "jimmy": "james", "joe": "joseph", "tom": "thomas", "tommy": "thomas",
    "dave": "david", "nick": "nicholas", "rick": "richard", "dick": "richard", "rich": "richard", "chuck": "charles",
    "charlie": "charles", "ted": "edward", "ed": "edward", "steve": "steven", "chris": "christopher", "dan": "daniel",
    "danny": "daniel", "tim": "timothy", "greg": "gregory", "jeff": "jeffrey", "ron": "ronald", "don": "donald",
    "andy": "andrew", "drew": "andrew", "ben": "benjamin", "pat": "patrick", "tony": "anthony", "lou": "luis",
    "jack": "john", "johnny": "john", "josh": "joshua", "matt": "matthew", "sam": "samuel", "jen": "jennifer",
    "liz": "elizabeth", "beth": "elizabeth", "kathy": "katherine", "kate": "katherine", "debbie": "deborah",
    "deb": "deborah", "cathy": "catherine", "sue": "susan", "abe": "abraham", "zach": "zachary", "fred": "frederick",
    "jerry": "gerald", "nate": "nathaniel", "alex": "alexander", "ken": "kenneth", "larry": "lawrence", "jon": "jonathan",
    "jake": "jacob", "tammy": "tamara", "val": "valerie", "angie": "angela", "mitch": "addison", "buddy": "earl",
    "gus": "augustus", "pete": "peter", "rand": "randal", "doug": "douglas", "jodey": "jodey", "brad": "bradley",
    "vince": "vincent", "randy": "randall", "lizzie": "elizabeth", "ami": "amerish", "nellie": "nelida",
    "jake": "jacob", "chuy": "jesus", "tj": "thomas", "frank": "francis", "hal": "harold", "harry": "harold", "les": "lester", "al": "albert",
}
FILL = set("friends of for committee pac victory fund team re elect reelect and congress senate house campaign "
           "leadership people inc llc political action america american majority joint fundraising new state party "
           "district democratic republican caucus office from to in on at by with".split())


def _acceptable(m: dict) -> set[str]:
    toks = {_norm(t) for t in re.split(r"[\s.]+", m.get("name") or "") if t}
    toks |= {_norm(m.get("first") or ""), _norm(m.get("nickname") or "")}
    toks -= {_norm(m.get("last") or "")}
    for t in list(toks):
        if t in NICK:
            toks.add(NICK[t])
        toks |= {k for k, v in NICK.items() if v == t}
    return {t for t in toks if t}


def _first_score(lead: list[str], m: dict) -> int:
    """How well the names before the surname fit this member: 2 per name that agrees (exact, a
    nickname, or a shared start), 1 per agreeing initial. 0 when names are given and none agree;
    1 when no first name is given at all."""
    names = [w for w in lead if w not in FILL]
    if not names:
        return 1
    ok = _acceptable(m)
    score = 0
    for w in names:
        if len(w) <= 2:
            score += 1 if any(t.startswith(w[0]) for t in ok) else 0
        elif (w in ok or any(t.startswith(w) or w.startswith(t) for t in ok if len(t) >= 3)
              or (len(w) >= 4 and any(len(t) >= 4 and t[:4] == w[:4] for t in ok))):
            score += 2
    full = [w for w in names if len(w) > 2]
    if full and not any(w in ok or any(t.startswith(w) or w.startswith(t) for t in ok if len(t) >= 3)
                        or (len(w) >= 4 and any(len(t) >= 4 and t[:4] == w[:4] for t in ok)) for w in full):
        return 0                                   # a full first name was given and it is not this member's
    return score


class HonoreeMatcher:
    """Matches the free-text honoree on an LD-203 item to one sitting member, or to no one.
    Conservative by design: a wrong match puts money on the wrong person's page, which is worse
    than leaving it unmatched."""

    def __init__(self, members: list[dict]):
        self.by_last: dict[str, list[dict]] = defaultdict(list)
        for m in members:
            last = _norm(m.get("last") or m["name"].split()[-1])
            self.by_last[last].append(m)
            for part in last.split():           # compound surnames, e.g. "Van Hollen", "Lujan"
                if part != last and len(part) > 3:
                    self.by_last[part].append(m)

    def match(self, honoree: str) -> dict | None:
        raw = honoree or ""
        # Lists of several people (cabinet lists, boards, staff rosters): match each piece; keep only
        # when exactly one sitting member is named.
        pieces = [p for p in re.split(r";|/|\n|\band\b|&", raw) if p.strip()]
        if len(pieces) > 1 or len(re.findall(r"\b(sen|senator|rep|representative)\b\.?", raw.lower())) > 1:
            if len(re.findall(r",", raw)) >= 2 and len(pieces) == 1:
                pieces = [p for p in raw.split(",") if p.strip()]
            found = {m["bioguide"]: m for m in (self._one(p) for p in pieces) if m}
            return next(iter(found.values())) if len(found) == 1 else None
        return self._one(raw)

    def _one(self, honoree: str) -> dict | None:
        chamber = _chamber_hint(honoree)
        # Drop party/state tags: "(R)", "(D-CA)", "R-TX", "[D]".
        text = re.sub(r"[\(\[]\s*[RDI](\s*-\s*[A-Z]{2}(-?\d+)?)?\s*[\)\]]", " ", honoree)
        text = re.sub(r"\b[RDI]-[A-Z]{2}(-?\d+)?\b", " ", text).strip()
        lc = re.match(r"^\s*([^,]+?),\s*([A-Za-z.\s]+?)\s*$", text)        # "Last, First" -> "First Last"
        if lc and not re.fullmatch(r"(jr|sr|ii|iii|iv)\.?", lc.group(2).strip().lower()):
            text = f"{lc.group(2)} {lc.group(1)}"
        words = [w for w in TITLES.sub(" ", _norm(text)).split() if len(w) > 1 or True]
        words = [w for w in words if w]
        if not words:
            return None
        candidates: dict[str, dict] = {}
        lead: list[str] = []
        for i in range(len(words)):             # try "van hollen", then "hollen", etc.
            key = " ".join(words[i:])
            for m in self.by_last.get(key, []):
                candidates[m["bioguide"]] = m
            if candidates:
                lead = words[:i]
                break
        pool = list(candidates.values())
        if chamber:
            pool = [m for m in pool if m["chamber"] == chamber]   # a named senator is never a representative
        scored = [(m, _first_score(lead, m)) for m in pool]
        scored = [(m, sc) for m, sc in scored if sc > 0]
        if not scored:
            return None
        best = max(sc for _, sc in scored)
        top = [m for m, sc in scored if sc == best]
        return top[0] if len(top) == 1 else None


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
    return clean({
        "years": years,
        "years_label": f"{years[0]}–{years[-1]}" if len(years) > 1 else str(years[0]),
        "reports": len(reports),
        "by_member": dict(by_member),
        "registrants": registrants,
        "unmatched_honorees": sorted(({"honoree": h, "amount": a} for h, a in unmatched.items()), key=lambda x: -x["amount"])[:500],
    }, members)


def _who(it: dict) -> str:
    c = (it.get("contributor") or "").strip()
    if c.lower() in ("self", "", "same", "myself"):
        return re.sub(r"\s*\(.*$", "", it.get("reported_by") or "").lower()   # the lobbyist, or the filing firm
    return re.sub(r"[^a-z0-9]", "", c.lower())[:30]


MATCHER_VERSION = 2


def clean(lobbying: dict, members: list[dict] | None = None) -> dict:
    """Count each contribution once, and keep campaign money apart from money spent in a member's honor.

    LD-203 is filed by each registrant and by each of its lobbyists. A firm's PAC contribution is often
    listed on the firm's report and again on every lobbyist's report, so the same check appears several
    times. A contribution is kept once per distinct filing it appears on, at the most times any single
    filing lists it (two identical lines on one filing are usually a primary and a general-election
    check, and both count).

    Honorary and meeting expenses (FECA type aside) are payments to charities, events or institutions
    in a member's honor, not money to the member's campaign; they move to `honors` and out of totals.
    With `members`, every item's honoree is matched again with the current matcher, so a snapshot
    saved with an older matcher is corrected when read: items move to the right member or, when the
    match no longer holds, drop out (counted in `_cleaned`).
    Idempotent: a cleaned record is returned unchanged.
    """
    if (lobbying.get("_cleaned") or {}).get("matcher") == MATCHER_VERSION or (lobbying.get("_cleaned") and members is None):
        return lobbying
    source = {b: list(v) for b, v in (lobbying.get("by_member") or {}).items()}
    for b, v in (lobbying.get("honors") or {}).items():      # a re-clean keeps honors already split out
        source.setdefault(b, []).extend(v)
    rematch = {"moved": 0, "dropped": 0, "dropped_amount": 0.0}
    if members is not None:
        matcher, regrouped = HonoreeMatcher(members), {}
        for bio, items in source.items():
            for it in items:
                m = matcher.match(it.get("honoree") or "")
                if not m:
                    rematch["dropped"] += 1
                    rematch["dropped_amount"] += float(it.get("amount") or 0)
                    continue
                if m["bioguide"] != bio:
                    rematch["moved"] += 1
                regrouped.setdefault(m["bioguide"], []).append(it)
        source = regrouped
    by_member, honors = {}, {}
    removed, removed_amt = 0, 0.0
    for bio, items in source.items():
        per_filing: dict[tuple, dict] = {}
        first: dict[tuple, list] = {}
        for it in items:
            k = (it.get("date"), round(float(it.get("amount") or 0), 2),
                 re.sub(r"[^a-z0-9]", "", (it.get("payee") or "").lower())[:30], _who(it), it.get("type"))
            f = per_filing.setdefault(k, {})
            f[it.get("filing_uuid")] = f.get(it.get("filing_uuid"), 0) + 1
            first.setdefault(k, []).append(it)
        for k, f in per_filing.items():
            keep = max(f.values())
            group = first[k]
            removed += len(group) - keep
            removed_amt += (len(group) - keep) * k[1]
            for it in group[:keep]:
                (by_member if (it.get("type") or "").upper() in ("FECA", "") else honors).setdefault(bio, []).append(it)
    return {**lobbying, "by_member": by_member, "honors": honors,
            "_cleaned": {"duplicates_removed": removed, "duplicate_amount": round(removed_amt, 2),
                         "matcher": MATCHER_VERSION if members is not None else None,
                         "rematch": {k: round(v, 2) for k, v in rematch.items()} if members is not None else None}}
