"""
AccessFlow Worker Lambda — processes one meeting from SQS.

This handler:
1. Receives a meeting from SQS
2. Invokes the AgentCore runtime synchronously via HTTP
3. The runtime persists the case to DynamoDB

Triggered by SQS. Has 15-minute timeout to accommodate ~4 minute runtime calls.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

# Structured logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AgentCore runtime ARN
AGENTCORE_RUNTIME_ARN = os.getenv(
    "AGENTCORE_RUNTIME_ARN",
    "arn:aws:bedrock-agentcore:us-west-2:684589295815:runtime/AccessFlow_AccessFlow-WrVmzyAk66"
)

# Extract runtime ID from ARN
RUNTIME_ID = AGENTCORE_RUNTIME_ARN.split("/")[-1] if AGENTCORE_RUNTIME_ARN else ""
REGION = "us-west-2"

# Lazy-init session
_session = None


def _get_session():
    global _session
    if _session is None:
        _session = boto3.Session()
    return _session


def _log_structured(event: str, **kwargs: Any) -> None:
    """Log structured JSON to CloudWatch."""
    record = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    logger.info(json.dumps(record))


def _invoke_agentcore(meeting: dict[str, Any]) -> dict[str, Any]:
    """Invoke AgentCore runtime for a meeting using boto3 client.

    Returns:
        dict with invocation result
    """
    _log_structured(
        "invoking_agentcore",
        runtime_id=RUNTIME_ID,
        meeting_key=meeting.get("key"),
    )

    try:
        # Use boto3 client for bedrock-agentcore with extended timeout
        # AgentCore calls take ~241s, default botocore read_timeout is 60s
        client = boto3.client(
            "bedrock-agentcore",
            region_name=REGION,
            config=Config(
                read_timeout=900,
                connect_timeout=10,
                retries={"max_attempts": 0},  # No retries - avoid double-charging
            ),
        )

        # Build the payload with meeting data
        payload = json.dumps({"meeting": meeting})

        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
            payload=payload,
        )

        # The response contains a StreamingBody - read it if needed
        # AgentCore runtime persists to DynamoDB, so we just need success status
        status_code = response.get("statusCode", 200)
        session_id = response.get("runtimeSessionId", "")

        _log_structured(
            "agentcore_complete",
            meeting_key=meeting.get("key"),
            status_code=status_code,
            session_id=session_id,
        )

        return {"success": True, "status_code": status_code}

    except Exception as e:
        _log_structured(
            "agentcore_error",
            meeting_key=meeting.get("key"),
            error=str(e),
            error_type=type(e).__name__,
        )
        return {"success": False, "error": str(e)}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler — processes meetings from SQS.

    Each SQS message contains one meeting to process.
    """
    records = event.get("Records", [])

    _log_structured(
        "worker_started",
        record_count=len(records),
    )

    results = []
    for record in records:
        body = json.loads(record.get("body", "{}"))

        # Build meeting dict from SQS message
        meeting = {
            "key": body.get("meeting_key"),
            "body_name": body.get("body_name"),
            "date": body.get("date"),
            "time": body.get("time"),
            "agenda_url": body.get("agenda_url"),
        }

        result = _invoke_agentcore(meeting)
        results.append({
            "meeting_key": meeting.get("key"),
            **result
        })

    success_count = sum(1 for r in results if r.get("success"))

    _log_structured(
        "worker_complete",
        total=len(records),
        success=success_count,
        failed=len(records) - success_count,
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "processed": len(records),
            "success": success_count,
            "results": results,
        }),
    }
