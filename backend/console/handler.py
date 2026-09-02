"""
AccessFlow Operations Console — public dashboard with operator controls.

Single Lambda with Function URL serving:
- GET / → HTML dashboard with case list, decision queue, and provider controls
- GET /api/cases → JSON array of cases
- GET /api/pending-requests → JSON array of provider requests awaiting response
- POST /api/simulate-provider-response → Simulate provider confirmation (operator action)

AuthType: NONE (public, no credentials required).
Provider simulation is an operator action, not an agent action.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from . import render

# DynamoDB table
CORE_TABLE = os.getenv("CORE_TABLE", "accessflow-core")
REGION = os.getenv("AWS_REGION", "us-west-2")

# Lazy-init DynamoDB
_table = None


def _get_table():
    global _table
    if _table is None:
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        _table = dynamodb.Table(CORE_TABLE)
    return _table


def _get_cases() -> list[dict[str, Any]]:
    """Query all cases from DynamoDB using GSI1."""
    table = _get_table()

    # Query GSI1 for all cases
    response = table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq("CASE"),
    )

    cases = []
    for item in response.get("Items", []):
        # Parse the data field (stored as JSON string)
        if "data" in item:
            case_data = json.loads(item["data"]) if isinstance(item["data"], str) else item["data"]
            cases.append(case_data)

    # Sort by created_at descending (most recent first)
    cases.sort(key=lambda c: c.get("created_at", ""), reverse=True)

    # Integrity check: no two live cases should share an event_id
    _check_duplicate_event_ids(cases)

    return cases


def _check_duplicate_event_ids(cases: list[dict[str, Any]]) -> None:
    """Log a warning if any event_id appears in multiple non-CANCELLED cases.

    This is a data integrity check — the product claim is one case per meeting.
    """
    from collections import Counter
    import logging

    live_cases = [c for c in cases if c.get("state") != "CANCELLED"]
    event_counts = Counter(c.get("event_id") for c in live_cases)
    duplicates = {k: v for k, v in event_counts.items() if v > 1}

    if duplicates:
        logging.warning(
            f"DATA INTEGRITY: duplicate event_ids found in live cases: {duplicates}. "
            "Each meeting should have exactly one case."
        )


def _get_case_decision(case_id: str) -> dict[str, Any] | None:
    """Get decision for a case if it exists."""
    table = _get_table()

    # Query for decision items under this case
    response = table.query(
        KeyConditionExpression=Key("PK").eq(f"CASE#{case_id}") & Key("SK").begins_with("DECISION#"),
    )

    items = response.get("Items", [])
    if items:
        item = items[0]
        if "data" in item:
            return json.loads(item["data"]) if isinstance(item["data"], str) else item["data"]
    return None


def _get_pending_requests() -> list[dict[str, Any]]:
    """Get all provider requests with status SENT (awaiting provider response)."""
    table = _get_table()

    # Scan for REQUEST entities with SENT status
    response = table.scan(
        FilterExpression="entity = :e",
        ExpressionAttributeValues={":e": "REQUEST"},
    )

    pending = []
    for item in response.get("Items", []):
        if "data" in item:
            data = json.loads(item["data"]) if isinstance(item["data"], str) else item["data"]
            if data.get("status") == "SENT":
                pending.append(data)

    # Sort by sent_at
    pending.sort(key=lambda r: r.get("sent_at", ""), reverse=True)
    return pending


def _get_events(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Query EVENT items for each distinct event_id in cases.

    Args:
        cases: List of case dicts

    Returns:
        Dict keyed by event_id with event data
    """
    table = _get_table()
    events: dict[str, dict[str, Any]] = {}

    # Get distinct event_ids
    event_ids = {str(c.get("event_id")) for c in cases if c.get("event_id")}

    for event_id in event_ids:
        # Query PK = EVENT#<event_id>, SK = META
        response = table.query(
            KeyConditionExpression="PK = :pk AND SK = :sk",
            ExpressionAttributeValues={":pk": f"EVENT#{event_id}", ":sk": "META"},
        )
        items = response.get("Items", [])
        if items:
            item = items[0]
            if "data" in item:
                event_data = json.loads(item["data"]) if isinstance(item["data"], str) else item["data"]
                events[event_id] = event_data

    return events


def _get_actions(cases: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Query ACTION items for each case.

    Args:
        cases: List of case dicts

    Returns:
        Dict keyed by case_id with list of action dicts
    """
    table = _get_table()
    actions: dict[str, list[dict[str, Any]]] = {}

    for case in cases:
        case_id = str(case.get("case_id") or "")
        if not case_id:
            continue

        response = table.query(
            KeyConditionExpression=Key("PK").eq(f"CASE#{case_id}") & Key("SK").begins_with("ACTION#"),
        )

        case_actions = []
        for item in response.get("Items", []):
            if "data" in item:
                action_data = json.loads(item["data"]) if isinstance(item["data"], str) else item["data"]
                case_actions.append(action_data)

        actions[case_id] = case_actions

    return actions


def _requeue_case_for_agent(case_id: str, table: Any) -> bool:
    """Re-queue a case for AgentCore processing.

    After a provider confirms, the agent needs to run verify_fulfillment and
    close_case. This function pushes the meeting back onto the SQS queue so
    the worker Lambda invokes the agent.

    Args:
        case_id: The case to re-queue
        table: DynamoDB table resource

    Returns:
        True if queued successfully, False otherwise
    """
    import boto3

    queue_url = os.getenv("MEETING_QUEUE_URL")
    if not queue_url:
        return False

    # Get the case to find the event_key
    # Case data is stored with SK = "META" (single-table design)
    case_response = table.query(
        KeyConditionExpression="PK = :pk AND SK = :sk",
        ExpressionAttributeValues={":pk": f"CASE#{case_id}", ":sk": "META"},
    )

    if not case_response.get("Items"):
        return False

    case_item = case_response["Items"][0]
    case_data = json.loads(case_item["data"]) if isinstance(case_item.get("data"), str) else case_item.get("data", {})
    event_key = case_data.get("event_id")

    if not event_key:
        return False

    # Get the event to build the meeting message
    event_response = table.query(
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": f"EVENT#{event_key}"},
    )

    if not event_response.get("Items"):
        # Fall back to minimal message
        message = {
            "meeting_key": event_key,
            "body_name": case_data.get("body_name", "Unknown"),
            "date": case_data.get("event_date", ""),
            "time": None,
            "agenda_url": None,
            "change_type": "provider_confirmed",
            "case_id": case_id,  # Include case_id for agent to use directly
        }
    else:
        event_item = event_response["Items"][0]
        event_data = json.loads(event_item["data"]) if isinstance(event_item.get("data"), str) else event_item.get("data", {})
        message = {
            "meeting_key": event_key,
            "body_name": event_data.get("body_name", case_data.get("body_name", "Unknown")),
            "date": event_data.get("date", case_data.get("event_date", "")),
            "time": event_data.get("time"),
            "agenda_url": event_data.get("agenda_url"),
            "change_type": "provider_confirmed",
            "case_id": case_id,  # Include case_id for agent to use directly
        }

    # Send to SQS
    try:
        sqs = boto3.client("sqs")
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message),
        )
        return True
    except Exception:
        return False


def _simulate_provider_response(request_id: str, response_type: str = "CONFIRMED") -> dict[str, Any]:
    """Simulate a provider response (confirmation or decline).

    This is an OPERATOR action, not an agent action. The agent cannot call this.
    The UI clearly labels this as simulated.

    Args:
        request_id: The request to respond to
        response_type: CONFIRMED or DECLINED

    Returns:
        Result dict with success status
    """
    table = _get_table()

    # Find the request
    response = table.scan(
        FilterExpression="entity = :e",
        ExpressionAttributeValues={":e": "REQUEST"},
    )

    request_data = None
    request_item = None
    for item in response.get("Items", []):
        if "data" in item:
            data = json.loads(item["data"]) if isinstance(item["data"], str) else item["data"]
            if data.get("request_id") == request_id:
                request_data = data
                request_item = item
                break

    if not request_data:
        return {"ok": False, "error": f"Request {request_id} not found"}

    if request_data.get("status") != "SENT":
        return {"ok": False, "error": f"Request is not in SENT status (current: {request_data.get('status')})"}

    # Update the request
    now = datetime.now(timezone.utc).isoformat()
    request_data["status"] = response_type
    if response_type == "CONFIRMED":
        request_data["confirmed_at"] = now
    request_data["simulated_by"] = "operator_console"
    request_data["simulated_at"] = now

    # Write back
    table.put_item(
        Item={
            "PK": request_item["PK"],
            "SK": request_item["SK"],
            "entity": "REQUEST",
            "data": json.dumps(request_data),
        }
    )

    # Record this as an operator action (not an agent action)
    action_id = f"act_{uuid.uuid4().hex[:8]}"
    case_id = request_data.get("case_id")
    action_data = {
        "action_id": action_id,
        "tool_name": "simulate_provider_response",
        "operator_action": True,
        "simulated": True,
        "request_id": request_id,
        "response_type": response_type,
        "case_id": case_id,
        "created_at": now,
    }

    table.put_item(
        Item={
            "PK": f"CASE#{case_id}",
            "SK": f"ACTION#{now}#{uuid.uuid4().hex[:8]}",
            "entity": "ACTION",
            "data": json.dumps(action_data),
        }
    )

    # Re-queue the case for AgentCore so it can verify and close
    # Only re-queue on CONFIRMED (declined cases need human decision)
    if response_type == "CONFIRMED":
        _requeue_case_for_agent(case_id, table)

    return {"ok": True, "request_id": request_id, "status": response_type, "simulated": True}


def _render_html(
    cases: list[dict],
    pending_requests: list[dict] | None = None,
    events: dict[str, dict] | None = None,
    decisions: dict[str, dict] | None = None,
    actions: dict[str, list[dict]] | None = None,
) -> str:
    """Render the dashboard HTML using the render module."""
    return render.page(cases, pending_requests, events, decisions, actions)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for Function URL requests."""

    # Get request info
    raw_path = event.get("rawPath", "/")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    # CORS headers for all responses
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

    # Handle OPTIONS preflight
    if method == "OPTIONS":
        return {"statusCode": 200, "headers": cors_headers, "body": ""}

    if raw_path == "/api/cases":
        # JSON API endpoint
        cases = _get_cases()
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json", **cors_headers},
            "body": json.dumps(cases),
        }

    elif raw_path == "/api/pending-requests":
        # Get pending provider requests
        pending = _get_pending_requests()
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json", **cors_headers},
            "body": json.dumps(pending),
        }

    elif raw_path == "/api/simulate-provider-response" and method == "POST":
        # Simulate provider response (operator action)
        try:
            body = json.loads(event.get("body", "{}"))
            request_id = body.get("request_id")
            response_type = body.get("response_type", "CONFIRMED")

            if not request_id:
                return {
                    "statusCode": 400,
                    "headers": {"Content-Type": "application/json", **cors_headers},
                    "body": json.dumps({"ok": False, "error": "request_id is required"}),
                }

            if response_type not in ("CONFIRMED", "DECLINED"):
                return {
                    "statusCode": 400,
                    "headers": {"Content-Type": "application/json", **cors_headers},
                    "body": json.dumps({"ok": False, "error": "response_type must be CONFIRMED or DECLINED"}),
                }

            result = _simulate_provider_response(request_id, response_type)
            status_code = 200 if result.get("ok") else 400
            return {
                "statusCode": status_code,
                "headers": {"Content-Type": "application/json", **cors_headers},
                "body": json.dumps(result),
            }
        except Exception as e:
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json", **cors_headers},
                "body": json.dumps({"ok": False, "error": str(e)}),
            }

    else:
        # HTML dashboard
        cases = _get_cases()
        pending_requests = _get_pending_requests()
        events = _get_events(cases)
        decisions = {
            str(c.get("case_id")): _get_case_decision(str(c.get("case_id")))
            for c in cases
            if c.get("state") == "AWAITING_DECISION"
        }
        actions = _get_actions(cases)
        html = _render_html(cases, pending_requests, events, decisions, actions)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": html,
        }
