"""
Model provider selection — the thing that keeps AccessFlow inside $50.

Bedrock is NOT required by the contest. Official Rules, verbatim:
  "Deploying with Amazon Bedrock AgentCore is a smart architectural choice and
   will strengthen your Technical Implementation score, but it's not required."

So: develop against Anthropic direct, demo and deploy on Bedrock. One env var.

    MODEL_PROVIDER=anthropic   (default)  -> your own API key, off the $50
    MODEL_PROVIDER=bedrock                -> the $50, use for demo + judging

⚠️ DO NOT add an Ollama path. `ollama.py:325` calls
   warn_on_tool_choice_not_supported() — Ollama SILENTLY IGNORES tool_choice,
   which Bedrock enforces. The ASK path in this product depends on forcing
   request_human_decision. It would work on Bedrock and be ignored locally:
   opposite behaviour in the exact system being demoed.
"""

from __future__ import annotations

import os


def get_model():
    provider = os.getenv("MODEL_PROVIDER", "anthropic").lower()

    if provider == "bedrock":
        from strands.models import BedrockModel
        return BedrockModel(
            # the "us." inference-profile prefix is required on Bedrock
            model_id=os.getenv("BEDROCK_MODEL_ID",
                               "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
            region_name=os.getenv("AWS_REGION", "us-west-2"),
            # cache_prompt deprecated in strands 1.53 — use SystemContentBlock cachePoint
        )

    from strands.models.anthropic import AnthropicModel
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set.\n"
            "  Either: export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  Or run against Bedrock: MODEL_PROVIDER=bedrock python ..."
        )
    return AnthropicModel(
        client_args={"api_key": key},
        # no "us." prefix on the direct API
        model_id=os.getenv("ANTHROPIC_MODEL_ID", "claude-haiku-4-5-20251001"),
    )


def describe() -> str:
    p = os.getenv("MODEL_PROVIDER", "anthropic").lower()
    return f"provider={p} " + (
        "(billed to AWS credits)" if p == "bedrock" else "(billed to your Anthropic key)"
    )
