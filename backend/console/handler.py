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

    return cases


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
    action_data = {
        "action_id": action_id,
        "tool_name": "simulate_provider_response",
        "operator_action": True,
        "simulated": True,
        "request_id": request_id,
        "response_type": response_type,
        "case_id": request_data.get("case_id"),
        "created_at": now,
    }

    table.put_item(
        Item={
            "PK": f"CASE#{request_data.get('case_id')}",
            "SK": f"ACTION#{now}#{uuid.uuid4().hex[:8]}",
            "entity": "ACTION",
            "data": json.dumps(action_data),
        }
    )

    return {"ok": True, "request_id": request_id, "status": response_type, "simulated": True}


def _render_html(cases: list[dict], pending_requests: list[dict] | None = None) -> str:
    """Render the dashboard HTML."""
    pending_requests = pending_requests or []

    # Split cases by state
    awaiting = [c for c in cases if c.get("state") == "AWAITING_DECISION"]
    other = [c for c in cases if c.get("state") != "AWAITING_DECISION"]

    def format_date(iso: str | None) -> str:
        if not iso:
            return "—"
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return iso[:16] if len(iso) > 16 else iso

    def format_obligations(obs: list) -> str:
        if not obs:
            return "—"
        return ", ".join(o.get("category", "?").replace("_", " ").title() for o in obs)

    def state_badge(state: str) -> str:
        colors = {
            "AWAITING_DECISION": "badge-warning",
            "IN_PROGRESS": "badge-info",
            "CLOSED": "badge-success",
            "CANCELLED": "badge-secondary",
        }
        cls = colors.get(state, "badge-secondary")
        return f'<span class="badge {cls}">{state}</span>'

    # Build case rows for main table
    case_rows = ""
    for c in cases:
        case_rows += f"""
        <tr>
            <td><code>{c.get('case_id', '?')[:12]}</code></td>
            <td>{c.get('event_id', '?')}</td>
            <td>{state_badge(c.get('state', '?'))}</td>
            <td>{format_obligations(c.get('obligations', []))}</td>
            <td>{format_date(c.get('created_at'))}</td>
        </tr>
        """

    # Build decision queue
    decision_rows = ""
    for c in awaiting:
        decision = _get_case_decision(c.get("case_id", ""))
        options_html = ""
        if decision and decision.get("options"):
            for opt in decision.get("options", []):
                description = opt.get("description", "No description")
                recommended = opt.get("recommended", False)
                rec_badge = ' <span class="recommended">[RECOMMENDED]</span>' if recommended else ''
                options_html += f'<div class="option">{description}{rec_badge}</div>'
        else:
            options_html = '<div class="option">No options computed yet</div>'

        decision_rows += f"""
        <div class="decision-card">
            <div class="decision-header">
                <strong>{c.get('event_id', '?')}</strong>
                <span class="case-id">Case: {c.get('case_id', '?')[:12]}</span>
            </div>
            <div class="obligations">
                Obligations: {format_obligations(c.get('obligations', []))}
            </div>
            <div class="options-list">
                <strong>Available Options:</strong>
                {options_html}
            </div>
            <div class="simulated-notice">
                Providers are seeded fixtures, not real vendors. All interactions are simulated.
            </div>
        </div>
        """

    if not decision_rows:
        decision_rows = '<p class="empty">No cases awaiting decision.</p>'

    # Build pending provider requests section
    pending_rows = ""
    for req in pending_requests:
        req_id = req.get("request_id", "?")
        provider = req.get("provider_id", "?")
        case_id = req.get("case_id", "?")
        sent_at = format_date(req.get("sent_at"))
        pending_rows += f"""
        <div class="provider-request-card">
            <div class="request-header">
                <strong>Request: <code>{req_id[:16]}</code></strong>
                <span class="case-id">Case: {case_id[:12]}</span>
            </div>
            <div class="request-details">
                Provider: <strong>{provider}</strong> | Sent: {sent_at}
            </div>
            <div class="simulate-controls">
                <button class="btn btn-confirm" onclick="simulateResponse('{req_id}', 'CONFIRMED')">
                    Simulate: Provider Confirms
                </button>
                <button class="btn btn-decline" onclick="simulateResponse('{req_id}', 'DECLINED')">
                    Simulate: Provider Declines
                </button>
            </div>
            <div class="simulated-notice">
                This is a SIMULATED provider response triggered by an operator. Not a real vendor interaction.
            </div>
        </div>
        """

    if not pending_rows:
        pending_rows = '<p class="empty">No provider requests awaiting response.</p>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AccessFlow Operations Console</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
            color: #333;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ color: #2c3e50; margin-bottom: 5px; }}
        .subtitle {{ color: #7f8c8d; margin-bottom: 20px; }}
        .section {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        h2 {{ color: #34495e; margin-top: 0; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #f8f9fa; font-weight: 600; color: #2c3e50; }}
        tr:hover {{ background: #f8f9fa; }}
        code {{ background: #f1f3f4; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.85em;
            font-weight: 500;
        }}
        .badge-warning {{ background: #fff3cd; color: #856404; }}
        .badge-info {{ background: #d1ecf1; color: #0c5460; }}
        .badge-success {{ background: #d4edda; color: #155724; }}
        .badge-secondary {{ background: #e2e3e5; color: #383d41; }}
        .decision-card {{
            background: #fffbf0;
            border: 1px solid #ffc107;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 15px;
        }}
        .decision-header {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
        }}
        .case-id {{ color: #7f8c8d; font-size: 0.9em; }}
        .obligations {{ margin-bottom: 10px; color: #555; }}
        .options-list {{ margin-top: 10px; }}
        .option {{
            background: white;
            padding: 8px 12px;
            margin: 5px 0;
            border-radius: 4px;
            border: 1px solid #ddd;
            font-size: 0.9em;
        }}
        .recommended {{
            color: #155724;
            background: #d4edda;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.85em;
            font-weight: bold;
            margin-left: 8px;
        }}
        .simulated-notice {{
            margin-top: 10px;
            padding: 8px;
            background: #fff3cd;
            border-radius: 4px;
            font-size: 0.8em;
            color: #856404;
        }}
        .empty {{ color: #7f8c8d; font-style: italic; }}
        .provider-request-card {{
            background: #e8f4fd;
            border: 1px solid #3498db;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 15px;
        }}
        .request-header {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
        }}
        .request-details {{ margin-bottom: 10px; color: #555; }}
        .simulate-controls {{
            display: flex;
            gap: 10px;
            margin: 10px 0;
        }}
        .btn {{
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.9em;
            font-weight: 500;
        }}
        .btn-confirm {{
            background: #27ae60;
            color: white;
        }}
        .btn-confirm:hover {{ background: #219a52; }}
        .btn-decline {{
            background: #e74c3c;
            color: white;
        }}
        .btn-decline:hover {{ background: #c0392b; }}
        .btn:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
        }}
        .stats {{
            display: flex;
            gap: 20px;
            margin-bottom: 15px;
        }}
        .stat {{
            background: #e8f4fd;
            padding: 10px 15px;
            border-radius: 6px;
        }}
        .stat-value {{ font-size: 1.5em; font-weight: bold; color: #2c3e50; }}
        .stat-label {{ font-size: 0.9em; color: #7f8c8d; }}
        .api-link {{
            float: right;
            color: #3498db;
            text-decoration: none;
            font-size: 0.9em;
        }}
        .api-link:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>AccessFlow Operations Console</h1>
        <p class="subtitle">Real-time accessibility accommodation case management</p>

        <div class="section">
            <a href="/api/cases" class="api-link">JSON API &rarr;</a>
            <h2>Dashboard</h2>
            <div class="stats">
                <div class="stat">
                    <div class="stat-value">{len(cases)}</div>
                    <div class="stat-label">Total Cases</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{len(awaiting)}</div>
                    <div class="stat-label">Awaiting Decision</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{len(pending_requests)}</div>
                    <div class="stat-label">Pending Requests</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{len([c for c in cases if c.get('state') == 'CLOSED'])}</div>
                    <div class="stat-label">Closed</div>
                </div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Case ID</th>
                        <th>Event</th>
                        <th>State</th>
                        <th>Obligations</th>
                        <th>Created</th>
                    </tr>
                </thead>
                <tbody>
                    {case_rows if case_rows else '<tr><td colspan="5" class="empty">No cases yet.</td></tr>'}
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2>Decision Queue</h2>
            <p style="color: #7f8c8d; font-size: 0.9em;">Cases requiring human review before accommodation fulfillment.</p>
            {decision_rows}
        </div>

        <div class="section">
            <h2>Pending Provider Requests</h2>
            <p style="color: #7f8c8d; font-size: 0.9em;">
                Provider requests awaiting response. Use the buttons below to <strong>simulate</strong> a provider response.
                This is an <strong>operator action</strong> for demonstration purposes.
            </p>
            {pending_rows}
        </div>

        <div class="simulated-notice" style="margin-top: 20px;">
            <strong>Notice:</strong> All provider interactions shown are simulated.
            The 6 providers listed are seeded fixtures for demonstration purposes, not real vendors.
        </div>
    </div>

    <script>
    async function simulateResponse(requestId, responseType) {{
        const btn = event.target;
        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Processing...';

        try {{
            const response = await fetch('/api/simulate-provider-response', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ request_id: requestId, response_type: responseType }})
            }});
            const result = await response.json();

            if (result.ok) {{
                alert('Provider response simulated: ' + responseType + '\\n\\nThis was an OPERATOR action, not an agent action. Refresh to see updated state.');
                location.reload();
            }} else {{
                alert('Error: ' + (result.error || 'Unknown error'));
                btn.disabled = false;
                btn.textContent = originalText;
            }}
        }} catch (e) {{
            alert('Request failed: ' + e.message);
            btn.disabled = false;
            btn.textContent = originalText;
        }}
    }}
    </script>
</body>
</html>"""

    return html


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
        html = _render_html(cases, pending_requests)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": html,
        }
