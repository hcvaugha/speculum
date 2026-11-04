"""The drafter: asks Claude to research a chain's mechanism and outcome links.

It runs only when ANTHROPIC_API_KEY is set, only for chains that have no research yet, and it writes
drafts, never approvals. Its output lands in data/chains/drafts/<chain id>.json for a reviewer to read,
correct and merge into data/chains/research.json. Nothing it writes reaches a reader as fact until a
person approves the chain in data/chains/reviews.json.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from pathlib import Path

import requests

log = logging.getLogger("speculum.interpret")

API = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("SPECULUM_MODEL", "claude-sonnet-5-5")

SYSTEM = """You research consequence chains for Speculum, a nonpartisan record of the US Congress.
A chain follows one recorded decision to what happened to people afterwards. You draft two kinds of link:

mechanism: exactly what the decision changed (for an arms sale: items, value, recipient, named contractors,
the notification number) from the bill text, DSCA, State Department or CRS.

outcomes: what happened to civilians afterwards, each with one evidence grade:
  documented  an official or authoritative investigative record ties weapons or actions of this kind from
              this decision's recipient to specific harm (UN panels and commissions, US government reports,
              court orders, forensic investigations that identified US-made munitions at strike sites)
  estimated   authoritative aggregate figures for the conflict (UN OCHA, UN agencies, IPC, peer-reviewed)
  associated  credible reporting of a pattern without direct linkage

Rules you never break:
- Only state what a source you opened says. Give figures with the date they apply to.
- Prefer primary documents (UN, US government, courts, SIPRI). Use news only for original forensic work.
- Record the subject government's response or a credible dispute beside each outcome when one exists.
- Record harm a decision prevented as faithfully as harm it caused.
- Never characterise anyone's intent, and never call a person or a government good or evil.
- If nothing credible ties the decision to harm, say so; that is a valid finding.

Answer with one JSON object and nothing else:
{"mechanism": {"items": "...", "value": "...", "contractors": ["..."], "notification": "...",
               "proceeded": "...", "sources": [{"title": "...", "url": "...", "publisher": "...", "date": "..."}]},
 "outcomes": [{"country": "...", "grade": "documented|estimated|associated", "claim": "...",
               "source": {"title": "...", "url": "...", "publisher": "...", "date": "..."}, "counterpoint": "..."}],
 "notes": "anything uncertain or contested"}"""


def _prompt(chain: dict) -> str:
    v = chain["votes"][-1]
    return (f"Decision: {chain['title']} ({chain['bill']}, {chain['congress']}th Congress). "
            f"Last recorded Senate vote {v['date']}: {v['question']}, {v['result']} ({v['yeas']}-{v['nays']}). "
            f"Recipient: {chain.get('country')}. Rule: {chain['rule_name']}. Draft the mechanism and outcome links.")


def draft(chain: dict, key: str, max_searches: int = 8) -> dict:
    resp = requests.post(API, timeout=600, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={
        "model": MODEL, "max_tokens": 8000, "system": SYSTEM,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}],
        "messages": [{"role": "user", "content": _prompt(chain)}]})
    resp.raise_for_status()
    body = resp.json()
    text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("drafter returned no JSON")
    out = json.loads(m.group(0))
    out["_drafted"] = {"by": body.get("model", MODEL), "on": date.today().isoformat(), "status": "draft",
                       "usage": body.get("usage")}
    return out


def check() -> str:
    """One small live request with one web search, to prove the key, model and tool all work."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    resp = requests.post(API, timeout=120, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={
        "model": MODEL, "max_tokens": 300,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}],
        "messages": [{"role": "user", "content": "Search once for the DSCA major arms sales page and reply with its URL only."}]})
    if resp.status_code != 200:
        raise RuntimeError(f"{resp.status_code}: {resp.text[:500]}")
    body = resp.json()
    used = sum(1 for b in body.get("content", []) if b.get("type") == "server_tool_use")
    text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text").strip()
    return f"model={body.get('model')} searches={used} usage={body.get('usage')} reply={text[:200]!r}"


def draft_missing(chains: list[dict], drafts_dir: Path, limit: int = 3) -> list[str]:
    """Draft research for up to `limit` chains that have neither reviewed research nor a draft."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.info("no ANTHROPIC_API_KEY; skipping the drafter")
        return []
    drafts_dir.mkdir(parents=True, exist_ok=True)
    done = []
    for ch in chains:
        if len(done) >= limit:
            break
        path = drafts_dir / f"{ch['id']}.json"
        if ch.get("mechanism") or path.exists():
            continue
        try:
            path.write_text(json.dumps(draft(ch, key), indent=1, ensure_ascii=False), encoding="utf-8")
            done.append(ch["id"])
            log.info("drafted %s", ch["id"])
        except Exception:
            log.exception("drafting %s failed", ch["id"])
    return done
