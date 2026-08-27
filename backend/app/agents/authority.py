"""
AccessFlow — the authority layer.

This module REPLACES §5.2 of the master prompt (the ACT / ASK / BLOCK table)
as an *implementation*. The table stops being a description in a document and
becomes an enforcement point that runs before every tool call.

Two mechanisms, in order of precedence:

  1. Cedar Authorization (intervention).  Hard boundary. Default-deny,
     fail-closed. If the policy does not permit the call, the tool does not
     run and the agent is told why. No prompt can talk past it.

  2. LLM steering (plugin).  Soft boundary. Catches the things a policy cannot
     express -- a case summary that overstates what the evidence shows, a
     decision request that buries the recommendation, prose that reads as a
     verdict about a named public body.

APIs verified against the Strands documentation on 2026-08-25:
    from strands.vended_interventions.cedar import CedarAuthorization
    from strands.vended_plugins.steering import LLMSteeringHandler
    Agent(tools=[...], interventions=[cedar], plugins=[steering])
    cedar.reload()          # atomic hot-swap; invalid policy leaves prior in effect
    context.input.*         # tool arguments
    context.session.*       # hour_utc, call_count, plus whatever context_enricher adds

Steering is Python-only. Cedar exists in both SDKs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strands import Agent
from strands.vended_interventions.cedar import CedarAuthorization
from strands.vended_plugins.steering import LLMSteeringHandler

log = logging.getLogger(__name__)

POLICY_FILE = Path(__file__).resolve().parents[3] / "policies" / "accessflow.cedar"


# ---------------------------------------------------------------------------
# Principal resolution — fail-closed by construction
# ---------------------------------------------------------------------------

def _resolve_principal(state: dict[str, Any]) -> dict[str, str] | None:
    """Map the invocation to a Cedar principal.

    Returning None denies every tool call in the turn. That is deliberate: an
    unattributed agent run must not be able to contact a provider or close a
    case. Cedar's own documented behaviour is that an unresolvable principal
    denies everything.
    """
    coordinator = state.get("coordinator_id")
    if not coordinator:
        log.warning("no coordinator_id on invocation; all tool calls will be denied")
        return None
    return {"type": "Coordinator", "id": str(coordinator)}


# ---------------------------------------------------------------------------
# Context enrichment — the facts the policy needs, computed outside the model
# ---------------------------------------------------------------------------

def _enrich_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Everything here is derived from persisted case state, never from model
    output. The model cannot assert `verification_passed` into existence.
    """
    inv: dict[str, Any] = ctx.get("invocation_state", {}) or {}
    case: dict[str, Any] = inv.get("case", {}) or {}

    requested_at = case.get("requested_at")
    hours_since_request = 0
    if requested_at:
        try:
            then = datetime.fromisoformat(str(requested_at).replace("Z", "+00:00"))
            hours_since_request = int(
                (datetime.now(timezone.utc) - then).total_seconds() // 3600
            )
        except (ValueError, TypeError):
            hours_since_request = 0

    return {
        "role": str(inv.get("role", "coordinator")),
        "org_id": str(inv.get("org_id", "")),
        # Set by the Verification Agent's persisted result, never by prose.
        "verification_passed": bool(case.get("verification_passed", False)),
        "reminders_sent_24h": int(case.get("reminders_sent_24h", 0)),
        "hours_since_request": hours_since_request,
        "case_state": str(case.get("state", "NEW")),
    }


# ---------------------------------------------------------------------------
# The steering handler — what policy cannot express
# ---------------------------------------------------------------------------

_STEERING_PROMPT = """
You review AccessFlow's outgoing text before it is written or sent. AccessFlow
coordinates accessibility accommodations for real public meetings on behalf of
the public body's own coordinator.

Return Guide (cancel and give feedback) if any of the following is true:

1. OVERSTATED EVIDENCE. The text says an accommodation is arranged, confirmed,
   fulfilled or complete when the case record does not contain the corresponding
   evidence. Anything not evidenced must be phrased as pending or unrecorded.

2. A VERDICT ABOUT A NAMED BODY. The text asserts that a named public body,
   department or vendor is non-compliant, in violation, negligent or at fault.
   AccessFlow works for the coordinator. It records what is and is not yet
   evidenced. It does not find anyone guilty. Rewrite as a statement about the
   record: "no conformance record on file for this document" — never "this
   document fails WCAG" or "the city is out of compliance".

3. INVENTED OPERATIONAL FACT. A provider name, availability, confirmation,
   price or response that did not come back from a tool call.

4. A BURIED DECISION. A decision request that does not answer, in this order:
   what changed, what was checked, the safe options, the recommendation, and
   why the agent stopped.

5. LEAKED REASONING. Internal deliberation or chain-of-thought presented to the
   user. Show provenance instead: facts checked, constraints applied, action
   taken, result.

Return Proceed otherwise. Be strict on 1 and 2 and lenient on style.
"""


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_authority(policy_file: Path = POLICY_FILE) -> CedarAuthorization:
    """The hard boundary. Loaded from the .cedar file so the authority model is
    reviewable as policy, diffable in git, and hot-reloadable in the demo."""
    if not policy_file.exists():
        raise FileNotFoundError(f"authority policy missing: {policy_file}")

    return CedarAuthorization(
        policies=str(policy_file),
        principal_resolver=_resolve_principal,
        context_enricher=_enrich_context,
        # Never 'proceed'. A policy-engine failure must close the gate, not open it.
        on_error="deny",
    )


def build_steering() -> LLMSteeringHandler:
    """The soft boundary."""
    return LLMSteeringHandler(system_prompt=_STEERING_PROMPT)


def build_case_agent(tools: list[Any], system_prompt: str) -> tuple[Agent, CedarAuthorization]:
    """Construct the Case Orchestrator with both boundaries attached.

    The CedarAuthorization handle is returned alongside the agent so the demo
    can call `.reload()` on camera and show the authority model changing
    without a restart.
    """
    cedar = build_authority()
    agent = Agent(
        tools=tools,
        system_prompt=system_prompt,
        interventions=[cedar],
        plugins=[build_steering()],
    )
    return agent, cedar
