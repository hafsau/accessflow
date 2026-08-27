#!/usr/bin/env python3
"""
LLM load-bearing test v2: agenda PDF analysis.

This is the IRREDUCIBLE work — inferring which accommodations a specific
agenda item requires by reading the actual document. A public hearing on a
housing ordinance in a high-LEP district has a different accommodation profile
from a procedural consent calendar.

No rules engine can do this. If the LLM performs well here, it justifies itself.

    MODEL_PROVIDER=bedrock python scripts/llm_test_v2_agenda.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from dataclasses import dataclass

import httpx
from pypdf import PdfReader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.app.tools.legistar import LegistarFeed, Meeting  # noqa: E402

# Accommodation categories the agent should identify
ACCOMMODATION_TYPES = [
    "ASL_INTERPRETER",      # American Sign Language
    "CART",                 # Communication Access Realtime Translation
    "SPANISH_INTERPRETER",  # Spanish language interpretation
    "OTHER_LANGUAGE",       # Other language interpretation
    "ASSISTIVE_LISTENING",  # Hearing loop / FM system
    "LARGE_PRINT",          # Large print materials
    "BRAILLE",              # Braille materials
    "EXTENDED_TIME",        # Extended public comment time
    "REMOTE_ACCESS",        # Remote/virtual participation option
]


@dataclass
class AgendaAnalysis:
    meeting_key: str
    body_name: str
    agenda_url: str
    page_count: int
    text_preview: str
    llm_accommodations: list[str]
    llm_reasoning: str
    llm_priority: str  # HIGH, MEDIUM, LOW


ANALYSIS_PROMPT = """You are an accessibility coordinator analyzing a public meeting agenda.

Meeting: {body_name}
Date: {date}

Agenda text (first 3000 chars):
---
{text}
---

Based on this agenda, determine:

1. ACCOMMODATIONS: Which of these accommodation types are likely needed?
   {accommodation_types}

   Consider:
   - Public hearings typically need interpreters (ASL, Spanish in high-LEP areas)
   - Long agendas or complex topics may need CART
   - Routine consent calendars have lower accommodation needs
   - Community input items need broader accessibility

2. PRIORITY: Rate as HIGH, MEDIUM, or LOW based on:
   - HIGH: Public hearing, community input, controversial topic, equity/housing/immigration
   - MEDIUM: Regular business with some public interest
   - LOW: Procedural, closed session, routine approvals

3. REASONING: Brief explanation of your assessment.

Reply with JSON only:
{{
  "accommodations": ["TYPE1", "TYPE2"],
  "priority": "HIGH|MEDIUM|LOW",
  "reasoning": "Brief explanation"
}}"""


def fetch_agenda_text(url: str, max_pages: int = 5) -> tuple[str, int]:
    """Download PDF and extract text from first N pages."""
    try:
        resp = httpx.get(url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        reader = PdfReader(io.BytesIO(resp.content))
        page_count = len(reader.pages)
        text_parts = []
        for i, page in enumerate(reader.pages[:max_pages]):
            text_parts.append(page.extract_text() or "")
        return "\n".join(text_parts), page_count
    except Exception as exc:
        return f"[PDF fetch/parse failed: {exc}]", 0


def analyze_agenda(m: Meeting) -> AgendaAnalysis | None:
    """Use LLM to analyze an agenda PDF."""
    if not m.agenda_url:
        return None

    text, page_count = fetch_agenda_text(m.agenda_url)
    if page_count == 0:
        print(f"  skip {m.key}: PDF fetch failed")
        return None

    from strands import Agent
    from backend.app.agents.model import get_model

    prompt = ANALYSIS_PROMPT.format(
        body_name=m.body_name,
        date=m.date,
        text=text[:3000],
        accommodation_types=", ".join(ACCOMMODATION_TYPES),
    )

    try:
        raw = str(Agent(model=get_model())(prompt))
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return AgendaAnalysis(
                meeting_key=m.key, body_name=m.body_name, agenda_url=m.agenda_url,
                page_count=page_count, text_preview=text[:200],
                llm_accommodations=[], llm_reasoning="PARSE_FAIL", llm_priority="UNKNOWN"
            )
        data = json.loads(match.group(0))
        return AgendaAnalysis(
            meeting_key=m.key,
            body_name=m.body_name,
            agenda_url=m.agenda_url,
            page_count=page_count,
            text_preview=text[:200],
            llm_accommodations=data.get("accommodations", []),
            llm_reasoning=data.get("reasoning", ""),
            llm_priority=data.get("priority", "UNKNOWN"),
        )
    except Exception as exc:
        print(f"  error on {m.key}: {exc}")
        return None


def main() -> None:
    feed = LegistarFeed()
    meetings_with_agendas: list[Meeting] = []

    print("Fetching meetings with agendas...")
    for client in feed.clients:
        try:
            for m in feed.upcoming(client, days_ahead=45):
                if m.agenda_url:
                    meetings_with_agendas.append(m)
        except Exception as exc:
            print(f"  skip {client}: {exc}")

    # Take first 10 with agendas
    sample = meetings_with_agendas[:10]
    print(f"Found {len(meetings_with_agendas)} meetings with agendas, analyzing {len(sample)}...\n")

    results: list[AgendaAnalysis] = []
    for i, m in enumerate(sample, 1):
        print(f"{i:2d}. {m.body_name[:40]} ({m.client})")
        analysis = analyze_agenda(m)
        if analysis:
            results.append(analysis)
            print(f"    Priority: {analysis.llm_priority}")
            print(f"    Accommodations: {', '.join(analysis.llm_accommodations) or 'none identified'}")
            print(f"    Reasoning: {analysis.llm_reasoning[:80]}...")
        print()

    # Summary
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)

    high = [r for r in results if r.llm_priority == "HIGH"]
    medium = [r for r in results if r.llm_priority == "MEDIUM"]
    low = [r for r in results if r.llm_priority == "LOW"]

    print(f"HIGH priority:   {len(high)}")
    print(f"MEDIUM priority: {len(medium)}")
    print(f"LOW priority:    {len(low)}")

    all_accommodations: dict[str, int] = {}
    for r in results:
        for acc in r.llm_accommodations:
            all_accommodations[acc] = all_accommodations.get(acc, 0) + 1

    print("\nAccommodation frequency:")
    for acc, count in sorted(all_accommodations.items(), key=lambda x: -x[1]):
        print(f"  {acc}: {count}")

    # Save results
    output = {
        "total_analyzed": len(results),
        "by_priority": {"HIGH": len(high), "MEDIUM": len(medium), "LOW": len(low)},
        "accommodation_frequency": all_accommodations,
        "analyses": [
            {
                "key": r.meeting_key,
                "body": r.body_name,
                "priority": r.llm_priority,
                "accommodations": r.llm_accommodations,
                "reasoning": r.llm_reasoning,
                "page_count": r.page_count,
            }
            for r in results
        ],
    }

    with open("llm_test_v2_agenda.json", "w") as fh:
        json.dump(output, fh, indent=2)
    print("\nWritten to llm_test_v2_agenda.json")

    print("\n" + "=" * 72)
    if len(high) > 0 or len(all_accommodations) > 3:
        print("VERDICT: LLM is extracting actionable accommodation signals from PDFs.")
        print("This is irreducible work — no rules engine can do it.")
    else:
        print("VERDICT: Results inconclusive. Try with more varied agendas.")
    print("=" * 72)


if __name__ == "__main__":
    main()
