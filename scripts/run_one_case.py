#!/usr/bin/env python3
"""
Run one real meeting through the orchestrator end to end.

Day 5 exit criterion: one real meeting from the live feed, start to finish.

    MODEL_PROVIDER=bedrock python scripts/run_one_case.py

Must print, and all four must be true:
    case_xxx  seattle:6860  "City Council" 2026-09-08
      ... transitions ...
      N model calls · $0.0xx spent · CLOSED · verification_passed=True
"""
from __future__ import annotations

import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.agents.budget import spent_today
from backend.app.agents.model import describe as describe_model
from backend.app.agents.orchestrator import build_orchestrator, run_case
from backend.app.domain.state import Case, format_audit_trail
from backend.app.models.store import get_store, reset_store
from backend.app.tools.case_tools import poll_public_meetings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)


def find_case_from_store(meeting_key: str) -> Case | None:
    """Find the case created for a meeting by looking up the store."""
    store = get_store()

    # Look through cases to find one matching this meeting
    for case_id, case in store._cases.items():
        if case.event_id == meeting_key:
            # Convert store Case to domain Case for audit trail
            return Case(
                case_id=case.case_id,
                event_key=case.event_id,
                body_name="",  # Will fill from meeting
                event_date="",
                state=case.state.value if hasattr(case.state, "value") else str(case.state),
                verification_passed=case.verification_passed,
                verification_id=case.verification_id,
            )

    return None


def main() -> int:
    """Run one real meeting through the orchestrator."""
    print(f"Model: {describe_model()}")
    print()

    # Reset store for clean run
    reset_store()

    # Step 1: Poll for a real meeting
    print("Polling for real meetings...")
    result = poll_public_meetings(clients=["seattle"], days_ahead=45)

    if not result["ok"]:
        print(f"Poll failed: {result}")
        return 1

    meetings = result["new"]
    if not meetings:
        print("No new meetings found. Try a different client or wider window.")
        return 1

    # Pick the first meeting with an agenda (for a more complete demo)
    meeting = None
    for m in meetings:
        if m.get("agenda_url"):
            meeting = m
            break

    if meeting is None:
        meeting = meetings[0]

    print(f"Selected: {meeting['key']} - {meeting['body_name']}")
    print()

    # Step 2: Build the orchestrator
    agent, budgeted_model, cedar = build_orchestrator()

    # Step 3: Run the case
    print("Running orchestrator...")
    print("-" * 60)

    try:
        run_result = run_case(agent, meeting, budgeted_model)
    except Exception as e:
        log.error("Orchestrator failed: %s", e)
        print(f"\nOrchestrator error: {e}")
        return 1

    print("-" * 60)
    print()

    # Step 4: Find the case and print audit trail
    store = get_store()

    # Find case - might need to search by different criteria
    final_case = None
    final_state = "UNKNOWN"
    verification_passed = False

    # Look for any cases in the store
    if store._cases:
        case_id = list(store._cases.keys())[0]
        store_case = store._cases[case_id]
        final_state = store_case.state.value if hasattr(store_case.state, "value") else str(store_case.state)
        verification_passed = store_case.verification_passed

        # Build a domain Case for display
        final_case = Case(
            case_id=store_case.case_id,
            event_key=meeting["key"],
            body_name=meeting["body_name"],
            event_date=meeting["date"],
            state=final_state,
            verification_passed=verification_passed,
            verification_id=store_case.verification_id,
        )

    # Print summary
    print(f"{meeting['key']}  \"{meeting['body_name']}\" {meeting['date']}")
    print()

    # Print actions as proxy for transitions (store tracks agent actions)
    actions = store.get_actions()
    for action in actions:
        status = "ok" if action.success else f"FAIL:{action.error_code}"
        print(f"  {action.tool_name:25} {status}")

    print()

    # Final line with the four key metrics
    model_calls = budgeted_model.model_calls
    spent = spent_today()

    print(
        f"  {model_calls} model calls · ${spent:.4f} spent · "
        f"{final_state} · verification_passed={verification_passed}"
    )

    # Exit criteria check
    success = (
        final_state == "CLOSED"
        and verification_passed is True
        and model_calls > 0
    )

    if success:
        print()
        print("Exit criteria met.")
    else:
        print()
        print("Exit criteria NOT met:")
        if final_state != "CLOSED":
            print(f"  - State is {final_state}, expected CLOSED")
        if not verification_passed:
            print("  - verification_passed is False")
        if model_calls == 0:
            print("  - No model calls recorded")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
