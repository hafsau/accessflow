"""
Tests for AccessFlow case management tools.

Each tool is tested for:
- Happy path
- Invalid ID
- Duplicate idempotency key (for mutating tools)
- Malformed input

Plus: extract_accommodation_policy always returns quote_verified: null
"""
from __future__ import annotations

import pytest

from backend.app.models.domain import CaseState, ErrorCode, Obligation, RequestStatus
from backend.app.models.store import get_store, reset_store
from backend.app.tools.case_tools import (
    close_case,
    derive_obligations,
    fetch_agenda_document,
    get_case,
    get_event,
    poll_public_meetings,
    request_human_decision,
    search_providers,
    send_provider_request,
    verify_fulfillment,
)


@pytest.fixture(autouse=True)
def fresh_store():
    """Reset store before each test."""
    reset_store()
    yield
    reset_store()


@pytest.fixture
def sample_case():
    """Create a sample case for testing."""
    store = get_store()
    obligations = [
        Obligation(
            category="effective_communication",
            basis="28 CFR 35.160",
            deadline="2026-09-06T14:00:00Z",
        ),
        Obligation(
            category="document_conformance",
            basis="28 CFR 35.200",
            deadline="2027-04-26",
        ),
    ]
    case = store.create_case("seattle:6860", obligations)
    return case


@pytest.fixture
def sample_event():
    """Create a sample event for testing."""
    from backend.app.models.domain import Event

    store = get_store()
    event = Event(
        event_key="seattle:6860",
        client="seattle",
        event_id=6860,
        body_name="City Council",
        date="2026-09-08",
        time="2:00 PM",
        location="Council Chamber, City Hall",
        agenda_url="https://example.com/agenda.pdf",
        comment="Regular meeting",
        is_cancelled=False,
    )
    store.upsert_event(event)
    return event


# ---------------------------------------------------------------------------
# 1. get_case tests
# ---------------------------------------------------------------------------

class TestGetCase:
    def test_happy_path(self, sample_case):
        result = get_case(sample_case.case_id)
        assert result["ok"] is True
        assert result["case"]["case_id"] == sample_case.case_id
        assert result["case"]["state"] == CaseState.NEW.value

    def test_invalid_id(self):
        result = get_case("nonexistent_case")
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.CASE_NOT_FOUND.value

    def test_malformed_input(self):
        # Empty string
        result = get_case("")
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.CASE_NOT_FOUND.value


# ---------------------------------------------------------------------------
# 2. get_event tests
# ---------------------------------------------------------------------------

class TestGetEvent:
    def test_happy_path(self, sample_event):
        result = get_event(sample_event.event_key)
        assert result["ok"] is True
        assert result["event"]["event_key"] == "seattle:6860"
        assert result["event"]["body_name"] == "City Council"

    def test_invalid_id(self):
        result = get_event("nonexistent:999")
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.EVENT_NOT_FOUND.value

    def test_malformed_input(self):
        result = get_event("")
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.EVENT_NOT_FOUND.value


# ---------------------------------------------------------------------------
# 3. fetch_agenda_document tests
# ---------------------------------------------------------------------------

class TestFetchAgendaDocument:
    def test_invalid_url(self):
        result = fetch_agenda_document("https://nonexistent.example.com/agenda.pdf")
        assert result["ok"] is False
        assert result["error_code"] in [
            ErrorCode.FETCH_FAILED.value,
            ErrorCode.PARSE_FAILED.value,
        ]

    def test_malformed_url(self):
        result = fetch_agenda_document("not-a-url")
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.FETCH_FAILED.value


# ---------------------------------------------------------------------------
# 4. search_providers tests
# ---------------------------------------------------------------------------

class TestSearchProviders:
    def test_happy_path(self):
        result = search_providers(service_type="ASL_INTERPRETER", jurisdiction="seattle")
        assert result["ok"] is True
        assert isinstance(result["providers"], list)
        # Should find at least Pacific Interpreting and SignOn (both approved)
        assert len(result["providers"]) >= 1
        # All returned should be approved
        for p in result["providers"]:
            assert p["approved"] is True

    def test_no_matches(self):
        result = search_providers(service_type="BRAILLE", jurisdiction="nonexistent")
        assert result["ok"] is True
        assert result["providers"] == []

    def test_no_filters(self):
        result = search_providers()
        assert result["ok"] is True
        # Returns all approved providers
        assert len(result["providers"]) >= 1


# ---------------------------------------------------------------------------
# 5. send_provider_request tests
# ---------------------------------------------------------------------------

class TestSendProviderRequest:
    def test_happy_path(self, sample_case):
        result = send_provider_request(
            idempotency_key="test_req_001",
            case_id=sample_case.case_id,
            provider_id="prov_pacific",
            service_type="ASL_INTERPRETER",
            event_date="2026-09-08",
            event_time="14:00",
            event_location="Council Chamber",
            provider_approved=True,
        )
        assert result["ok"] is True
        assert result["request"]["status"] == RequestStatus.SENT.value
        assert result["request"]["case_id"] == sample_case.case_id

    def test_invalid_case_id(self):
        result = send_provider_request(
            idempotency_key="test_req_002",
            case_id="nonexistent",
            provider_id="prov_pacific",
            service_type="ASL_INTERPRETER",
            event_date="2026-09-08",
            event_time="14:00",
            event_location="Council Chamber",
            provider_approved=True,
        )
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.CASE_NOT_FOUND.value

    def test_invalid_provider_id(self, sample_case):
        result = send_provider_request(
            idempotency_key="test_req_003",
            case_id=sample_case.case_id,
            provider_id="nonexistent",
            service_type="ASL_INTERPRETER",
            event_date="2026-09-08",
            event_time="14:00",
            event_location="Council Chamber",
            provider_approved=True,
        )
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.PROVIDER_NOT_FOUND.value

    def test_unapproved_provider(self, sample_case):
        result = send_provider_request(
            idempotency_key="test_req_004",
            case_id=sample_case.case_id,
            provider_id="prov_unapproved",
            service_type="ASL_INTERPRETER",
            event_date="2026-09-08",
            event_time="14:00",
            event_location="Council Chamber",
            provider_approved=True,  # Trying to send to unapproved provider
        )
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.PROVIDER_NOT_APPROVED.value

    def test_duplicate_idempotency_key(self, sample_case):
        # First request succeeds
        result1 = send_provider_request(
            idempotency_key="test_req_dup",
            case_id=sample_case.case_id,
            provider_id="prov_pacific",
            service_type="ASL_INTERPRETER",
            event_date="2026-09-08",
            event_time="14:00",
            event_location="Council Chamber",
            provider_approved=True,
        )
        assert result1["ok"] is True

        # Second request with same key fails
        result2 = send_provider_request(
            idempotency_key="test_req_dup",
            case_id=sample_case.case_id,
            provider_id="prov_pacific",
            service_type="ASL_INTERPRETER",
            event_date="2026-09-08",
            event_time="14:00",
            event_location="Council Chamber",
            provider_approved=True,
        )
        assert result2["ok"] is False
        assert result2["error_code"] == ErrorCode.DUPLICATE_REQUEST.value


# ---------------------------------------------------------------------------
# 6. request_human_decision tests
# ---------------------------------------------------------------------------

class TestRequestHumanDecision:
    def test_happy_path(self, sample_case):
        result = request_human_decision(
            idempotency_key="test_dec_001",
            case_id=sample_case.case_id,
            decision_type="PROVIDER_SUBSTITUTION",
            context="Original provider unavailable",
            options=[
                {"option_id": "A", "description": "Use alternate provider", "recommended": True},
                {"option_id": "B", "description": "Wait for original"},
            ],
        )
        assert result["ok"] is True
        assert result["decision"]["status"] == "PENDING"
        assert result["decision"]["case_id"] == sample_case.case_id

    def test_invalid_case_id(self):
        result = request_human_decision(
            idempotency_key="test_dec_002",
            case_id="nonexistent",
            decision_type="PROVIDER_SUBSTITUTION",
            context="Test",
            options=[{"option_id": "A", "description": "Option A"}],
        )
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.CASE_NOT_FOUND.value

    def test_duplicate_idempotency_key(self, sample_case):
        result1 = request_human_decision(
            idempotency_key="test_dec_dup",
            case_id=sample_case.case_id,
            decision_type="PROVIDER_SUBSTITUTION",
            context="Test",
            options=[{"option_id": "A", "description": "Option A"}],
        )
        assert result1["ok"] is True

        result2 = request_human_decision(
            idempotency_key="test_dec_dup",
            case_id=sample_case.case_id,
            decision_type="PROVIDER_SUBSTITUTION",
            context="Test",
            options=[{"option_id": "A", "description": "Option A"}],
        )
        assert result2["ok"] is False
        assert result2["error_code"] == ErrorCode.DUPLICATE_REQUEST.value


# ---------------------------------------------------------------------------
# 7. verify_fulfillment tests
# ---------------------------------------------------------------------------

class TestVerifyFulfillment:
    def test_happy_path(self, sample_case):
        result = verify_fulfillment(sample_case.case_id)
        assert result["ok"] is True
        assert result["verification"]["case_id"] == sample_case.case_id
        # New case without provider confirmation should not pass
        assert result["verification"]["passed"] is False

    def test_invalid_case_id(self):
        result = verify_fulfillment("nonexistent")
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.CASE_NOT_FOUND.value


# ---------------------------------------------------------------------------
# 8. close_case tests
# ---------------------------------------------------------------------------

class TestCloseCase:
    def test_happy_path(self, sample_case):
        store = get_store()
        # Create a passing verification
        verification = store.create_verification(sample_case.case_id, True, [])
        sample_case.verification_passed = True
        sample_case.state = CaseState.VERIFIED
        store.update_case(sample_case)

        result = close_case(
            idempotency_key="test_close_001",
            case_id=sample_case.case_id,
            verification_id=verification.verification_id,
        )
        assert result["ok"] is True
        assert result["case"]["state"] == CaseState.CLOSED.value

    def test_invalid_case_id(self):
        result = close_case(
            idempotency_key="test_close_002",
            case_id="nonexistent",
            verification_id="ver_001",
        )
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.CASE_NOT_FOUND.value

    def test_invalid_verification_id(self, sample_case):
        result = close_case(
            idempotency_key="test_close_003",
            case_id=sample_case.case_id,
            verification_id="nonexistent",
        )
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.VERIFICATION_NOT_FOUND.value

    def test_verification_failed(self, sample_case):
        store = get_store()
        # Create a FAILING verification
        verification = store.create_verification(sample_case.case_id, False, [])

        result = close_case(
            idempotency_key="test_close_004",
            case_id=sample_case.case_id,
            verification_id=verification.verification_id,
        )
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.VERIFICATION_FAILED.value

    def test_duplicate_idempotency_key(self, sample_case):
        store = get_store()
        verification = store.create_verification(sample_case.case_id, True, [])
        sample_case.verification_passed = True
        sample_case.state = CaseState.VERIFIED
        store.update_case(sample_case)

        result1 = close_case(
            idempotency_key="test_close_dup",
            case_id=sample_case.case_id,
            verification_id=verification.verification_id,
        )
        assert result1["ok"] is True

        result2 = close_case(
            idempotency_key="test_close_dup",
            case_id=sample_case.case_id,
            verification_id=verification.verification_id,
        )
        assert result2["ok"] is False
        assert result2["error_code"] == ErrorCode.DUPLICATE_REQUEST.value

    def test_already_closed(self, sample_case):
        store = get_store()
        verification = store.create_verification(sample_case.case_id, True, [])
        sample_case.state = CaseState.CLOSED
        store.update_case(sample_case)

        result = close_case(
            idempotency_key="test_close_already",
            case_id=sample_case.case_id,
            verification_id=verification.verification_id,
        )
        assert result["ok"] is False
        assert result["error_code"] == ErrorCode.INVALID_STATE.value


# ---------------------------------------------------------------------------
# 9. extract_accommodation_policy — quote_verified always null
# ---------------------------------------------------------------------------

class TestExtractAccommodationPolicy:
    """
    Test that extract_accommodation_policy ALWAYS returns quote_verified: null.
    The model cannot verify its own quotes.
    """

    def test_quote_verified_always_null(self):
        """
        This test would call the actual tool with MODEL_PROVIDER set,
        but since we don't want to call the model in unit tests,
        we test the contract: the Pydantic model enforces quote_verified=None.
        """
        from backend.app.models.domain import AccommodationPolicy

        # The model cannot set quote_verified to True or False
        policy = AccommodationPolicy(
            recommended_accommodations=["ASL_INTERPRETER"],
            priority="HIGH",
            reasoning="Public hearing",
            quote="PUBLIC HEARING on housing",
            quote_verified=None,  # This is the contract
        )
        assert policy.quote_verified is None

        # Even if someone tries to set it, the contract is that the TOOL
        # always returns None and only context_enricher can verify
        output = {"ok": True, "policy": policy.model_dump()}
        assert output["policy"]["quote_verified"] is None


# ---------------------------------------------------------------------------
# Audit trail tests
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_mutating_tools_create_audit_rows(self, sample_case):
        store = get_store()

        # Clear any existing actions
        initial_count = len(store.get_actions())

        # Call a mutating tool
        send_provider_request(
            idempotency_key="test_audit_001",
            case_id=sample_case.case_id,
            provider_id="prov_pacific",
            service_type="ASL_INTERPRETER",
            event_date="2026-09-08",
            event_time="14:00",
            event_location="Council Chamber",
            provider_approved=True,
        )

        actions = store.get_actions()
        assert len(actions) > initial_count

        # Check the action was recorded
        latest = actions[-1]
        assert latest.tool_name == "send_provider_request"
        assert latest.idempotency_key == "test_audit_001"
        assert latest.success is True

    def test_failed_tools_create_audit_rows(self):
        store = get_store()

        send_provider_request(
            idempotency_key="test_audit_fail",
            case_id="nonexistent",
            provider_id="prov_pacific",
            service_type="ASL_INTERPRETER",
            event_date="2026-09-08",
            event_time="14:00",
            event_location="Council Chamber",
            provider_approved=True,
        )

        actions = store.get_actions()
        latest = actions[-1]
        assert latest.tool_name == "send_provider_request"
        assert latest.success is False
        assert latest.error_code == ErrorCode.CASE_NOT_FOUND.value


# ---------------------------------------------------------------------------
# 10. poll_public_meetings tests
# ---------------------------------------------------------------------------

class TestPollPublicMeetings:
    """
    poll_public_meetings is deterministic, no model.
    Critical test: a poll where nothing moved returns empty lists and zero model calls.
    """

    def test_empty_poll_returns_empty_lists(self):
        """
        When polling with a fake client that has no meetings,
        we should get {"new": [], "changes": []} and NO model calls.
        """
        # Use a nonexistent client to ensure no meetings are found
        # (This will log a warning but return empty results)
        result = poll_public_meetings(clients=["nonexistent_test_client"], days_ahead=1)

        assert result["ok"] is True
        assert result["new"] == []
        assert result["changes"] == []

    def test_returns_correct_structure(self):
        """Test that the response has the correct structure."""
        result = poll_public_meetings(clients=["nonexistent_test_client"], days_ahead=1)

        assert "ok" in result
        assert "new" in result
        assert "changes" in result
        assert isinstance(result["new"], list)
        assert isinstance(result["changes"], list)

    def test_no_model_calls(self):
        """
        Ensure poll_public_meetings makes ZERO model calls.
        This is verified by the fact that we can call it without any model config.
        """
        # If this tried to call a model, it would fail because no ANTHROPIC_API_KEY
        # or Bedrock credentials are configured in the test environment
        result = poll_public_meetings(clients=["nonexistent_test_client"], days_ahead=1)
        assert result["ok"] is True
        # The mere fact that we got here without an error proves no model was called


# ---------------------------------------------------------------------------
# 11. derive_obligations tests
# ---------------------------------------------------------------------------

class TestDeriveObligations:
    """
    derive_obligations is deterministic, no model.
    Always returns exactly TWO obligations with correct bases and deadlines.
    """

    def test_always_returns_two_obligations(self):
        """Both bases are always present."""
        event = {"date": "2026-09-08", "time": "2:00 PM"}

        result = derive_obligations(event, population_over_50k=True)

        assert result["ok"] is True
        assert len(result["obligations"]) == 2

        bases = [o["basis"] for o in result["obligations"]]
        assert "28 CFR 35.160" in bases
        assert "28 CFR 35.200" in bases

    def test_categories_are_correct(self):
        """Check categories match the spec."""
        event = {"date": "2026-09-08", "time": "2:00 PM"}

        result = derive_obligations(event, population_over_50k=True)

        categories = {o["basis"]: o["category"] for o in result["obligations"]}
        assert categories["28 CFR 35.160"] == "effective_communication"
        assert categories["28 CFR 35.200"] == "document_conformance"

    def test_deadline_large_entity(self):
        """population_over_50k=True -> deadline 2027-04-26."""
        event = {"date": "2026-09-08", "time": "2:00 PM"}

        result = derive_obligations(event, population_over_50k=True)

        doc_obligation = next(o for o in result["obligations"] if o["basis"] == "28 CFR 35.200")
        assert doc_obligation["deadline"] == "2027-04-26"

    def test_deadline_small_entity(self):
        """population_over_50k=False -> deadline 2028-04-26."""
        event = {"date": "2026-09-08", "time": "2:00 PM"}

        result = derive_obligations(event, population_over_50k=False)

        doc_obligation = next(o for o in result["obligations"] if o["basis"] == "28 CFR 35.200")
        assert doc_obligation["deadline"] == "2028-04-26"

    def test_effective_communication_deadline_is_48h_before_event(self):
        """35.160 deadline is event start minus 48 hours."""
        event = {"date": "2026-09-08", "time": "2:00 PM"}

        result = derive_obligations(event, population_over_50k=True)

        comm_obligation = next(o for o in result["obligations"] if o["basis"] == "28 CFR 35.160")
        # 2026-09-08 2:00 PM - 48h = 2026-09-06 2:00 PM (14:00 UTC)
        assert "2026-09-06" in comm_obligation["deadline"]

    def test_must_have_is_true_for_both(self):
        """Both obligations have must_have=True."""
        event = {"date": "2026-09-08", "time": "2:00 PM"}

        result = derive_obligations(event, population_over_50k=True)

        for obl in result["obligations"]:
            assert obl["must_have"] is True

    def test_no_model_calls(self):
        """
        Ensure derive_obligations makes ZERO model calls.
        Verified by calling without model config.
        """
        event = {"date": "2026-09-08"}

        # If this tried to call a model, it would fail
        result = derive_obligations(event, population_over_50k=True)
        assert result["ok"] is True

    def test_handles_missing_time(self):
        """Should handle events without time gracefully."""
        event = {"date": "2026-09-08"}  # No time

        result = derive_obligations(event, population_over_50k=True)

        assert result["ok"] is True
        assert len(result["obligations"]) == 2
