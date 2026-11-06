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


SYSTEM_RULE = """You research consequence chains for Speculum, a nonpartisan record of the US Congress.
A chain follows one recorded decision to what happened to people afterwards. This decision is a joint resolution
under the Congressional Review Act to overturn a federal agency's rule. You draft two kinds of link:

mechanism: exactly what the rule did and what the resolution changed, from the Federal Register, the agency's
own regulatory impact analysis or economic analysis, Congress.gov, the Congressional Research Service, GAO or
the Congressional Budget Office:
  rule        the rule's name and Federal Register citation, its publication and effective dates
  required    what it required or changed, and of whom
  benefits    the agency's own estimate of the rule's benefits, with figures, units and the period they cover
  costs       the agency's own estimate of the rule's costs, from the same analysis
  agency_reasons   the agency's stated purpose
  sponsor_reasons  the stated reasons of the resolution's sponsors and supporters, from their own statements
  status      what happened: whether the resolution became law (and its Public Law number) or failed, and
              therefore whether the rule is void or still in force

outcomes: what happened to people afterwards, or what official analysis expects, each with one evidence grade:
  documented  an official record of what actually followed (agency data, GAO, inspector general, court records)
  estimated   an official or peer-reviewed estimate: the agency's regulatory impact analysis, CBO, or a
              peer-reviewed study. A forecast made before the vote is an estimate, never a documented outcome.
  associated  credible reporting or analysis of a pattern without direct evidence for the specific claim

Rules you never break:
- Only state what a source you opened says. Give figures with the date and period they apply to.
- Give the agency's estimated costs as faithfully as its estimated benefits, from the same analysis, and record
  any official dispute of those estimates (a CBO, GAO or court finding) beside them.
- Record harm the rule's removal prevented, or burden it lifted, as faithfully as harm it allowed.
- Prefer primary documents. Use news only for original reporting of what followed.
- Never characterise anyone's intent, and never call a person, party, company or agency good or evil.
- If nothing credible records what followed, say so; that is a valid finding.

Answer with one JSON object and nothing else:
{"mechanism": {"rule": "...", "agency": "...", "federal_register": "...", "published": "YYYY-MM-DD", "effective": "YYYY-MM-DD",
               "required": "...", "benefits": "...", "costs": "...", "agency_reasons": "...", "sponsor_reasons": "...",
               "status": "...", "sources": [{"title": "...", "url": "...", "publisher": "...", "date": "..."}]},
 "outcomes": [{"grade": "documented|estimated|associated", "claim": "...", "date": "YYYY-MM-DD",
               "source": {"title": "...", "url": "...", "publisher": "...", "date": "..."}, "counterpoint": "..."}],
 "notes": "anything uncertain or contested"}"""


_COMMON = """
Evidence grades for outcomes:
  documented  an official record of what actually followed (government data, GAO, inspectors general, courts,
              UN bodies, forensic or investigative records that identify what happened and to whom)
  estimated   an official or peer-reviewed estimate (CBO, an agency's own analysis, UN agencies, peer-reviewed
              studies). A forecast made before the vote is an estimate, never a documented outcome.
  associated  credible reporting or analysis of a pattern without direct evidence for the specific claim

Rules you never break:
- Only state what a source you opened says. Give figures with the date and period they apply to.
- Give estimated costs as faithfully as estimated benefits, from the same analysis, and record any official
  dispute of an estimate beside it.
- Record harm a decision prevented, or burden it lifted, as faithfully as harm it caused or allowed.
- Give the stated reasons of both sides in their own terms, from their own statements.
- Prefer primary documents. Use news only for original reporting of what followed.
- Never characterise anyone's intent, and never call a person, party, company, country or agency good or evil.
- If nothing credible records what followed, say so; that is a valid finding.

Answer with one JSON object and nothing else:
{"mechanism": {MECHANISM, "status": "...", "sources": [{"title": "...", "url": "...", "publisher": "...", "date": "..."}]},
 "outcomes": [{"grade": "documented|estimated|associated", "claim": "...", "date": "YYYY-MM-DD",
               "source": {"title": "...", "url": "...", "publisher": "...", "date": "..."}, "counterpoint": "..."}],
 "notes": "anything uncertain or contested"}"""

_HEAD = """You research consequence chains for Speculum, a nonpartisan record of the US Congress.
A chain follows one recorded decision to what happened to people afterwards. """

BRIEFS = {
    "reconciliation": _HEAD + """This decision is a budget reconciliation bill. You draft:

mechanism: what the bill changed, from the enacted text, Congress.gov, the Congressional Budget Office, the Joint
Committee on Taxation and the Congressional Research Service:
  summary          its major provisions: taxes, spending programs, eligibility rules, the debt limit
  budget_effect    CBO's (and JCT's) estimate of its effect on deficits, with the period it covers
  distribution     CBO's estimate of its effects on households by income
  sponsor_reasons  supporters' stated reasons; opponent_reasons: opponents' stated objections
  status           whether it became law (and its Public Law number) or failed

outcomes: what happened to people afterwards, or what official analysis expects (for example coverage, benefits,
taxes paid, prices or jobs), by group affected.""".replace("{MECHANISM}", ""),
    "emergencies": _HEAD + """This decision is a joint resolution to end a national emergency under the National
Emergencies Act. You draft:

mechanism: what the emergency did, from the Federal Register, the executive order or proclamation, CRS, CBO,
the US International Trade Commission or the agencies carrying it out:
  declared           when and by what order it was declared, and what it imposed or allowed (for tariffs: the
                     rates, the goods and countries covered, and changes since)
  estimates          official estimates of its effects (revenue, prices, trade volumes), with periods
  executive_reasons  the stated basis for the emergency; sponsor_reasons: the resolution's sponsors' stated reasons
  status             whether the resolution passed either chamber or became law, and whether the emergency continues

outcomes: what happened to people afterwards (prices paid, jobs, businesses, trading partners' responses), or
what official analysis expects.""",
    "war-powers": _HEAD + """This decision is a resolution or bill directing the removal of United States Armed Forces
from hostilities under the War Powers Resolution. You draft:

mechanism: the hostilities, from the President's War Powers reports, the Department of Defense, CRS and official
statements:
  hostilities        what operations, where, since when and at what scale, and what Congress had or had not authorized
  executive_reasons  the administration's stated legal basis; sponsor_reasons: the resolution's sponsors' stated reasons
  status             what happened to the resolution, and whether the hostilities continued

outcomes: what happened to people afterwards: civilian and military casualties, displacement, damage, costs, each
from UN bodies, official records or forensic investigations, with any official dispute beside it.""",
}
for _k in BRIEFS:
    _MECH = {"reconciliation": '"summary": "...", "budget_effect": "...", "distribution": "...", "sponsor_reasons": "...", "opponent_reasons": "..."',
             "emergencies": '"declared": "...", "estimates": "...", "executive_reasons": "...", "sponsor_reasons": "..."',
             "war-powers": '"hostilities": "...", "executive_reasons": "...", "sponsor_reasons": "..."'}[_k]
    BRIEFS[_k] = BRIEFS[_k] + _COMMON.replace("{MECHANISM", "{" + _MECH)


def _prompt_rule(chain: dict) -> str:
    votes = "; ".join(f"{(v.get('chamber') or '').title()} {v['date']}: {v['question']}, {v['result']} ({v['yeas']}-{v['nays']})"
                      for v in chain["votes"])
    return (f"Decision: {chain['title']} ({chain['bill']}, {chain['congress']}th Congress). "
            + (f"Agency: {chain.get('agency')}. Rule: {chain.get('rule_title')}. " if chain.get("agency") else
               f"{(chain.get('page') or {}).get('subject_label', 'Subject')}: {chain.get('subject') or 'not named'}. ")
            + f"Recorded votes: {votes}. "
            f"Latest action: {chain.get('latest_action_text') or 'unknown'}. Draft the mechanism and outcome links.")


def _prompt(chain: dict) -> str:
    v = chain["votes"][-1]
    return (f"Decision: {chain['title']} ({chain['bill']}, {chain['congress']}th Congress). "
            f"Last recorded Senate vote {v['date']}: {v['question']}, {v['result']} ({v['yeas']}-{v['nays']}). "
            f"Recipient: {chain.get('country')}. Rule: {chain['rule_name']}. Draft the mechanism and outcome links.")


def draft(chain: dict, key: str, max_searches: int = 8) -> dict:
    theme = chain.get("rule") or "arms-sales"
    system = SYSTEM if theme == "arms-sales" else SYSTEM_RULE if theme == "rules-overturned" else BRIEFS[theme]
    resp = requests.post(API, timeout=600, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={
        "model": MODEL, "max_tokens": 8000, "system": system,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}],
        "messages": [{"role": "user", "content": _prompt(chain) if theme == "arms-sales" else _prompt_rule(chain)}]})
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
    # Resolutions that became law first (their rules are void, so what followed is a matter of record), oldest
    # first, since older decisions have the longest record; a fixed order, the same every night.
    for ch in sorted(chains, key=lambda c: (not c.get("enacted"), c.get("date") or "")):
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
