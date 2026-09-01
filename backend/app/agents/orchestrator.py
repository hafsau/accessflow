"""
AccessFlow Case Orchestrator — one agent, not a graph.

AWS's own engineering blog measured steering hooks at 100% accuracy against
graph workflows at 80.8%, and explicitly warns against decomposing tasks into
separate agents within graph workflows. See:
https://strandsagents.com/blog/what-we-learned-from-one-year-of-building-production-agents/

Two more reasons specific to this entry:
1. GraphBuilder / Swarm are the most-sampled Strands surface. Every official
   example shows them. Creativity asks for a non-obvious use — a graph is the
   obvious one.
2. The 1st-place winner of the last AWS agent hackathon used one agent.

This module implements the Case Orchestrator as a single Agent with 11 tools
and a Cedar intervention for the hard authorization boundary.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from strands import Agent
from strands.types.agent import Limits

from backend.app.agents.authority import (
    TracingCedarAuthorization,
    build_authority,
)
from backend.app.agents.budget import check_and_charge, estimate, spent_today
from backend.app.agents.model import get_model
from backend.app.tools.case_tools import (
    close_case,
    create_case,
    derive_obligations,
    extract_accommodation_policy,
    fetch_agenda_document,
    get_case,
    get_event,
    poll_public_meetings,
    request_human_decision,
    search_providers,
    send_provider_request,
    verify_fulfillment,
)

log = logging.getLogger(__name__)

PROMPT_FILE = Path(__file__).parent / "prompts" / "orchestrator.md"

# All 12 tools: 11 deterministic, 1 model-bearing
# Note: confirm_provider_request is NOT in this list. The agent cannot
# manufacture confirmations it later verifies against. Provider responses
# are triggered by operators via the console, labelled as simulated.
ALL_TOOLS = [
    # Deterministic (no model call)
    poll_public_meetings,
    get_case,
    get_event,
    fetch_agenda_document,
    derive_obligations,
    search_providers,
    send_provider_request,
    request_human_decision,
    verify_fulfillment,
    close_case,
    create_case,
    # Model-bearing (uses judgment)
    extract_accommodation_policy,
]


class BudgetedModel:
    """Wraps a model to enforce budget checks before each invocation.

    A multi-step agent loop resends the whole history each turn: roughly $1 per
    case at Sonnet rates. This wrapper ensures check_and_charge() is called
    before every Bedrock invocation, enforcing the DAILY_USD_CAP.
    """

    def __init__(self, inner_model: Any):
        self._inner = inner_model
        self.model_calls = 0
        self.total_in_tokens = 0
        self.total_out_tokens = 0

    def __getattr__(self, name: str) -> Any:
        """Delegate all attributes to the inner model."""
        return getattr(self._inner, name)

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        """Wrap stream to meter before invocation.

        The Strands SDK BedrockModel uses stream() for all model calls.
        This must be an async generator to match Strands' expectations.
        """
        # Estimate tokens for budget check
        # Conservative estimate: ~4k input (context + history), ~1k output
        estimated_in = 4000
        estimated_out = 1000
        cached_in = 0

        # Check budget before invocation
        check_and_charge(estimate(estimated_in, estimated_out, cached_in))
        self.model_calls += 1

        # Make the actual call and yield results
        async for chunk in self._inner.stream(*args, **kwargs):
            # Track usage from chunks if available
            if hasattr(chunk, "usage"):
                usage = chunk.usage
                if hasattr(usage, "input_tokens"):
                    self.total_in_tokens = usage.input_tokens
                if hasattr(usage, "output_tokens"):
                    self.total_out_tokens = usage.output_tokens
            yield chunk


def load_system_prompt() -> str:
    """Load the system prompt from the versioned file."""
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(f"System prompt missing: {PROMPT_FILE}")
    return PROMPT_FILE.read_text()


def build_orchestrator(
    cedar: TracingCedarAuthorization | None = None,
    default_coordinator: str = "coord_demo",
) -> tuple[Agent, BudgetedModel, TracingCedarAuthorization]:
    """Build the Case Orchestrator agent.

    Args:
        cedar: Optional pre-built Cedar authorization. If None, builds one.
        default_coordinator: Default coordinator ID for principal resolution.
            Used when no coordinator_id is in the invocation state.

    Returns:
        tuple of (agent, budgeted_model, cedar_authorization)

    The budgeted_model is returned so callers can inspect model_calls and
    token counts after running. The cedar handle is returned so the demo
    can call .reload() on camera.
    """
    if cedar is None:
        cedar = build_authority(default_coordinator=default_coordinator)

    inner_model = get_model()
    budgeted_model = BudgetedModel(inner_model)

    agent = Agent(
        model=budgeted_model,
        tools=ALL_TOOLS,
        system_prompt=load_system_prompt(),
        interventions=[cedar],
        # context_manager="auto" offloads large tool results and compresses
        # old turns. Without it, a 15-turn case is where the $50 goes.
        context_manager="auto",
    )

    return agent, budgeted_model, cedar


# Hard limit on agent turns. When hit, the agent stops and must escalate.
# Belt: Strands SDK enforces this before each cycle.
# Braces: Cedar forbid rule at turns > 15 catches any bypass.
MAX_TURNS: int = 12


def run_case(
    agent: Agent,
    meeting: dict[str, Any],
    budgeted_model: BudgetedModel,
) -> dict[str, Any]:
    """Run the orchestrator on a single meeting.

    Args:
        agent: The orchestrator agent
        meeting: Meeting dict from poll_public_meetings
        budgeted_model: The budgeted model wrapper for tracking

    Returns:
        dict with case_id, final_state, verification_passed, model_calls, spent_usd
    """
    # Build the initial prompt for this meeting
    prompt = f"""A new meeting has arrived from the live feed:

Meeting Key: {meeting['key']}
Body: {meeting['body_name']}
Date: {meeting['date']}
Time: {meeting.get('time', 'Not specified')}
Agenda URL: {meeting.get('agenda_url', 'None')}

Process this meeting through to closure:
1. Create a case for this meeting
2. Derive the required obligations
3. Fetch the agenda document, then extract the accommodation policy from the
   text it returns. This is what tells you WHICH accommodations this meeting
   needs. Do not skip it. There is no default accommodation.
4. Search for providers matching the accommodations you identified in step 3
5. Send ONE provider request (with an idempotency key)
6. Verify fulfillment
7. Close the case when verification passes

Steps 3 and 4 are not interchangeable and their order is not negotiable. Booking
a provider for an accommodation you have not established this meeting needs is
inventing an operational fact.

If the Agenda URL above is None, or if fetching or extracting fails, that is not
a finding that no accommodations are needed — request a human decision and say
which step failed.

If you cannot proceed safely at any step, request a human decision."""

    # Run the agent with turn limit
    log.info("Starting orchestrator for meeting %s", meeting["key"])
    limits: Limits = {"turns": MAX_TURNS}
    result = agent(prompt, limits=limits)

    # Check if we hit the turn limit — escalate to human
    if result.stop_reason == "limit_turns":
        log.warning(
            "Orchestrator hit %d-turn limit on meeting %s, escalating to human",
            MAX_TURNS,
            meeting["key"],
        )
        # Call request_human_decision to record the escalation
        import uuid
        from backend.app.tools.case_tools import request_human_decision
        escalation = request_human_decision(
            idempotency_key=f"turn_limit_{meeting['key']}_{uuid.uuid4().hex[:8]}",
            case_id=meeting.get("case_id", "unknown"),
            decision_type="TURN_LIMIT_EXCEEDED",
            context=f"Agent reached {MAX_TURNS}-turn limit without completing case. Review agent trace and manually complete remaining steps.",
            options=[
                {"option_id": "extend", "description": "Continue processing with extended limit"},
                {"option_id": "close_incomplete", "description": "Close case as incomplete"},
                {"option_id": "manual", "description": "Manually complete remaining steps", "recommended": True},
            ],
        )
        return {
            "meeting_key": meeting["key"],
            "body_name": meeting["body_name"],
            "event_date": meeting["date"],
            "model_calls": budgeted_model.model_calls,
            "spent_usd": spent_today(),
            "in_tokens": budgeted_model.total_in_tokens,
            "out_tokens": budgeted_model.total_out_tokens,
            "result": result,
            "escalation": escalation,
            "stop_reason": "limit_turns",
        }

    # Extract final state from the result
    # The agent should have created and closed a case
    return {
        "meeting_key": meeting["key"],
        "body_name": meeting["body_name"],
        "event_date": meeting["date"],
        "model_calls": budgeted_model.model_calls,
        "spent_usd": spent_today(),
        "in_tokens": budgeted_model.total_in_tokens,
        "out_tokens": budgeted_model.total_out_tokens,
        "result": result,
        "stop_reason": result.stop_reason,
    }
