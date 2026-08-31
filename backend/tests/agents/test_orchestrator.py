"""
Orchestrator tests — turn limits and escalation behavior.

These tests verify:
1. The agent is called with limits={"turns": 12}
2. When limit_turns is hit, request_human_decision is called
3. The Cedar forbid rule blocks tool calls after 15 turns
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Test: Agent called with turn limit
# ---------------------------------------------------------------------------


def test_run_case_passes_turn_limit_to_agent():
    """The orchestrator must pass limits={"turns": 12} to the agent."""
    from backend.app.agents.orchestrator import MAX_TURNS, run_case

    # Create mocks
    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.stop_reason = "end_turn"  # Normal completion
    mock_agent.return_value = mock_result

    mock_budgeted_model = MagicMock()
    mock_budgeted_model.model_calls = 5
    mock_budgeted_model.total_in_tokens = 10000
    mock_budgeted_model.total_out_tokens = 2000

    meeting = {
        "key": "seattle:123",
        "body_name": "Seattle City Council",
        "date": "2026-09-01",
    }

    with patch("backend.app.agents.orchestrator.spent_today", return_value=0.50):
        result = run_case(mock_agent, meeting, mock_budgeted_model)

    # Verify agent was called with limits
    mock_agent.assert_called_once()
    call_kwargs = mock_agent.call_args.kwargs
    assert "limits" in call_kwargs, "limits not passed to agent"
    assert call_kwargs["limits"]["turns"] == MAX_TURNS


def test_run_case_escalates_on_turn_limit():
    """When the agent hits the turn limit, it must escalate to request_human_decision."""
    from backend.app.agents.orchestrator import MAX_TURNS, run_case

    # Create mocks
    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.stop_reason = "limit_turns"  # Hit the limit
    mock_agent.return_value = mock_result

    mock_budgeted_model = MagicMock()
    mock_budgeted_model.model_calls = 12
    mock_budgeted_model.total_in_tokens = 50000
    mock_budgeted_model.total_out_tokens = 10000

    meeting = {
        "key": "seattle:456",
        "body_name": "Seattle City Council",
        "date": "2026-09-15",
        "case_id": "case_test_123",
    }

    with patch("backend.app.agents.orchestrator.spent_today", return_value=1.50):
        with patch(
            "backend.app.tools.case_tools.request_human_decision"
        ) as mock_escalate:
            mock_escalate.return_value = {"ok": True, "decision": {"decision_id": "dec_001"}}
            result = run_case(mock_agent, meeting, mock_budgeted_model)

    # Verify escalation was called
    mock_escalate.assert_called_once()
    call_kwargs = mock_escalate.call_args.kwargs

    assert call_kwargs["decision_type"] == "TURN_LIMIT_EXCEEDED"
    assert str(MAX_TURNS) in call_kwargs["context"]
    assert "idempotency_key" in call_kwargs

    # Verify result contains escalation info
    assert result["stop_reason"] == "limit_turns"
    assert "escalation" in result


def test_max_turns_constant():
    """MAX_TURNS must be 12 as specified in the requirements."""
    from backend.app.agents.orchestrator import MAX_TURNS
    assert MAX_TURNS == 12


# ---------------------------------------------------------------------------
# Test: Cedar forbid at turns > 15 (braces)
# ---------------------------------------------------------------------------


def test_cedar_denies_after_15_tool_calls():
    """The Cedar forbid rule must block tool calls after 15 turns.

    This is the "braces" safety net that catches any bypass of the SDK limit.
    """
    from backend.app.models.store import reset_store, get_store
    from backend.app.models.domain import CaseState

    # Reset to fresh store
    reset_store()
    store = get_store()

    # Create a case
    case = store.create_case("event_001", [])
    case_id = case.case_id

    # Simulate 15 tool calls
    for i in range(15):
        count = store.increment_tool_calls(case_id)
        assert count == i + 1

    # After 15 calls, turns should be 15
    updated_case = store.get_case(case_id)
    assert updated_case.tool_calls == 15

    # The context enricher should return turns=16 on the next tool call
    # (it increments before returning)
    count = store.increment_tool_calls(case_id)
    assert count == 16

    # Verify the Cedar policy would deny this
    # We test the context enricher output, not the actual Cedar evaluation
    # (that requires the full Strands SDK)
    from backend.app.agents.authority import _enrich_context

    # Simulate a tool call context with this case
    ctx = {
        "tool_input": {"case_id": case_id},
        "invocation_state": {},
    }

    # The enricher increments again, so turns would be 17
    enriched = _enrich_context(ctx)
    assert enriched["turns"] == 17, f"Expected turns=17, got {enriched['turns']}"

    # The Cedar policy: forbid when context.session.turns > 15
    # With turns=17, this would be denied
    assert enriched["turns"] > 15, "Turns should exceed Cedar limit"


def test_tool_calls_tracked_per_case():
    """Tool calls must be tracked per case, not globally."""
    from backend.app.models.store import reset_store, get_store

    reset_store()
    store = get_store()

    # Create two cases
    case1 = store.create_case("event_001", [])
    case2 = store.create_case("event_002", [])

    # Increment case1 a few times
    for _ in range(5):
        store.increment_tool_calls(case1.case_id)

    # Increment case2 once
    store.increment_tool_calls(case2.case_id)

    # Verify counts are independent
    assert store.get_case(case1.case_id).tool_calls == 5
    assert store.get_case(case2.case_id).tool_calls == 1


# ---------------------------------------------------------------------------
# Test: Limits type validation
# ---------------------------------------------------------------------------


def test_limits_type_has_turns():
    """The Strands Limits TypedDict must have a 'turns' key."""
    from strands.types.agent import Limits

    # TypedDict annotations should include 'turns'
    assert hasattr(Limits, "__annotations__")
    assert "turns" in Limits.__annotations__
    assert Limits.__annotations__["turns"] == int


# ---------------------------------------------------------------------------
# Test: Sequential provider contact
# ---------------------------------------------------------------------------


def test_sequential_provider_one_request_when_confirmed():
    """One case produces at most one send_provider_request when the first confirms.

    The orchestrator should contact ONE provider for §35.160, not multiple.
    This test verifies the store correctly tracks provider requests per case.
    """
    from backend.app.models.store import reset_store, get_store
    from backend.app.models.domain import RequestStatus

    reset_store()
    store = get_store()

    # Create a case
    case = store.create_case("event_001", [])
    case_id = case.case_id

    # Initially, no requests
    assert store.count_requests_for_case(case_id) == 0
    assert not store.has_declined_request(case_id)

    # Send first provider request
    req1 = store.create_request(case_id, "prov_pacific")
    assert store.count_requests_for_case(case_id) == 1
    assert not store.has_declined_request(case_id)

    # Simulate confirmation — the request status is updated
    req1.status = RequestStatus.CONFIRMED

    # With one confirmed request, count is 1, no decline
    assert store.count_requests_for_case(case_id) == 1
    assert not store.has_declined_request(case_id)

    # The context enricher should report these values
    from backend.app.agents.authority import _enrich_context

    ctx = {"tool_input": {"case_id": case_id}, "invocation_state": {}}
    enriched = _enrich_context(ctx)

    assert enriched["provider_requests_for_case"] == 1
    assert enriched["has_prior_decline"] is False


def test_sequential_provider_second_request_after_decline():
    """A second provider request is allowed after the first declines."""
    from backend.app.models.store import reset_store, get_store
    from backend.app.models.domain import RequestStatus

    reset_store()
    store = get_store()

    # Create a case
    case = store.create_case("event_002", [])
    case_id = case.case_id

    # Send first request
    req1 = store.create_request(case_id, "prov_pacific")
    assert store.count_requests_for_case(case_id) == 1

    # First provider declines
    req1.status = RequestStatus.DECLINED

    # Now has_prior_decline should be True
    assert store.has_declined_request(case_id)

    # Context enricher should reflect this
    from backend.app.agents.authority import _enrich_context

    ctx = {"tool_input": {"case_id": case_id}, "invocation_state": {}}
    enriched = _enrich_context(ctx)

    assert enriched["provider_requests_for_case"] == 1
    assert enriched["has_prior_decline"] is True

    # A second request should now be allowed (Cedar would permit)
    req2 = store.create_request(case_id, "prov_signon")
    assert store.count_requests_for_case(case_id) == 2
    # Still has decline from req1
    assert store.has_declined_request(case_id)


def test_sequential_provider_cedar_blocks_parallel_requests():
    """Cedar blocks a third provider request without a prior decline.

    This tests the context enricher values that Cedar uses for the forbid rule:
    forbid send_provider_request when provider_requests_for_case >= 2
    unless has_prior_decline == true
    """
    from backend.app.models.store import reset_store, get_store
    from backend.app.models.domain import RequestStatus

    reset_store()
    store = get_store()

    # Create a case and send TWO requests without any declines
    case = store.create_case("event_003", [])
    case_id = case.case_id

    req1 = store.create_request(case_id, "prov_pacific")
    req2 = store.create_request(case_id, "prov_signon")

    # Both are SENT (not declined)
    assert req1.status == RequestStatus.SENT
    assert req2.status == RequestStatus.SENT

    # Context enricher should show 2 requests, no decline
    from backend.app.agents.authority import _enrich_context

    ctx = {"tool_input": {"case_id": case_id}, "invocation_state": {}}
    enriched = _enrich_context(ctx)

    assert enriched["provider_requests_for_case"] == 2
    assert enriched["has_prior_decline"] is False

    # Cedar rule: forbid when provider_requests_for_case >= 2 unless has_prior_decline
    # With 2 requests and no decline, a third request would be blocked
    # (The actual Cedar evaluation happens in the intervention, this test verifies the inputs)
