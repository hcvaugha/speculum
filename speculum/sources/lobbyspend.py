"""Lobbying spending by issue area, from quarterly LD-2 reports (lda.gov).

Every registrant files an LD-2 each quarter for each client: what it was paid (a lobbying firm reports
"income") or what it spent (an organization lobbying for itself reports "expenses"), and the general
issue areas it lobbied on. This module totals those reports per quarter.

Counting rules (published on the Method page):
- Amendments replace the report they amend: the latest posted report per registrant, client and quarter.
- No double counting: an organization's own report includes what it paid outside lobbying firms (LDA
  guidance), so when a client files for itself, the firms' reports for that client are not added.
- A report lists issue areas but not dollars per area, so its amount is split equally across the areas
  it lists.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

log = logging.getLogger("speculum.lobbyspend")

BASE = "https://lda.gov/api/v1"
PERIODS = {1: "first_quarter", 2: "second_quarter", 3: "third_quarter", 4: "fourth_quarter"}
FIRST_YEAR = 2025                    # the record covers the 119th Congress
REFRESH_DAYS = 120                   # quarters this recent are fetched again (late filings, amendments)


def _deadline(year: int, q: int) -> date:
    """LD-2 reports are due 20 days after each quarter ends."""
    return date(year + 1, 1, 20) if q == 4 else date(year, 3 * q + 1, 20)


def due_quarters(today: date) -> list[tuple[int, int]]:
    out = []
    for y in range(FIRST_YEAR, today.year + 1):
        for q in range(1, 5):
            if _deadline(y, q) <= today:
                out.append((y, q))
    return out


def _amount(x) -> float:
    try:
        return float(x) if x not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _fetch_quarter(fetch, key: str | None, year: int, q: int, deadline=None) -> tuple[list[dict], bool]:
    headers = {"Authorization": f"Token {key}"} if key else None
    rows, page = [], 1
    import time
    while True:
        if deadline and time.monotonic() > deadline:
            log.warning("lobbying spend %d Q%d: stopped at page %d for time", year, q, page)
            return rows, False
        data = fetch.get_json("lda", f"filings_{year}_q{q}_p{page:04d}.json", f"{BASE}/filings/",
                              params={"filing_year": year, "filing_period": PERIODS[q], "page": page, "page_size": 25},
                              headers=headers)
        rows.extend(data.get("results", []))
        if not data.get("next"):
            return rows, True
        page += 1


def aggregate(filings: list[dict]) -> dict:
    """One quarter's totals. Pure function of the filings, so it can be tested without the network."""
    latest: dict[tuple, dict] = {}
    for f in filings:
        reg, cli = f.get("registrant") or {}, f.get("client") or {}
        if not reg.get("id") or not cli.get("id"):
            continue
        k = (reg["id"], cli["id"])
        if k not in latest or (f.get("dt_posted") or "") > (latest[k].get("dt_posted") or ""):
            latest[k] = f
    reports = list(latest.values())
    self_filed = {(f.get("client") or {}).get("id") for f in reports
                  if (f.get("client") or {}).get("client_self_select") and _amount(f.get("expenses")) > 0}

    issues: dict[str, dict] = {}
    clients: dict[str, dict] = {}
    registrants: dict[str, dict] = {}
    total, counted = 0.0, 0
    for f in reports:
        reg, cli = f["registrant"], f["client"]
        own = bool(cli.get("client_self_select"))
        amt = _amount(f.get("expenses")) if own else _amount(f.get("income"))
        if not own and cli["id"] in self_filed:
            amt_counted = 0.0            # already inside the client's own report
        else:
            amt_counted = amt
        codes = sorted({a.get("general_issue_code") for a in f.get("lobbying_activities") or [] if a.get("general_issue_code")})
        for a in f.get("lobbying_activities") or []:
            c = a.get("general_issue_code")
            if c and c not in issues:
                issues[c] = {"name": a.get("general_issue_code_display") or c, "amount": 0.0, "reports": 0}
        share = amt_counted / len(codes) if codes else 0.0
        for c in codes:
            issues[c]["amount"] += share
            issues[c]["reports"] += 1
        total += amt_counted
        counted += 1 if amt_counted else 0
        ck = str(cli["id"])
        e = clients.setdefault(ck, {"name": cli.get("name"), "amount": 0.0, "issues": defaultdict(float), "self": own})
        e["amount"] += amt_counted
        for c in codes:
            e["issues"][c] += share
        # A registrant's issue mix, by the money it reported (or its report count when it reported none):
        # used to place its political contributions (LD-203) in an issue area.
        r = registrants.setdefault(str(reg["id"]), {"name": reg.get("name"), "issues": defaultdict(float),
                                                    "own": 0, "for_clients": 0})
        r["own" if own else "for_clients"] += 1
        w = amt if amt else 1.0
        for c in codes:
            r["issues"][c] += w / len(codes)
    top = sorted(clients.items(), key=lambda kv: -kv[1]["amount"])[:400]
    return {
        "total": round(total, 2), "reports": len(reports), "reports_with_amount": counted,
        "issues": {c: {**v, "amount": round(v["amount"], 2)} for c, v in issues.items()},
        "top_clients": [{"id": k, "name": v["name"], "amount": round(v["amount"], 2), "self": v["self"],
                         "issues": {c: round(a, 2) for c, a in v["issues"].items()}} for k, v in top],
        "registrants": {k: {"name": v["name"], "own": v["own"], "for_clients": v["for_clients"],
                            "issues": {c: round(a, 2) for c, a in v["issues"].items()}} for k, v in registrants.items()},
    }


def fetch_spend(fetch, key: str | None, run_date: str, previous: dict | None = None, deadline=None) -> dict:
    """Quarters already fetched and older than REFRESH_DAYS are carried over from the last snapshot."""
    fetch.min_interval["lda.gov"] = 0.55 if key else 4.2
    today = date.fromisoformat(run_date)
    prev = (previous or {}).get("quarters", {})
    out = {}
    for y, q in due_quarters(today):
        label = f"{y}-Q{q}"
        old = prev.get(label)
        fresh = (today - _deadline(y, q)).days <= REFRESH_DAYS
        if old and old.get("complete") and not fresh:
            out[label] = old
            continue
        filings, complete = _fetch_quarter(fetch, key, y, q, deadline)
        if not complete and old and old.get("complete"):
            out[label] = old                       # a partial read never replaces a complete one
            continue
        agg = aggregate(filings)
        agg.update({"year": y, "quarter": q, "complete": complete, "fetched": run_date})
        out[label] = agg
        log.info("lobbying spend %s: %d reports, $%.0f%s", label, agg["reports"], agg["total"], "" if complete else " (partial)")
    return {"quarters": out, "source": f"{BASE}/filings/"}
