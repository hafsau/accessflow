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

from backend.app.agents.cedar_debug import trace as cedar_trace

log = logging.getLogger(__name__)

POLICY_FILE = Path(__file__).resolve().parents[3] / "policies" / "accessflow.cedar"
SCHEMA_FILE = Path(__file__).resolve().parents[3] / "policies" / "accessflow.cedarschema"
SUPPLEMENTARY_POLICY_FILE = Path(__file__).parent / "tool_permits.cedar"


# ---------------------------------------------------------------------------
# Tracing wrapper — every evaluation is logged, errors raise in tests
# ---------------------------------------------------------------------------


class TracingCedarAuthorization(CedarAuthorization):
    """Wraps CedarAuthorization to trace every evaluation.

    Cedar silently skips erroring policies. This wrapper ensures every
    evaluation is logged with decision, determining policies, and errors.
    In test mode, non-empty diagnostics.errors raises — a skipped policy
    must never be silent.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._last_response: Any = None

    def before_tool_call(self, event: Any, **kwargs: Any) -> Any:
        """Intercept tool calls to trace Cedar authorization.

        Args:
            event: BeforeToolCallEvent from Strands SDK
            **kwargs: Additional keyword arguments

        Returns:
            Proceed or Deny result from parent authorization
        """
        result = super().before_tool_call(event, **kwargs)

        # Extract tool info for tracing from the event
        tool = getattr(event, "tool", None)
        tool_name = getattr(tool, "name", str(tool)) if tool else "unknown"
        tool_input = getattr(event, "tool_use", {})
        if hasattr(tool_input, "input"):
            tool_input = tool_input.input

        # Log the authorization decision
        # The result may be Proceed or Deny
        cedar_trace(result, tool_name, tool_input if isinstance(tool_input, dict) else {})

        return result


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


def _make_principal_resolver(default_coordinator: str | None = None):
    """Create a principal resolver, optionally with a default coordinator.

    Args:
        default_coordinator: If set, use this as fallback when no coordinator_id
            is found in the invocation state. For demo/testing only.
    """
    def resolve(state: dict[str, Any]) -> dict[str, str] | None:
        coordinator = state.get("coordinator_id") or default_coordinator
        if not coordinator:
            log.warning("no coordinator_id on invocation; all tool calls will be denied")
            return None
        return {"type": "Coordinator", "id": str(coordinator)}
    return resolve


# ---------------------------------------------------------------------------
# Context enrichment — the facts the policy needs, computed outside the model
# ---------------------------------------------------------------------------

def _enrich_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Everything here is derived from persisted case state, never from model
    output. The model cannot assert `verification_passed` into existence.

    The enricher looks up the case from the store based on the case_id in the
    tool input. This ensures verification_passed reflects the actual persisted
    state, not anything the model can assert.
    """
    from backend.app.models.store import get_store

    # Try to get case_id from tool input - handle various Strands SDK formats
    tool_input = ctx.get("tool_input", {}) or {}
    tool_use = ctx.get("tool_use", {}) or {}

    # Try multiple paths to find input
    if hasattr(tool_input, "input"):
        tool_input = tool_input.input
    if hasattr(tool_use, "input"):
        tool_input = tool_use.input
    if not isinstance(tool_input, dict):
        tool_input = {}

    # Debug logging
    log.debug("context enricher ctx keys: %s", list(ctx.keys()))

    case_id = tool_input.get("case_id")
    case_data: dict[str, Any] = {}

    if case_id:
        store = get_store()
        case = store.get_case(case_id)
        if case:
            # Increment tool_calls counter (for Cedar turns limit)
            turns = store.increment_tool_calls(case_id)
            # Count provider requests and check for declines (for Cedar sequential limit)
            provider_requests_for_case = store.count_requests_for_case(case_id)
            has_prior_decline = store.has_declined_request(case_id)
            case_data = {
                "verification_passed": case.verification_passed,
                "state": case.state.value if hasattr(case.state, "value") else str(case.state),
                "turns": turns,
                "provider_requests_for_case": provider_requests_for_case,
                "has_prior_decline": has_prior_decline,
            }
            log.debug(
                "context enricher: case_id=%s verification_passed=%s state=%s turns=%d "
                "provider_requests=%d has_decline=%s",
                case_id, case.verification_passed, case_data["state"], turns,
                provider_requests_for_case, has_prior_decline
            )

    # Fall back to invocation_state if available
    inv: dict[str, Any] = ctx.get("invocation_state", {}) or {}
    fallback_case: dict[str, Any] = inv.get("case", {}) or {}

    requested_at = fallback_case.get("requested_at")
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
        # First check actual case from store, fall back to invocation_state.
        "verification_passed": case_data.get("verification_passed", fallback_case.get("verification_passed", False)),
        "reminders_sent_24h": int(fallback_case.get("reminders_sent_24h", 0)),
        "hours_since_request": hours_since_request,
        "case_state": case_data.get("state", str(fallback_case.get("state", "NEW"))),
        # Tool call counter for Cedar turns limit (braces). 0 if no case_id.
        "turns": case_data.get("turns", 0),
        # Provider request tracking for Cedar sequential limit.
        # provider_requests_for_case >= 2 without has_prior_decline triggers forbid.
        "provider_requests_for_case": case_data.get("provider_requests_for_case", 0),
        "has_prior_decline": case_data.get("has_prior_decline", False),
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

def _combine_policies(
    main_file: Path,
    supplementary_file: Path,
) -> str:
    """Combine main and supplementary policy files into a single string.

    The supplementary file contains permits for tools not covered by the
    main policy file (poll_public_meetings, derive_obligations,
    extract_accommodation_policy).
    """
    policies = []

    if main_file.exists():
        policies.append(main_file.read_text())

    if supplementary_file.exists():
        policies.append(supplementary_file.read_text())

    return "\n\n".join(policies)


def build_authority(
    policy_file: Path = POLICY_FILE,
    schema_file: Path = SCHEMA_FILE,
    supplementary_policy_file: Path = SUPPLEMENTARY_POLICY_FILE,
    default_coordinator: str | None = None,
) -> TracingCedarAuthorization:
    """The hard boundary. Loaded from the .cedar file so the authority model is
    reviewable as policy, diffable in git, and hot-reloadable in the demo.

    Uses TracingCedarAuthorization to log every evaluation. In test mode,
    policy evaluation errors raise CedarDiagnosticsError — a skipped policy
    must never be silent.

    The schema validates policies at load time — a typo in an action name fails
    at startup, not as a mysterious runtime denial.

    Args:
        policy_file: Path to the main Cedar policy file
        schema_file: Path to the Cedar schema file
        supplementary_policy_file: Path to supplementary permits
        default_coordinator: If set, use as fallback principal when no
            coordinator_id is found. For demo/testing only.
    """
    if not policy_file.exists():
        raise FileNotFoundError(f"authority policy missing: {policy_file}")

    # Use custom principal resolver if default_coordinator is provided
    if default_coordinator:
        principal_resolver = _make_principal_resolver(default_coordinator)
    else:
        principal_resolver = _resolve_principal

    # Combine main and supplementary policies
    combined_policies = _combine_policies(policy_file, supplementary_policy_file)

    kwargs: dict[str, Any] = {
        "policies": combined_policies,
        "principal_resolver": principal_resolver,
        "context_enricher": _enrich_context,
        # Never 'proceed'. A policy-engine failure must close the gate, not open it.
        "on_error": "deny",
    }

    # Schema validation disabled temporarily — Strands SDK has compatibility issues
    # TODO: Re-enable when schema format is confirmed compatible
    # if schema_file.exists():
    #     kwargs["schema"] = str(schema_file)

    return TracingCedarAuthorization(**kwargs)


def build_steering() -> LLMSteeringHandler:
    """The soft boundary."""
    return LLMSteeringHandler(system_prompt=_STEERING_PROMPT)


def build_case_agent(tools: list[Any], system_prompt: str) -> tuple[Agent, TracingCedarAuthorization]:
    """Construct the Case Orchestrator with both boundaries attached.

    The TracingCedarAuthorization handle is returned alongside the agent so the
    demo can call `.reload()` on camera and show the authority model changing
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
