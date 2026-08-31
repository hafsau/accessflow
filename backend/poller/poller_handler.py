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

from backend.poller.persistence import (
    load_fingerprints,
    save_fingerprints_batch,
)
from backend.app.tools.legistar import LegistarFeed, Meeting, WATCHED_CLIENTS

# Structured logging to CloudWatch
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Max cases per invocation to prevent cost spikes
MAX_CASES_PER_INVOCATION = int(os.getenv("MAX_CASES_PER_INVOCATION", "3"))

# SQS queue for overflow (when > MAX_CASES changes detected)
OVERFLOW_QUEUE_URL = os.getenv("OVERFLOW_QUEUE_URL", "")

# Bedrock agent configuration
AGENT_ID = os.getenv("BEDROCK_AGENT_ID", "")
AGENT_ALIAS_ID = os.getenv("BEDROCK_AGENT_ALIAS_ID", "TSTALIASID")

# Lazy-init clients
_bedrock_agent_runtime = None
_sqs = None


def _get_bedrock_agent_runtime():
    global _bedrock_agent_runtime
    if _bedrock_agent_runtime is None:
        _bedrock_agent_runtime = boto3.client("bedrock-agent-runtime")
    return _bedrock_agent_runtime


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


def _invoke_agent(meeting: Meeting, change_type: str) -> dict[str, Any]:
    """Invoke Bedrock AgentCore for a meeting change.

    Returns:
        dict with invocation result or error
    """
    if not AGENT_ID:
        _log_structured(
            "agent_invocation_skipped",
            reason="BEDROCK_AGENT_ID not configured",
            meeting_key=meeting.key,
        )
        return {"skipped": True, "reason": "agent not configured"}

    prompt = f"""A meeting has changed and requires attention:

Meeting Key: {meeting.key}
Body: {meeting.body_name}
Date: {meeting.date}
Time: {meeting.time or 'Not specified'}
Change Type: {change_type}
Agenda URL: {meeting.agenda_url or 'None'}

Process this meeting through to closure following the standard workflow.
If the meeting is cancelled, close the case appropriately.
If you cannot proceed safely at any step, request a human decision."""

    try:
        client = _get_bedrock_agent_runtime()
        response = client.invoke_agent(
            agentId=AGENT_ID,
            agentAliasId=AGENT_ALIAS_ID,
            sessionId=f"poll-{meeting.key.replace(':', '-')}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            inputText=prompt,
        )

        # Collect response
        completion = ""
        for event in response.get("completion", []):
            chunk = event.get("chunk", {})
            if "bytes" in chunk:
                completion += chunk["bytes"].decode("utf-8")

        _log_structured(
            "agent_invocation_complete",
            meeting_key=meeting.key,
            change_type=change_type,
            response_length=len(completion),
        )

        return {"success": True, "response_length": len(completion)}

    except Exception as e:
        _log_structured(
            "agent_invocation_error",
            meeting_key=meeting.key,
            change_type=change_type,
            error=str(e),
        )
        return {"success": False, "error": str(e)}


def _queue_overflow(meetings: list[tuple[Meeting, str, str, str]]) -> None:
    """Queue overflow meetings to SQS for later processing."""
    if not OVERFLOW_QUEUE_URL or not meetings:
        return

    try:
        sqs = _get_sqs()
        for meeting, change_type, old_fp, new_fp in meetings:
            message = {
                "meeting_key": meeting.key,
                "body_name": meeting.body_name,
                "date": meeting.date,
                "time": meeting.time,
                "agenda_url": meeting.agenda_url,
                "change_type": change_type,
                "old_fingerprint": old_fp,
                "new_fingerprint": new_fp,
            }
            sqs.send_message(
                QueueUrl=OVERFLOW_QUEUE_URL,
                MessageBody=json.dumps(message),
            )
            _log_structured(
                "meeting_queued",
                meeting_key=meeting.key,
                change_type=change_type,
            )
    except Exception as e:
        _log_structured(
            "queue_error",
            error=str(e),
            meetings_count=len(meetings),
        )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler — the background loop.

    Triggered by EventBridge every 15 minutes.
    """
    _log_structured("poll_started")

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
        )
        return {
            "statusCode": 200,
            "body": json.dumps({
                "new_meetings": 0,
                "changes": 0,
                "agent_invocations": 0,
            }),
        }

    # Process up to MAX_CASES_PER_INVOCATION, queue the rest
    to_process = changes_to_process[:MAX_CASES_PER_INVOCATION]
    to_queue = changes_to_process[MAX_CASES_PER_INVOCATION:]

    agent_invocations = 0
    for meeting, change_type, old_fp, new_fp in to_process:
        _log_structured(
            "change_detected",
            key=meeting.key,
            old_fp=old_fp,
            new_fp=new_fp,
            change_type=change_type,
            invoked_agent=True,
        )
        result = _invoke_agent(meeting, change_type)
        if result.get("success") or result.get("skipped"):
            agent_invocations += 1

    # Queue overflow
    if to_queue:
        _queue_overflow(to_queue)

    # Save updated fingerprints
    if updated_fingerprints:
        # Merge with existing and save
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
        agent_invocations=agent_invocations,
        queued=len(to_queue),
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "new_meetings": len(new_meetings),
            "changes": len(changes),
            "agent_invocations": agent_invocations,
            "queued": len(to_queue),
        }),
    }


# For local testing
if __name__ == "__main__":
    result = handler({}, None)
    print(json.dumps(json.loads(result["body"]), indent=2))
