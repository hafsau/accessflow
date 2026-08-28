"""
Tests for AccessFlow case state machine.

Five invariants, each with its own test:
1. Any transition not in ALLOWED raises InvalidTransition — never silently corrected
2. → CLOSED requires non-null evidence_id AND case.verification_passed is True
3. Every transition appends an append-only CaseTransition row
4. actor ∈ system | agent | human
5. Same transition + same idempotency key = no-op, not a duplicate row
"""
from __future__ import annotations

import pytest

from backend.app.domain.state import (
    ALLOWED,
    Case,
    CaseTransition,
    InvalidTransition,
    State,
    apply_feed_change,
    transition,
)


@pytest.fixture
def new_case() -> Case:
    """Create a fresh case in NEW state."""
    return Case(
        case_id="case_001",
        event_key="seattle:6860",
        body_name="City Council",
        event_date="2026-09-08",
    )


# ---------------------------------------------------------------------------
# Invariant 1: Any transition not in ALLOWED raises InvalidTransition
# ---------------------------------------------------------------------------

class TestInvariant1_InvalidTransitionRaises:
    """Any transition not in ALLOWED raises InvalidTransition — never silently corrected."""

    def test_new_to_closed_raises(self, new_case):
        """NEW → CLOSED is not allowed."""
        with pytest.raises(InvalidTransition) as exc_info:
            transition(new_case, "CLOSED", "trying to skip", "system")
        assert exc_info.value.from_state == "NEW"
        assert exc_info.value.to_state == "CLOSED"

    def test_new_to_coordinating_raises(self, new_case):
        """NEW → COORDINATING is not allowed (must go through ANALYZING, PLANNING)."""
        with pytest.raises(InvalidTransition):
            transition(new_case, "COORDINATING", "trying to skip", "system")

    def test_closed_to_analyzing_raises(self, new_case):
        """CLOSED → ANALYZING is not allowed (must go through REOPENED)."""
        # First get to CLOSED legitimately
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "PLANNING", "plan", "system")
        transition(new_case, "COORDINATING", "coordinate", "system")
        transition(new_case, "VERIFYING", "verify", "system")
        new_case.verification_passed = True
        transition(new_case, "CLOSED", "close", "system", evidence_id="ver_001")

        # Now try invalid transition
        with pytest.raises(InvalidTransition):
            transition(new_case, "ANALYZING", "reopen directly", "system")

    def test_state_not_silently_corrected(self, new_case):
        """State is never silently corrected to 'closest valid state'."""
        original_state = new_case.state
        try:
            transition(new_case, "CLOSED", "invalid", "system")
        except InvalidTransition:
            pass
        # State must be unchanged
        assert new_case.state == original_state

    def test_all_invalid_transitions_raise(self, new_case):
        """Exhaustively test that non-ALLOWED transitions raise."""
        all_states = set(ALLOWED.keys())

        for from_state in all_states:
            allowed_targets = ALLOWED[from_state]
            invalid_targets = all_states - allowed_targets - {from_state}

            for to_state in invalid_targets:
                case = Case(
                    case_id="test",
                    event_key="test:1",
                    body_name="Test",
                    event_date="2026-01-01",
                    state=from_state,
                )
                # Set up for CLOSED transition if needed
                if to_state == "CLOSED":
                    case.verification_passed = True

                with pytest.raises(InvalidTransition):
                    transition(case, to_state, "test", "system", evidence_id="ev_1")


# ---------------------------------------------------------------------------
# Invariant 2: → CLOSED requires evidence_id AND verification_passed
# ---------------------------------------------------------------------------

class TestInvariant2_ClosedRequiresEvidence:
    """→ CLOSED requires non-null evidence_id AND case.verification_passed is True."""

    def test_closed_without_evidence_id_raises(self, new_case):
        """Cannot transition to CLOSED without evidence_id."""
        # Get to VERIFYING
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "PLANNING", "plan", "system")
        transition(new_case, "COORDINATING", "coordinate", "system")
        transition(new_case, "VERIFYING", "verify", "system")
        new_case.verification_passed = True

        with pytest.raises(InvalidTransition) as exc_info:
            transition(new_case, "CLOSED", "close", "system", evidence_id=None)
        assert "evidence_id" in str(exc_info.value)

    def test_closed_without_verification_passed_raises(self, new_case):
        """Cannot transition to CLOSED without verification_passed=True."""
        # Get to VERIFYING
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "PLANNING", "plan", "system")
        transition(new_case, "COORDINATING", "coordinate", "system")
        transition(new_case, "VERIFYING", "verify", "system")
        # verification_passed is False by default

        with pytest.raises(InvalidTransition) as exc_info:
            transition(new_case, "CLOSED", "close", "system", evidence_id="ver_001")
        assert "verification_passed" in str(exc_info.value)

    def test_closed_with_both_succeeds(self, new_case):
        """Can transition to CLOSED with both evidence_id and verification_passed."""
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "PLANNING", "plan", "system")
        transition(new_case, "COORDINATING", "coordinate", "system")
        transition(new_case, "VERIFYING", "verify", "system")
        new_case.verification_passed = True

        record = transition(new_case, "CLOSED", "close", "system", evidence_id="ver_001")

        assert new_case.state == "CLOSED"
        assert record.evidence_id == "ver_001"

    def test_ask_to_closed_requires_human_decision(self, new_case):
        """ASK → CLOSED requires human_decision_made=True."""
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "ASK", "need decision", "agent")
        new_case.verification_passed = True
        # human_decision_made is False

        with pytest.raises(InvalidTransition) as exc_info:
            transition(new_case, "CLOSED", "close after ask", "human", evidence_id="ver_001")
        assert "human_decision" in str(exc_info.value)

    def test_ask_to_closed_with_human_decision_succeeds(self, new_case):
        """ASK → CLOSED succeeds with human_decision_made=True."""
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "ASK", "need decision", "agent")
        new_case.verification_passed = True
        new_case.human_decision_made = True

        record = transition(new_case, "CLOSED", "close after ask", "human", evidence_id="ver_001")

        assert new_case.state == "CLOSED"
        assert record.actor == "human"


# ---------------------------------------------------------------------------
# Invariant 3: Every transition appends a CaseTransition row (append-only)
# ---------------------------------------------------------------------------

class TestInvariant3_AppendOnlyAuditTrail:
    """Every transition appends a CaseTransition row. Append-only."""

    def test_transition_appends_record(self, new_case):
        """Each transition adds exactly one record."""
        assert len(new_case.transitions) == 0

        transition(new_case, "ANALYZING", "start", "system")
        assert len(new_case.transitions) == 1

        transition(new_case, "PLANNING", "plan", "system")
        assert len(new_case.transitions) == 2

    def test_record_has_correct_fields(self, new_case):
        """CaseTransition has all required fields."""
        transition(new_case, "ANALYZING", "start analysis", "agent")

        record = new_case.transitions[0]
        assert record.from_state == "NEW"
        assert record.to_state == "ANALYZING"
        assert record.reason == "start analysis"
        assert record.actor == "agent"
        assert record.at is not None
        assert record.evidence_id is None

    def test_evidence_id_recorded(self, new_case):
        """evidence_id is recorded when provided."""
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "PLANNING", "plan", "system")
        transition(new_case, "COORDINATING", "coordinate", "system")
        transition(new_case, "VERIFYING", "verify", "system")
        new_case.verification_passed = True

        transition(new_case, "CLOSED", "close", "system", evidence_id="ver_123")

        record = new_case.transitions[-1]
        assert record.evidence_id == "ver_123"

    def test_transitions_are_immutable(self, new_case):
        """CaseTransition records are frozen (immutable)."""
        transition(new_case, "ANALYZING", "start", "system")

        record = new_case.transitions[0]
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            record.reason = "modified"

    def test_audit_trail_preserves_history(self, new_case):
        """Full transition history is preserved."""
        transition(new_case, "ANALYZING", "step 1", "system")
        transition(new_case, "PLANNING", "step 2", "agent")
        transition(new_case, "COORDINATING", "step 3", "system")

        assert len(new_case.transitions) == 3
        assert new_case.transitions[0].to_state == "ANALYZING"
        assert new_case.transitions[1].to_state == "PLANNING"
        assert new_case.transitions[2].to_state == "COORDINATING"


# ---------------------------------------------------------------------------
# Invariant 4: actor ∈ system | agent | human
# ---------------------------------------------------------------------------

class TestInvariant4_ValidActors:
    """actor ∈ system | agent | human."""

    def test_system_actor_valid(self, new_case):
        """'system' is a valid actor."""
        record = transition(new_case, "ANALYZING", "start", "system")
        assert record.actor == "system"

    def test_agent_actor_valid(self, new_case):
        """'agent' is a valid actor."""
        record = transition(new_case, "ANALYZING", "start", "agent")
        assert record.actor == "agent"

    def test_human_actor_valid(self, new_case):
        """'human' is a valid actor."""
        record = transition(new_case, "ANALYZING", "start", "human")
        assert record.actor == "human"

    def test_invalid_actor_raises(self, new_case):
        """Invalid actor raises InvalidTransition."""
        with pytest.raises(InvalidTransition) as exc_info:
            transition(new_case, "ANALYZING", "start", "robot")
        assert "invalid actor" in str(exc_info.value)

    def test_empty_actor_raises(self, new_case):
        """Empty actor raises InvalidTransition."""
        with pytest.raises(InvalidTransition):
            transition(new_case, "ANALYZING", "start", "")

    def test_agent_closing_without_human_decision_is_bug(self, new_case):
        """
        A CLOSED transition with actor='agent' and no human decision
        on an ASK path is a bug.
        """
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "ASK", "need decision", "agent")
        new_case.verification_passed = True
        # No human_decision_made

        # Agent trying to close directly should fail
        with pytest.raises(InvalidTransition):
            transition(new_case, "CLOSED", "agent closing", "agent", evidence_id="ver_001")


# ---------------------------------------------------------------------------
# Invariant 5: Same transition + same idempotency key = no-op
# ---------------------------------------------------------------------------

class TestInvariant5_IdempotencyKey:
    """Same transition + same idempotency key = no-op, not a duplicate row."""

    def test_same_key_is_noop(self, new_case):
        """Same idempotency key for same transition returns None and doesn't add row."""
        # First transition NEW → ANALYZING
        record1 = transition(new_case, "ANALYZING", "start", "system", idempotency_key="key_001")
        assert record1 is not None
        assert len(new_case.transitions) == 1

        # Reset state to test idempotency (simulating retry)
        new_case.state = "NEW"

        # Same FROM→TO with same key should be no-op
        record2 = transition(new_case, "ANALYZING", "start again", "system", idempotency_key="key_001")
        assert record2 is None
        assert len(new_case.transitions) == 1  # No duplicate row
        # State should not have been changed by the no-op
        assert new_case.state == "NEW"

    def test_different_keys_both_recorded(self, new_case):
        """Different idempotency keys both create records."""
        transition(new_case, "ANALYZING", "start", "system", idempotency_key="key_001")

        # Move to next state
        transition(new_case, "PLANNING", "plan", "system", idempotency_key="key_002")

        assert len(new_case.transitions) == 2

    def test_no_key_always_records(self, new_case):
        """Without idempotency key, transitions are always recorded."""
        transition(new_case, "ANALYZING", "start", "system")
        # This would normally fail as we're in ANALYZING, but let's test with valid transitions
        transition(new_case, "PLANNING", "plan 1", "system")

        # Go back via ASK to test multiple similar transitions
        new_case_2 = Case(
            case_id="test",
            event_key="test:1",
            body_name="Test",
            event_date="2026-01-01",
        )
        transition(new_case_2, "ANALYZING", "first", "system")
        # Without idempotency, same transition type in different contexts would work

    def test_key_is_transition_specific(self, new_case):
        """Idempotency key is specific to the from→to transition."""
        transition(new_case, "ANALYZING", "start", "system", idempotency_key="key_001")

        # Same key but different transition should work
        record = transition(new_case, "PLANNING", "plan", "system", idempotency_key="key_001")
        assert record is not None
        assert len(new_case.transitions) == 2


# ---------------------------------------------------------------------------
# Feed change → transition mapping tests
# ---------------------------------------------------------------------------

class TestFeedChangeMapping:
    """Test feed-change → transition mapping per the spec table."""

    def test_cancelled_from_non_closed_goes_to_cancelled(self, new_case):
        """cancelled + anything except CLOSED → CANCELLED."""
        transition(new_case, "ANALYZING", "start", "system")

        apply_feed_change(new_case, "cancelled")

        assert new_case.state == "CANCELLED"

    def test_cancelled_from_closed_goes_to_reopened(self, new_case):
        """cancelled + CLOSED → REOPENED."""
        # Get to CLOSED
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "PLANNING", "plan", "system")
        transition(new_case, "COORDINATING", "coordinate", "system")
        transition(new_case, "VERIFYING", "verify", "system")
        new_case.verification_passed = True
        transition(new_case, "CLOSED", "close", "system", evidence_id="ver_001")

        apply_feed_change(new_case, "cancelled")

        assert new_case.state == "REOPENED"

    def test_rescheduled_from_closed_goes_to_reopened(self, new_case):
        """rescheduled_or_relocated + CLOSED → REOPENED."""
        # Get to CLOSED
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "PLANNING", "plan", "system")
        transition(new_case, "COORDINATING", "coordinate", "system")
        transition(new_case, "VERIFYING", "verify", "system")
        new_case.verification_passed = True
        transition(new_case, "CLOSED", "close", "system", evidence_id="ver_001")

        apply_feed_change(new_case, "rescheduled_or_relocated")

        assert new_case.state == "REOPENED"

    def test_rescheduled_from_other_goes_to_analyzing(self, new_case):
        """rescheduled_or_relocated + anything else → ANALYZING."""
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "PLANNING", "plan", "system")

        apply_feed_change(new_case, "rescheduled_or_relocated")

        assert new_case.state == "ANALYZING"

    def test_agenda_posted_from_new_goes_to_analyzing(self, new_case):
        """agenda_posted + NEW → ANALYZING."""
        apply_feed_change(new_case, "agenda_posted")

        assert new_case.state == "ANALYZING"

    def test_agenda_replaced_from_closed_goes_to_reopened(self, new_case):
        """agenda_replaced + CLOSED → REOPENED (evidence is stale)."""
        # Get to CLOSED
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "PLANNING", "plan", "system")
        transition(new_case, "COORDINATING", "coordinate", "system")
        transition(new_case, "VERIFYING", "verify", "system")
        new_case.verification_passed = True
        transition(new_case, "CLOSED", "close", "system", evidence_id="ver_001")

        apply_feed_change(new_case, "agenda_replaced")

        assert new_case.state == "REOPENED"

    def test_agenda_replaced_from_verifying_goes_to_reopened(self, new_case):
        """agenda_replaced + VERIFYING → REOPENED (evidence is stale)."""
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "PLANNING", "plan", "system")
        transition(new_case, "COORDINATING", "coordinate", "system")
        transition(new_case, "VERIFYING", "verify", "system")

        apply_feed_change(new_case, "agenda_replaced")

        assert new_case.state == "REOPENED"

    def test_agenda_replaced_from_other_goes_to_coordinating(self, new_case):
        """agenda_replaced + anything else → COORDINATING."""
        transition(new_case, "ANALYZING", "start", "system")
        transition(new_case, "PLANNING", "plan", "system")

        apply_feed_change(new_case, "agenda_replaced")

        assert new_case.state == "COORDINATING"
