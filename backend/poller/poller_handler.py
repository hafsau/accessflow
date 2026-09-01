"""
AccessFlow Lambda Poller — the background loop that makes the product claim true.

This handler:
1. Loads fingerprints from DynamoDB
2. Polls Legistar for real meetings
3. Diffs fingerprints to detect REAL changes only
4. Invokes AgentCore for each change (max 3 per invocation)
5. Saves updated fingerprints back to DynamoDB
6. Logs structured JSON to CloudWatch

The critical invariant:
> A poll where nothing moved produces zero agent invocations.

Triggered by EventBridge every 15 minutes. Runs OUTSIDE any VPC.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3

try:
    # Lambda deployment structure (CodeUri: ../backend/)
    from poller.persistence import (
        load_fingerprints,
        save_fingerprints_batch,
        load_budget,
        save_budget,
    )
    from app.tools.legistar import LegistarFeed, Meeting, WATCHED_CLIENTS
except ImportError:
    # Local development structure
    from backend.poller.persistence import (
        load_fingerprints,
        save_fingerprints_batch,
        load_budget,
        save_budget,
    )
    from backend.app.tools.legistar import LegistarFeed, Meeting, WATCHED_CLIENTS

# Structured logging to CloudWatch
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Max cases to queue per poller invocation (cost control)
MAX_CASES_PER_INVOCATION = int(os.getenv("MAX_CASES_PER_INVOCATION", "3"))

# Daily budget cap in USD
DAILY_USD_CAP = float(os.getenv("DAILY_USD_CAP", "1.50"))

# Cost per agent invocation (estimated)
COST_PER_INVOCATION = 0.54  # Updated: ~9 model calls per case

# SQS queue for meeting processing (async via worker Lambda)
MEETING_QUEUE_URL = os.getenv("MEETING_QUEUE_URL", "")

# Lazy-init SQS client
_sqs = None


def _get_sqs():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


def _log_structured(event: str, **kwargs: Any) -> None:
    """Log structured JSON to CloudWatch."""
    record = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    logger.info(json.dumps(record))


def _queue_meeting(meeting: Meeting, change_type: str) -> bool:
    """Queue a meeting to SQS for async processing by worker Lambda.

    Returns:
        True if queued successfully, False otherwise
    """
    if not MEETING_QUEUE_URL:
        _log_structured(
            "queue_skipped",
            reason="MEETING_QUEUE_URL not configured",
            meeting_key=meeting.key,
        )
        return False

    try:
        sqs = _get_sqs()
        message = {
            "meeting_key": meeting.key,
            "body_name": meeting.body_name,
            "date": meeting.date,
            "time": meeting.time,
            "agenda_url": meeting.agenda_url,
            "change_type": change_type,
        }
        sqs.send_message(
            QueueUrl=MEETING_QUEUE_URL,
            MessageBody=json.dumps(message),
        )
        _log_structured(
            "meeting_queued",
            meeting_key=meeting.key,
            change_type=change_type,
        )
        return True
    except Exception as e:
        _log_structured(
            "queue_error",
            error=str(e),
            meeting_key=meeting.key,
        )
        return False


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler — the background loop.

    Triggered by EventBridge every 15 minutes.
    """
    _log_structured("poll_started")

    # Pre-flight budget check — FAIL CLOSED on any error
    budget_skipped = False
    try:
        budget = load_budget()
        budget_spent_usd = budget["spent"]
    except Exception as e:
        _log_structured(
            "budget_load_error",
            error=str(e),
            action="skipping_all_invocations",
        )
        # FAIL CLOSED: cannot verify budget, skip all invocations
        budget_spent_usd = 0.0
        budget_skipped = True
        budget = {"date": datetime.now(timezone.utc).date().isoformat(), "spent": 0.0, "calls": 0}

    # Check if daily cap exceeded
    if not budget_skipped and budget_spent_usd >= DAILY_USD_CAP:
        _log_structured(
            "budget_exceeded",
            spent=budget_spent_usd,
            cap=DAILY_USD_CAP,
            action="skipping_all_invocations",
        )
        budget_skipped = True

    # Load current fingerprints from DynamoDB
    fingerprints = load_fingerprints()
    _log_structured(
        "fingerprints_loaded",
        count=len(fingerprints),
    )

    # Create feed with fingerprints pre-loaded
    feed = LegistarFeed(clients=WATCHED_CLIENTS)
    feed._seen = fingerprints.copy()

    # Poll all jurisdictions
    new_meetings, changes = feed.poll()

    # Calculate what actually changed
    updated_fingerprints: dict[str, str] = {}
    changes_to_process: list[tuple[Meeting, str, str, str]] = []

    for meeting in new_meetings:
        fp = meeting.fingerprint()
        old_fp = fingerprints.get(meeting.key)
        if old_fp is None:
            # Truly new meeting
            updated_fingerprints[meeting.key] = fp
            changes_to_process.append((meeting, "new_meeting", "", fp))
            _log_structured(
                "change_detected",
                key=meeting.key,
                old_fp="",
                new_fp=fp,
                change_type="new_meeting",
                invoked_agent=False,  # Updated below
            )

    for change in changes:
        meeting = change.meeting
        fp = meeting.fingerprint()
        old_fp = fingerprints.get(meeting.key, "")
        updated_fingerprints[meeting.key] = fp
        changes_to_process.append((meeting, change.change_type, old_fp, fp))
        _log_structured(
            "change_detected",
            key=meeting.key,
            old_fp=old_fp,
            new_fp=fp,
            change_type=change.change_type,
            invoked_agent=False,  # Updated below
        )

    # If nothing changed, we're done — zero agent invocations
    if not changes_to_process:
        _log_structured(
            "poll_complete",
            new_meetings=0,
            changes=0,
            agent_invocations=0,
            queued=0,
            budget_spent_usd=budget_spent_usd,
            budget_skipped=budget_skipped,
        )
        return {
            "statusCode": 200,
            "body": json.dumps({
                "new_meetings": 0,
                "changes": 0,
                "agent_invocations": 0,
                "queued": 0,
                "budget_spent_usd": budget_spent_usd,
                "budget_skipped": budget_skipped,
            }),
        }

    # If budget exceeded or load failed, skip queueing
    if budget_skipped:
        # Still save fingerprints so we don't reprocess on next poll
        if updated_fingerprints:
            all_fingerprints = {**fingerprints, **updated_fingerprints}
            save_fingerprints_batch(all_fingerprints)
            _log_structured(
                "fingerprints_saved",
                updated=len(updated_fingerprints),
                total=len(all_fingerprints),
            )
        _log_structured(
            "poll_complete",
            new_meetings=len(new_meetings),
            changes=len(changes),
            queued=0,
            budget_spent_usd=budget_spent_usd,
            budget_skipped=budget_skipped,
        )
        return {
            "statusCode": 200,
            "body": json.dumps({
                "new_meetings": len(new_meetings),
                "changes": len(changes),
                "queued": 0,
                "budget_spent_usd": budget_spent_usd,
                "budget_skipped": budget_skipped,
            }),
        }

    # Queue only actionable meetings to SQS (async via worker Lambda)
    # A meeting is actionable when:
    #   1. It's a new meeting AND has an agenda_url, OR
    #   2. change_type is "agenda_posted" (agenda just became available)
    # Meetings without agenda_url cost ~$0.54 per invocation and cannot succeed.
    queued_count = 0
    awaiting_agenda_count = 0

    for meeting, change_type, old_fp, new_fp in changes_to_process:
        if queued_count >= MAX_CASES_PER_INVOCATION:
            break

        # Check if this meeting is actionable
        has_agenda = bool(meeting.agenda_url)
        is_agenda_posted = change_type == "agenda_posted"
        is_new_meeting = change_type == "new_meeting"

        if is_agenda_posted or (is_new_meeting and has_agenda):
            # Actionable: queue for AgentCore
            if _queue_meeting(meeting, change_type):
                queued_count += 1
        elif is_new_meeting and not has_agenda:
            # Not actionable yet: log as awaiting agenda
            awaiting_agenda_count += 1
            _log_structured(
                "awaiting_agenda",
                meeting_key=meeting.key,
                body_name=meeting.body_name,
                date=meeting.date,
                reason="new meeting has no agenda_url; will queue when agenda_posted",
            )
        # Other change types (cancelled, rescheduled) are tracked via fingerprints
        # but don't need agent invocation - they're detected on next poll if relevant

    # Save updated fingerprints
    if updated_fingerprints:
        all_fingerprints = {**fingerprints, **updated_fingerprints}
        save_fingerprints_batch(all_fingerprints)
        _log_structured(
            "fingerprints_saved",
            updated=len(updated_fingerprints),
            total=len(all_fingerprints),
        )

    _log_structured(
        "poll_complete",
        new_meetings=len(new_meetings),
        changes=len(changes),
        queued=queued_count,
        awaiting_agenda=awaiting_agenda_count,
        budget_spent_usd=budget_spent_usd,
        budget_skipped=budget_skipped,
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "new_meetings": len(new_meetings),
            "changes": len(changes),
            "queued": queued_count,
            "awaiting_agenda": awaiting_agenda_count,
            "budget_spent_usd": budget_spent_usd,
            "budget_skipped": budget_skipped,
        }),
    }


# For local testing
if __name__ == "__main__":
    result = handler({}, None)
    print(json.dumps(json.loads(result["body"]), indent=2))
