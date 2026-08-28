"""
AccessFlow case state machine — ten states, every transition explicit, zero model calls.

State lives in a persisted record, never in model prose. That is what makes
`close_case` provable rather than promised — Cedar reads `verification_passed`
from this record via `context_enricher`, and the model can never write it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class State(str, Enum):
    NEW = "NEW"
    ANALYZING = "ANALYZING"
    PLANNING = "PLANNING"
    COORDINATING = "COORDINATING"
    WAITING = "WAITING"
    VERIFYING = "VERIFYING"
    ASK = "ASK"
    BLOCKED = "BLOCKED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    REOPENED = "REOPENED"


# ---------------------------------------------------------------------------
# Allowed transitions — exactly as specified
# ---------------------------------------------------------------------------

ALLOWED: dict[str, set[str]] = {
    "NEW":          {"ANALYZING"},
    "ANALYZING":    {"PLANNING", "ASK", "BLOCKED"},
    "PLANNING":     {"COORDINATING", "ASK", "BLOCKED"},
    "COORDINATING": {"WAITING", "VERIFYING", "ASK", "BLOCKED"},
    "WAITING":      {"COORDINATING", "ASK", "CANCELLED"},
    "VERIFYING":    {"CLOSED", "COORDINATING", "ASK", "BLOCKED"},
    "ASK":          {"COORDINATING", "BLOCKED", "CLOSED"},
    "BLOCKED":      {"ASK", "COORDINATING"},
    "CLOSED":       {"REOPENED"},
    "CANCELLED":    {"REOPENED"},
    "REOPENED":     {"ANALYZING"},
}


# ---------------------------------------------------------------------------
# Actor type
# ---------------------------------------------------------------------------

Actor = Literal["system", "agent", "human"]

VALID_ACTORS: set[str] = {"system", "agent", "human"}


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class InvalidTransition(Exception):
    """Raised when a transition is not allowed. Never silently corrected."""

    def __init__(self, from_state: str, to_state: str, reason: str = ""):
        self.from_state = from_state
        self.to_state = to_state
        msg = f"Invalid transition: {from_state} → {to_state}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Transition record — append-only audit trail
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CaseTransition:
    """Immutable record of a single state transition. Append-only."""
    from_state: str
    to_state: str
    reason: str
    actor: str
    at: datetime
    evidence_id: str | None = None


# ---------------------------------------------------------------------------
# Case record with transitions
# ---------------------------------------------------------------------------

@dataclass
class Case:
    """Case record with state and append-only transition history."""
    case_id: str
    event_key: str
    body_name: str
    event_date: str
    state: str = "NEW"
    verification_passed: bool = False
    verification_id: str | None = None
    human_decision_made: bool = False
    transitions: list[CaseTransition] = field(default_factory=list)
    _idempotency_keys: set[str] = field(default_factory=set)

    def __post_init__(self):
        # Ensure _idempotency_keys is a set
        if not isinstance(self._idempotency_keys, set):
            self._idempotency_keys = set(self._idempotency_keys)


# ---------------------------------------------------------------------------
# Transition function
# ---------------------------------------------------------------------------

def transition(
    case: Case,
    to_state: str,
    reason: str,
    actor: str,
    evidence_id: str | None = None,
    idempotency_key: str | None = None,
) -> CaseTransition | None:
    """
    Transition a case to a new state.

    Raises InvalidTransition on any illegal move. Never silently corrects.
    Every call appends a CaseTransition row — that list IS the audit trail.

    Args:
        case: The case to transition
        to_state: Target state
        reason: Why this transition is happening
        actor: One of "system", "agent", "human"
        evidence_id: Required for → CLOSED
        idempotency_key: If provided and seen before, no-op (no duplicate row)

    Returns:
        The CaseTransition record, or None if idempotency key was duplicate

    Raises:
        InvalidTransition: If transition is not allowed
    """
    from_state = case.state

    # Validate actor
    if actor not in VALID_ACTORS:
        raise InvalidTransition(from_state, to_state, f"invalid actor: {actor}")

    # Idempotency check — same key = no-op, not duplicate row
    if idempotency_key is not None:
        idem_signature = f"{from_state}→{to_state}:{idempotency_key}"
        if idem_signature in case._idempotency_keys:
            return None  # No-op
        case._idempotency_keys.add(idem_signature)

    # Check if transition is allowed
    allowed_targets = ALLOWED.get(from_state, set())
    if to_state not in allowed_targets:
        raise InvalidTransition(from_state, to_state)

    # Special invariant: → CLOSED requires evidence_id AND verification_passed
    if to_state == "CLOSED":
        if evidence_id is None:
            raise InvalidTransition(
                from_state, to_state,
                "→ CLOSED requires non-null evidence_id"
            )
        if not case.verification_passed:
            raise InvalidTransition(
                from_state, to_state,
                "→ CLOSED requires verification_passed=True"
            )
        # Additional check: ASK → CLOSED requires human decision
        if from_state == "ASK" and not case.human_decision_made:
            raise InvalidTransition(
                from_state, to_state,
                "ASK → CLOSED requires human_decision_made=True"
            )

    # Create transition record
    record = CaseTransition(
        from_state=from_state,
        to_state=to_state,
        reason=reason,
        actor=actor,
        at=datetime.now(timezone.utc),
        evidence_id=evidence_id,
    )

    # Append to audit trail (append-only)
    case.transitions.append(record)

    # Update state
    case.state = to_state

    return record


# ---------------------------------------------------------------------------
# Feed change → transition mapping (deterministic, no model)
# ---------------------------------------------------------------------------

def apply_feed_change(
    case: Case,
    change_type: str,
    idempotency_key: str | None = None,
) -> CaseTransition | None:
    """
    Apply a feed change to a case, transitioning state as specified.

    | change_type                  | Case is in                    | Goes to      |
    |------------------------------|-------------------------------|--------------|
    | cancelled                    | anything except CLOSED        | CANCELLED    |
    | cancelled                    | CLOSED                        | REOPENED     |
    | rescheduled_or_relocated     | CLOSED                        | REOPENED     |
    | rescheduled_or_relocated     | anything else                 | ANALYZING    |
    | agenda_posted                | NEW · ANALYZING               | ANALYZING    |
    | agenda_replaced              | CLOSED · VERIFYING            | REOPENED     |
    | agenda_replaced              | anything else                 | COORDINATING |

    Args:
        case: The case to update
        change_type: One of cancelled, rescheduled_or_relocated, agenda_posted, agenda_replaced
        idempotency_key: For deduplication

    Returns:
        The CaseTransition record, or None if no transition needed
    """
    current = case.state

    if change_type == "cancelled":
        if current == "CLOSED":
            to_state = "REOPENED"
            reason = "meeting cancelled after closure"
        else:
            # Direct transition to CANCELLED — need to check if allowed
            # From the ALLOWED map, only WAITING can go to CANCELLED
            # For other states, we may need intermediate steps
            # But per the spec table, "anything except CLOSED" → CANCELLED
            # This means we need to allow this as a special override
            to_state = "CANCELLED"
            reason = "meeting cancelled in feed"

    elif change_type == "rescheduled_or_relocated":
        if current == "CLOSED":
            to_state = "REOPENED"
            reason = "meeting rescheduled/relocated after closure"
        else:
            # Go to ANALYZING to re-derive obligations
            to_state = "ANALYZING"
            reason = "meeting rescheduled/relocated"

    elif change_type == "agenda_posted":
        if current in ("NEW", "ANALYZING"):
            to_state = "ANALYZING"
            reason = "agenda posted"
        else:
            return None  # No transition needed

    elif change_type == "agenda_replaced":
        if current in ("CLOSED", "VERIFYING"):
            to_state = "REOPENED"
            reason = "agenda replaced — evidence is now stale"
        else:
            to_state = "COORDINATING"
            reason = "agenda replaced"

    else:
        return None  # Unknown change type

    # Check if we're already in target state
    if current == to_state:
        return None

    # Special handling for transitions not in ALLOWED map
    # The feed changes can force certain transitions that wouldn't normally be allowed
    # (e.g., NEW → CANCELLED is not in ALLOWED, but feed says meeting is cancelled)
    allowed_targets = ALLOWED.get(current, set())

    if to_state not in allowed_targets:
        # For feed-driven changes, we allow override via intermediate states
        # Or we can treat these as special system transitions
        # Per the spec, feed changes should drive these transitions
        # Let's handle the common cases:

        if to_state == "CANCELLED" and current != "WAITING":
            # Need to go through intermediate states or allow directly
            # The spec says "anything except CLOSED" → CANCELLED
            # This is a system override
            return _force_transition(case, to_state, reason, idempotency_key)

        if to_state == "ANALYZING" and current not in ("NEW", "REOPENED"):
            # rescheduled_or_relocated from non-standard state
            return _force_transition(case, to_state, reason, idempotency_key)

        if to_state == "COORDINATING" and current not in ("PLANNING", "WAITING", "VERIFYING", "ASK", "BLOCKED"):
            # agenda_replaced from non-standard state
            return _force_transition(case, to_state, reason, idempotency_key)

        if to_state == "REOPENED" and current == "VERIFYING":
            # agenda_replaced from VERIFYING — evidence is now stale
            return _force_transition(case, to_state, reason, idempotency_key)

    return transition(case, to_state, reason, "system", idempotency_key=idempotency_key)


def _force_transition(
    case: Case,
    to_state: str,
    reason: str,
    idempotency_key: str | None = None,
) -> CaseTransition:
    """
    Force a transition that the feed requires but ALLOWED map doesn't permit.
    This is a system-level override for external events.
    """
    from_state = case.state

    # Idempotency check
    if idempotency_key is not None:
        idem_signature = f"{from_state}→{to_state}:{idempotency_key}"
        if idem_signature in case._idempotency_keys:
            return None
        case._idempotency_keys.add(idem_signature)

    # Create transition record
    record = CaseTransition(
        from_state=from_state,
        to_state=to_state,
        reason=f"[feed override] {reason}",
        actor="system",
        at=datetime.now(timezone.utc),
        evidence_id=None,
    )

    case.transitions.append(record)
    case.state = to_state

    return record


# ---------------------------------------------------------------------------
# Helper to print audit trail
# ---------------------------------------------------------------------------

def format_audit_trail(case: Case) -> str:
    """Format the audit trail for display."""
    lines = [f"{case.case_id}  {case.event_key}  \"{case.body_name}\" {case.event_date}", ""]

    for t in case.transitions:
        ev = f"  evidence={t.evidence_id}" if t.evidence_id else ""
        lines.append(f"  {t.from_state:12} -> {t.to_state:13} {t.actor:6}  {t.reason}{ev}")

    lines.append("")
    lines.append(f"  {len(case.transitions)} transitions · verification_passed={case.verification_passed}")

    return "\n".join(lines)
