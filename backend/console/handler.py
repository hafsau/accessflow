"""
AccessFlow Operations Console — public, read-only dashboard.

Single Lambda with Function URL serving:
- GET / → HTML dashboard with case list and decision queue
- GET /api/cases → JSON array of cases

AuthType: NONE (public, no credentials required).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
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


def _render_html(cases: list[dict]) -> str:
    """Render the dashboard HTML."""

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
                provider = opt.get("provider_id", "?")
                service = opt.get("service_type", "?")
                score = opt.get("score", 0)
                options_html += f'<div class="option">Provider: {provider} | Service: {service} | Score: {score:.1f}</div>'
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
        .simulated-notice {{
            margin-top: 10px;
            padding: 8px;
            background: #fff3cd;
            border-radius: 4px;
            font-size: 0.8em;
            color: #856404;
        }}
        .empty {{ color: #7f8c8d; font-style: italic; }}
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

        <div class="simulated-notice" style="margin-top: 20px;">
            <strong>Notice:</strong> All provider interactions shown are simulated.
            The 7 providers listed are seeded fixtures for demonstration purposes, not real vendors.
            This console is read-only.
        </div>
    </div>
</body>
</html>"""

    return html


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for Function URL requests."""

    # Get request path
    raw_path = event.get("rawPath", "/")

    if raw_path == "/api/cases":
        # JSON API endpoint
        cases = _get_cases()
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps(cases),
        }
    else:
        # HTML dashboard
        cases = _get_cases()
        html = _render_html(cases)
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "text/html; charset=utf-8",
            },
            "body": html,
        }
