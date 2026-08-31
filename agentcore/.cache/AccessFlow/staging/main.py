"""AccessFlow — AgentCore Runtime entrypoint.

This is a thin transport wrapper. All behaviour lives in
backend/app/agents/orchestrator.py, which is the same module the local
runner and the 110 tests exercise. Nothing here makes a decision.

Contract:
    request : {"meeting": {"key","body_name","date","time","agenda_url"}}
    response: {"meeting_key","body_name","event_date","model_calls",
               "spent_usd","in_tokens","out_tokens","stop_reason", ...}
"""
from __future__ import annotations

import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from backend.app.agents.orchestrator import build_orchestrator, run_case

app = BedrockAgentCoreApp()
log = app.logger or logging.getLogger(__name__)

REQUIRED_FIELDS = ("key", "body_name", "date")


@app.entrypoint
def invoke(payload, context):
    """One invocation == one case.

    A FRESH agent is built per invocation on purpose. Reusing one agent
    across cases would carry conversation history between unrelated
    meetings — which is both a correctness bug and the fastest way to
    burn the token budget.
    """
    if not isinstance(payload, dict):
        return {"error": "payload must be a JSON object"}

    meeting = payload.get("meeting")
    if not isinstance(meeting, dict):
        return {"error": "payload.meeting must be an object"}

    missing = [f for f in REQUIRED_FIELDS if not meeting.get(f)]
    if missing:
        return {"error": f"meeting missing required field(s): {', '.join(missing)}"}

    log.info("AccessFlow runtime invoked for meeting %s", meeting["key"])

    agent, budgeted_model, _cedar = build_orchestrator()
    result = run_case(agent, meeting, budgeted_model)

    # AgentResult is not JSON-serialisable and would fail the HTTP response.
    result.pop("result", None)
    return result


if __name__ == "__main__":
    app.run()
