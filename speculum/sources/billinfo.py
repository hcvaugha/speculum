"""Bill titles and basic facts from the Congress.gov API, for the bills the lobbying pages show.

Senate roll calls carry a bill's title; House roll calls and lobbying reports do not. Each night this fetches
the title, introduction date, sponsor, policy area and latest action for the bills that need them: the most
lobbied bills and every voted bill without a title. A bill already fetched is kept; its latest action is
refreshed when it is more than a week old.
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("speculum.billinfo")
BASE = "https://api.congress.gov/v3"
TYPES = {"HR": "hr", "S": "s", "HRES": "hres", "SRES": "sres", "HJRES": "hjres", "SJRES": "sjres",
         "HCONRES": "hconres", "SCONRES": "sconres"}
REFRESH_DAYS = 7


def split(key: str) -> tuple[str, int] | None:
    import re
    m = re.match(r"^([A-Z]+)(\d+)$", key or "")
    return (m.group(1), int(m.group(2))) if m and m.group(1) in TYPES else None


def fetch_bill_info(fetch, key: str, wanted: list[tuple[int, str]], run_date: str, previous: dict | None = None,
                    limit: int = 600, deadline=None) -> dict:
    import time
    prev = (previous or {}).get("bills", {})
    out = dict(prev)
    today = date.fromisoformat(run_date)
    n = 0
    for congress, bill in wanted:
        k = f"{congress}-{bill}"
        old = prev.get(k)
        if old and (today - date.fromisoformat(old.get("fetched", "2000-01-01"))).days < REFRESH_DAYS:
            continue
        if n >= limit or (deadline and time.monotonic() > deadline):
            break
        t = split(bill)
        if not t:
            continue
        try:
            d = fetch.get_json("congress", f"bill_{congress}_{TYPES[t[0]]}_{t[1]}.json",
                               f"{BASE}/bill/{congress}/{TYPES[t[0]]}/{t[1]}", params={"api_key": key, "format": "json"})
        except Exception as e:                      # a missing bill (a typo in a filing) must not stop the run
            log.info("bill %s: %s", k, e)
            out[k] = {**(old or {}), "missing": True, "fetched": run_date}
            n += 1
            continue
        b = d.get("bill") or {}
        sp = (b.get("sponsors") or [{}])[0]
        la = b.get("latestAction") or {}
        out[k] = {"title": b.get("title"), "introduced": b.get("introducedDate"),
                  "sponsor": sp.get("fullName"), "sponsor_bioguide": sp.get("bioguideId"),
                  "policy_area": (b.get("policyArea") or {}).get("name"),
                  "latest_action": {"date": la.get("actionDate"), "text": la.get("text")},
                  "url": f"https://www.congress.gov/bill/{congress}th-congress/{_slug(t[0])}/{t[1]}",
                  "fetched": run_date}
        n += 1
    log.info("bill info: %d fetched, %d known", n, len(out))
    return {"bills": out}


def _slug(t: str) -> str:
    return {"HR": "house-bill", "S": "senate-bill", "HRES": "house-resolution", "SRES": "senate-resolution",
            "HJRES": "house-joint-resolution", "SJRES": "senate-joint-resolution",
            "HCONRES": "house-concurrent-resolution", "SCONRES": "senate-concurrent-resolution"}[t]
