#!/usr/bin/env python3
"""
The one-hour test: is the LLM actually doing work a rule engine could not?

Run this BEFORE building the agent loop. It hits two equally-weighted judging
criteria (Technological Implementation and Creativity), and a judge feels a
decorative LLM without being able to name it.

    python scripts/llm_load_bearing_test.py

READ THE RESULT HONESTLY:
    >= 18/20 agree  -> the LLM is decoration. Move it onto the ambiguous work.
    12-17/20 agree  -> partially load-bearing. Find where it diverges and lean there.
    <= 11/20 agree  -> genuinely load-bearing. Proceed as planned.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.app.tools.legistar import LegistarFeed, Meeting  # noqa: E402

# --- ADA obligations, corrected 2026-08-27 -------------------------------
# 28 CFR 35.160 (effective communication) has been in force since 1991 and is
# what actually governs interpreters/CART. Subpart H (35.200) governs WEB
# CONTENT and is dated April 26 2027 / 2028 as extended.
LARGE_ENTITY_DEADLINE = "2027-04-26"
SMALL_ENTITY_DEADLINE = "2028-04-26"


# ========================================================================
# THE RULE ENGINE — 30 lines, no model, no API key
# ========================================================================

def rules_verdict(m: Meeting, pop_over_50k: bool) -> dict:
    """Everything a deterministic engine can decide on its own."""
    comment = (m.comment or "").lower()

    if "cancel" in comment:
        action = "REPLAN"
    elif "postpone" in comment or "reschedul" in comment:
        action = "REPLAN"
    elif m.agenda_url:
        action = "COORDINATE"
    else:
        action = "WAIT"

    obligations = []
    if m.agenda_url:
        obligations.append({
            "basis": "28 CFR 35.200",
            "deadline": LARGE_ENTITY_DEADLINE if pop_over_50k else SMALL_ENTITY_DEADLINE,
        })
    start = m.starts_at
    if start:
        obligations.append({
            "basis": "28 CFR 35.160",
            "deadline": (start - timedelta(hours=48)).isoformat()[:10],
        })

    return {"action": action, "bases": sorted(o["basis"] for o in obligations)}


# ========================================================================
# THE AGENT — same question, put to the model
# ========================================================================

AGENT_PROMPT = """You coordinate accessibility accommodations for public meetings.

Meeting record:
  body:      {body}
  date:      {date} {time}
  location:  {location}
  comment:   {comment}
  agenda:    {agenda}
  entity serves over 50,000 people: {pop}

Decide:
1. action — exactly one of COORDINATE, REPLAN, WAIT
2. bases  — which of "28 CFR 35.160" (effective communication, in force since
   1991, governs interpreters/CART) and "28 CFR 35.200" (Subpart H, governs web
   content conformance, compliance date April 26 2027 for entities serving 50k+,
   April 26 2028 otherwise) apply.

Reply with JSON only: {{"action": "...", "bases": ["..."]}}"""


def agent_verdict(m: Meeting, pop_over_50k: bool) -> dict:
    from strands import Agent
    from backend.app.agents.model import get_model

    prompt = AGENT_PROMPT.format(
        body=m.body_name, date=m.date, time=m.time or "unspecified",
        location=m.location or "unspecified", comment=m.comment or "none",
        agenda=m.agenda_url or "not posted", pop=pop_over_50k,
    )
    raw = str(Agent(model=get_model())(prompt))
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {"action": "PARSE_FAIL", "bases": [], "raw": raw[:200]}
    try:
        out = json.loads(match.group(0))
        return {"action": out.get("action"), "bases": sorted(out.get("bases", []))}
    except json.JSONDecodeError:
        return {"action": "PARSE_FAIL", "bases": [], "raw": raw[:200]}


# ========================================================================

def main() -> None:
    feed = LegistarFeed()
    meetings: list[Meeting] = []
    for client in feed.clients:
        try:
            meetings.extend(feed.upcoming(client, days_ahead=60))
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {client}: {exc}")
    meetings = meetings[:20]

    if len(meetings) < 20:
        print(f"WARNING: only {len(meetings)} meetings. Widen days_ahead or add clients.\n")

    agree = 0
    rows = []
    for i, m in enumerate(meetings, 1):
        pop = m.client in {"seattle", "oakland", "sanjose"}   # >50k
        r = rules_verdict(m, pop)
        a = agent_verdict(m, pop)
        same = (r["action"] == a["action"]) and (r["bases"] == a["bases"])
        agree += same
        rows.append((i, m.body_name[:34], r["action"], a["action"], "same" if same else "DIFFERS"))
        print(f"{i:2d}. {m.body_name[:34]:34s}  rules={r['action']:10s} agent={str(a['action']):10s} "
              f"{'' if same else '  <-- DIFFERS'}")

    print("\n" + "=" * 72)
    print(f"AGREEMENT: {agree}/{len(meetings)}")
    print("=" * 72)
    if agree >= 18:
        print("""
VERDICT: the LLM is DECORATION on this task.

Strip it and a rules engine does the same job. That fails your own constraint 9
and it will read as thin to a judge on two of five criteria.

THE FIX — move the agent onto work that is genuinely ambiguous:
  1. Parse free-text EventComment into intent. Agencies write these
     inconsistently across every jurisdiction; there is no schema.
  2. Read the agenda PDF and infer which accommodations THAT AGENDA ITEM needs.
     A public hearing on a housing ordinance in a high-LEP district has a
     different accommodation profile from a procedural consent calendar.

(2) is irreducible, and it is a better product. Rebuild around it today.
""")
    elif agree >= 12:
        print("\nVERDICT: PARTIALLY load-bearing. Look at every DIFFERS row above —\n"
              "that is where the model earns its place. Build the agent around those cases.\n")
    else:
        print("\nVERDICT: genuinely load-bearing. Proceed as planned.\n")

    with open("llm_test_results.json", "w") as fh:
        json.dump({"agreement": agree, "total": len(meetings), "rows": rows}, fh, indent=2)
    print("Written to llm_test_results.json — keep it, it is README and blog-post material.")


if __name__ == "__main__":
    main()
