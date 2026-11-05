"""The source checker: the gate between AI research and the public site.

Every claim in the Dependents (an outcome, a context item, the record of a sale, an organization's stated
position) has a fingerprint of its content. Each night this module checks every claim that has no result
for its current fingerprint, and every claim a reader has disputed:

  1. it reads the claim's cited sources (fetching them itself, or letting the model fetch them),
  2. it asks Claude, as a strict fact-checker with no stake in the research, whether the sources say what
     the claim says and whether the evidence grade fits the definitions,
  3. for a dispute, it also reads the reader's source and decides whether the claim still stands.

Results go to data/chains/checks.json, keyed by fingerprint. Only "supported" claims are published, so a
failed fetch, an unclear answer or a missing key always leaves a claim off the site, never on it.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

import requests

from . import chains

log = logging.getLogger("speculum.verify")

API = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("SPECULUM_CHECK_MODEL", "claude-opus-5-5")
UA = "Mozilla/5.0 (compatible; SpeculumSourceCheck/1.0; +https://speculum.vote/method.html)"
MAX_SOURCE_CHARS = 60_000

GRADES = """documented: an official or authoritative investigative record ties weapons or actions of this kind from
  the recipient to specific harm (UN panels and commissions, US government reports, court findings of fact,
  forensic investigations that identified US-made munitions at strike sites)
estimated: authoritative aggregate figures (UN OCHA, UN agencies, IPC, peer-reviewed studies)
associated: credible reporting of a pattern without direct linkage; court orders on provisional measures and
  arrest warrants are associated, because they do not decide the facts
none_found: research found no record; the claim states an absence"""

SYSTEM = f"""You are the source checker for Speculum, a nonpartisan public record of the US Congress. You did not
write the research you are checking and you have no stake in it. Your only job is to decide whether the cited
sources say what the claim says. You are strict: a claim that adds a figure, a date, an attribution, a
causal link or a degree of certainty that the source does not state is not supported, even if it is
probably true. Quoted words must appear in the source. Paraphrase must not change meaning or strength.
Counterpoints and scope notes attached to a claim are part of it and must also be supported (by the cited
source or by a source named inside them). Do not use your own knowledge to fill gaps.

Evidence grades, for outcome claims:
{GRADES}

Answer with one JSON object and nothing else:
{{"result": "supported" | "partly_supported" | "not_supported" | "unreachable",
  "grade_ok": true | false | null,
  "note": "one or two sentences: what matches, or exactly what does not",
  "quotes": ["short verbatim passages from the source that bear on the claim"]}}
Use "unreachable" only when you could not read any cited source. Use null for grade_ok when the claim has no grade."""

DISPUTE = """A reader disputes this claim and gives a source. Read the reader's source as well. Decide whether, in
light of all sources, the claim as written still stands. Add to your JSON:
  "dispute_upheld": true if the claim still stands (the reader's source does not show it is wrong, overstated
  or missing a necessary caveat), false if it does not."""


def _text(url: str) -> str | None:
    """The readable text of a source, or None if it cannot be fetched here."""
    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": UA, "Accept": "text/html,application/pdf,*/*"})
        r.raise_for_status()
    except Exception as exc:
        log.info("fetch failed %s: %s", url, exc)
        return None
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            pages = PdfReader(io.BytesIO(r.content)).pages
            return "\n".join((p.extract_text() or "") for p in pages)[:MAX_SOURCE_CHARS]
        except Exception as exc:
            log.info("pdf read failed %s: %s", url, exc)
            return None
    html = r.text
    html = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html)
    txt = re.sub(r"(?s)<[^>]+>", " ", html)
    txt = re.sub(r"&nbsp;", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:MAX_SOURCE_CHARS] if len(txt) > 200 else None


def _ask(key: str, prompt: str, fetch_urls: list[str]) -> dict:
    """One check. Sources we could not read ourselves are left for the model to fetch."""
    body = {"model": MODEL, "max_tokens": 2000, "system": SYSTEM, "messages": [{"role": "user", "content": prompt}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    if fetch_urls:
        body["tools"] = [{"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": len(fetch_urls) + 2}]
        headers["anthropic-beta"] = "web-fetch-2025-09-10"
    r = requests.post(API, timeout=600, headers=headers, json=body)
    r.raise_for_status()
    out = r.json()
    text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("checker returned no JSON")
    res = json.loads(m.group(0))
    res["by"] = out.get("model", MODEL)
    return res


def check_claim(key: str, claim: dict, dispute: dict | None = None) -> dict:
    parts, unread = [], []
    srcs = [s for s in claim["sources"] if s.get("url")]
    for s in srcs:
        t = _text(s["url"])
        if t:
            parts.append(f"SOURCE: {s.get('title')} ({s.get('publisher')}, {s.get('date')})\nURL: {s['url']}\nTEXT:\n{t}")
        else:
            unread.append(s["url"])
    if dispute:
        t = _text(dispute["source"])
        if t:
            parts.append(f"READER'S SOURCE\nURL: {dispute['source']}\nTEXT:\n{t}")
        else:
            unread.append(dispute["source"])
    prompt = (f"Kind of claim: {claim['kind']}\n"
              f"Claim, as published (JSON):\n{json.dumps(claim['item'], ensure_ascii=False, indent=1)}\n\n"
              + ("\n\n".join(parts) if parts else "No source text could be read locally.")
              + (f"\n\nFetch and read these sources yourself with the web_fetch tool: {', '.join(unread)}" if unread else "")
              + (f"\n\n{DISPUTE}\nReader's note: {dispute.get('note') or '(none)'}" if dispute else ""))
    if not srcs:
        return {"result": "not_supported", "note": "No source is cited.", "quotes": [], "by": "rule"}
    return _ask(key, prompt, unread)


def _verdict(res: dict, claim: dict) -> str:
    """Only a clean pass publishes; a grade stronger than the evidence counts as a failure."""
    if res.get("result") == "supported" and (claim["grade"] is None or res.get("grade_ok") is not False):
        return "supported"
    if res.get("result") == "supported":
        return "grade_too_strong"
    return res.get("result") or "not_supported"


def run(data_root: Path, limit: int = 40, key: str | None = None) -> dict:
    key = key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.info("no ANTHROPIC_API_KEY; skipping the source check (unchecked items stay off the public site)")
        return {"checked": 0}
    cdir = data_root / "chains"
    research = chains.load_json(cdir / "research.json", {})
    adv = chains.load_json(data_root / "advocacy.json", {})
    drafts = [chains.load_json(p, {}) for p in sorted((cdir / "drafts").glob("*.json"))] if (cdir / "drafts").exists() else []
    claims = {c["fp"]: c for c in chains.all_claims(research, adv, drafts)}
    store = chains.load_json(cdir / "checks.json", {"checks": {}})
    checks = store.setdefault("checks", {})
    disputes = chains.load_json(cdir / "disputes.json", {"open": [], "closed": []})
    today = date.today().isoformat()

    queue = [(fp, None) for fp in claims if fp not in checks]
    for d in list(disputes.get("open", [])):
        if d.get("fp") in claims:
            queue.insert(0, (d["fp"], d))                 # disputes first: a disputed item is down until checked
        else:                                            # the item changed or was removed: the dispute lapses
            disputes["open"].remove(d)
            disputes.setdefault("closed", []).append({**d, "closed": today, "outcome": "item changed or removed before the check"})
    done = 0
    for fp, d in queue[:limit]:
        claim = claims[fp]
        try:
            res = check_claim(key, claim, d)
        except Exception as exc:
            log.warning("check failed for %s (%s): %s", fp, claim["label"], exc)
            continue
        entry = {"result": _verdict(res, claim), "on": today, "by": res.get("by"), "note": res.get("note"),
                 "quotes": (res.get("quotes") or [])[:3], "kind": claim["kind"], "label": claim["label"]}
        if d:
            upheld = bool(res.get("dispute_upheld")) and entry["result"] == "supported"
            entry["dispute_upheld"] = upheld
            disputes["open"].remove(d)
            disputes.setdefault("closed", []).append({**d, "closed": today, "outcome": "stands" if upheld else "withdrawn",
                                                      "note": res.get("note")})
        checks[fp] = entry
        done += 1
        log.info("checked %s: %s (%s)", claim["label"][:60], entry["result"], claim["kind"])
    # Results for claims that no longer exist are dropped, so the file holds only what is on the site.
    store["checks"] = {fp: v for fp, v in checks.items() if fp in claims}
    store["_how"] = ("Source-check results keyed by claim fingerprint (written by speculum.verify). A claim is "
                     "published only when its result is 'supported'. Delete an entry to have it checked again.")
    (cdir / "checks.json").write_text(json.dumps(store, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (cdir / "disputes.json").write_text(json.dumps(disputes, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    left = max(0, len(queue) - limit)
    log.info("source check: %d checked, %d left for the next night", done, left)
    return {"checked": done, "left": left}


def ingest_disputes(data_root: Path) -> int:
    """Move readers' disputes from the Cloudflare KV store (written by functions/api/dispute.js) into
    data/chains/disputes.json, where they withdraw the item until the checker has re-read it."""
    token, acct = os.environ.get("CLOUDFLARE_API_TOKEN"), os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not token or not acct:
        log.info("no Cloudflare credentials; no disputes read")
        return 0
    base = f"https://api.cloudflare.com/client/v4/accounts/{acct}/storage/kv/namespaces"
    h = {"Authorization": f"Bearer {token}"}
    try:
        ns = requests.get(base, headers=h, params={"per_page": 100}, timeout=60).json()
        nid = next((n["id"] for n in ns.get("result") or [] if n.get("title") == "speculum-disputes"), None)
        if not nid:
            log.info("no disputes store yet")
            return 0
        keys = requests.get(f"{base}/{nid}/keys", headers=h, params={"prefix": "d:", "limit": 1000}, timeout=60).json()
        names = [k["name"] for k in keys.get("result") or []]
    except Exception as exc:
        log.warning("could not read disputes: %s", exc)
        return 0
    path = data_root / "chains" / "disputes.json"
    disputes = chains.load_json(path, {"open": [], "closed": []})
    seen = {(d.get("fp"), d.get("source")) for d in disputes.get("open", []) + disputes.get("closed", [])}
    added = 0
    for name in names:
        try:
            d = requests.get(f"{base}/{nid}/values/{name}", headers=h, timeout=60).json()
        except Exception:
            continue
        if not re.fullmatch(r"[0-9a-f]{16}", str(d.get("fp", ""))) or not str(d.get("source", "")).startswith(("http://", "https://")):
            pass
        elif (d["fp"], d["source"]) not in seen:
            disputes.setdefault("open", []).append({k: d.get(k) for k in ("id", "fp", "chain", "source", "note", "received")})
            seen.add((d["fp"], d["source"]))
            added += 1
        requests.delete(f"{base}/{nid}/values/{name}", headers=h, timeout=60)
    path.write_text(json.dumps(disputes, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("disputes received: %d new (%d open)", added, len(disputes.get("open", [])))
    return added
