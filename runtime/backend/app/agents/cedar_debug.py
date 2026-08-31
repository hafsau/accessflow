"""
Cedar diagnostic harness — log EVERY evaluation and catch silent policy errors.

Cedar silently skips any policy that errors during evaluation. An erroring
`forbid` is skipped, so a `permit` elsewhere wins and the call goes through.
**The gate fails OPEN.**

This harness ensures:
1. Every evaluation is logged with decision, determining policies, and errors
2. In tests, a non-empty diagnostics.errors raises — a skipped policy must never be silent

Build the diagnostic harness BEFORE policy #2 exists.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("cedar")


class CedarDiagnosticsError(Exception):
    """Raised when a Cedar policy has evaluation errors.

    An erroring policy is silently skipped by Cedar. This exception ensures
    errors are loud in tests, not silent in production decisions.
    """

    def __init__(self, tool_name: str, errors: list[Any]):
        self.tool_name = tool_name
        self.errors = errors
        super().__init__(f"Cedar policy evaluation errors on {tool_name}: {errors}")


def trace(response: Any, tool_name: str, tool_input: dict[str, Any]) -> None:
    """Log EVERY evaluation: decision, which policies determined it, and any
    evaluation errors. Errors are the ones that matter — an erroring policy is
    invisible in the decision alone.

    Args:
        response: The Cedar authorization response object (or Proceed/Deny)
        tool_name: Name of the tool being authorized
        tool_input: The tool input arguments

    Raises:
        CedarDiagnosticsError: If diagnostics.errors is non-empty and we're in test mode
    """
    # Handle Strands SDK Proceed/Deny objects
    response_type = getattr(response, "type", None)
    if response_type is not None:
        # This is a Proceed or Deny object from Strands SDK
        decision = str(response_type)  # "proceed" or "deny"
        reason = getattr(response, "reason", None)
        errors = None
        diag = None
    else:
        # Standard Cedar response format
        diag = getattr(response, "diagnostics", None)
        decision = getattr(response, "decision", "?")
        reason = getattr(diag, "reason", None) if diag else None
        errors = getattr(diag, "errors", None) if diag else None

    # Always log
    log.info(
        "cedar decision=%s tool=%s determining=%s errors=%s",
        decision,
        tool_name,
        reason,      # which policy ids decided
        errors,      # ← the silent killers
    )

    # In tests, non-empty errors must raise, not just log
    # A skipped policy must never be silent again
    if errors and len(errors) > 0:
        # Check if we're in test mode
        if _is_test_mode():
            raise CedarDiagnosticsError(tool_name, errors)
        else:
            log.error(
                "CEDAR POLICY ERROR (silent skip in production): tool=%s errors=%s",
                tool_name,
                errors,
            )


def _is_test_mode() -> bool:
    """Detect if we're running in test mode."""
    # Check for pytest
    return (
        "PYTEST_CURRENT_TEST" in os.environ
        or os.environ.get("CEDAR_STRICT_ERRORS", "").lower() in ("1", "true", "yes")
    )


def denied(response: Any) -> bool:
    """Check if the Cedar response is a denial."""
    decision = getattr(response, "decision", None)
    if decision is None:
        return True  # No decision = deny
    return str(decision).lower() in ("deny", "denied")


def allowed(response: Any) -> bool:
    """Check if the Cedar response is an allow."""
    return not denied(response)
