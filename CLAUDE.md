# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AccessFlow is an AI agent that coordinates accessibility accommodations for public meetings. It ingests real public meeting data from the Legistar Web API (used by US state/local governments), derives ADA Title II obligations, and coordinates accommodation providers.

Key differentiators:
- **Real inbound edge**: Cases come from live Legistar feeds, not simulated data
- **Cedar authority model**: Tool access is policy-enforced via Cedar (AWS's policy language), not prompt instructions
- **Fail-closed verification**: A case cannot close without verified evidence—enforced by Cedar, not application code

## Architecture

```
backend/
  app/
    agents/
      authority.py     # Cedar + LLM steering wiring (TracingCedarAuthorization)
      orchestrator.py  # Single-agent case orchestrator with 12 tools
      budget.py        # Daily USD cap enforcement via DynamoDB
    domain/
      state.py         # 10-state machine, explicit transitions, append-only audit
    tools/
      case_tools.py    # 12 @tool implementations (Pydantic responses, idempotency)
      legistar.py      # Real-time Legistar feed client, change detection
    models/
      dynamo_store.py  # DynamoDB persistence layer
      domain.py        # Pydantic models for all tool I/O
  poller/
    poller_handler.py  # Lambda: polls Legistar, queues changed meetings
    worker_handler.py  # Lambda: invokes AgentCore runtime per meeting
  console/
    handler.py         # Lambda Function URL: read-only operations dashboard
policies/
  accessflow.cedar     # THE authority model—ACT/ASK/BLOCK matrix as enforceable policy
infrastructure/
  template.yaml        # SAM template: Poller + Worker + Console Lambdas
agentcore/
  agentcore.json       # AgentCore runtime config
  runtime/main.py      # AgentCore entrypoint
```

### Data Flow
1. **Poller Lambda** (EventBridge, every 15 min) → polls Legistar, fingerprints changes
2. Changed meetings → **SQS queue** → **Worker Lambda**
3. Worker invokes **AgentCore Runtime** (Strands SDK + Cedar)
4. Cedar checks every tool call against `accessflow.cedar` before execution
5. Cases persist to **DynamoDB** (`accessflow-core`), audit trail is append-only

### Case State Machine
`NEW → ANALYZING → PLANNING → COORDINATING → WAITING → VERIFYING → CLOSED`
Special states: `ASK` (human decision needed), `BLOCKED`, `CANCELLED`, `REOPENED`

## Commands

### Environment Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install 'strands-agents==1.53.0' 'strands-agents-tools==0.8.6' 'strands-agents[cedar]' anthropic
```

### Run Tests
```bash
# Offline tests (no AWS required)
pytest -q

# Integration tests (requires DynamoDB table)
CORE_TABLE=accessflow-core pytest -q

# Single test file
pytest backend/tests/agents/test_cedar_authority.py -v
```

### Run a Single Case Locally
```bash
# Walk through state machine without LLM
python scripts/walk_one_case.py

# Full orchestrator run (requires MODEL_PROVIDER)
MODEL_PROVIDER=anthropic CORE_TABLE=accessflow-core python scripts/run_one_case.py
```

### Verify Legistar Feed Access
```bash
# Must use $orderby or you get 2015 data
curl -s "https://webapi.legistar.com/v1/seattle/Events?\$top=3&\$orderby=EventLastModifiedUtc+desc"
```

### AgentCore CLI
```bash
npm install -g @aws/agentcore
agentcore dev           # run locally
agentcore deploy        # deploy to AWS (ARM64 only)
agentcore invoke --prompt "test" --session-id "$(uuidgen)"
```

### Deploy Lambda Infrastructure
```bash
cd infrastructure
sam build && sam deploy --guided --capabilities CAPABILITY_IAM --region us-west-2
```

## Key Technical Constraints

### Cedar Policy
- **Default-deny, fail-closed**: Every tool call must match a `permit`; `forbid` always wins
- **`context.input.*` is model-controlled**: Never trust it for authorization. Only `context.session.*` (from `context_enricher` in `authority.py:133`) is trustworthy
- **Silent error-skip**: Cedar skips policies that error during evaluation. Every `context.*` dereference MUST be `has`-guarded
- **The invariant**: `close_case` requires `context.session.verification_passed == true`—set by persisted state, never by model output

### Model Strategy (Budget: $50)
- **Tier 0 (free)**: Cedar tests, tool contracts, state machine, feed client—no model needed
- **Tier 1 (dev)**: Use Anthropic API direct (`MODEL_PROVIDER=anthropic`)
- **Tier 2 (demo)**: Use Bedrock Haiku 4.5 (`MODEL_PROVIDER=bedrock`)

Do NOT use Ollama—it silently ignores `tool_choice`, which Bedrock enforces.

### Cost Traps
- **Never provision a NAT Gateway**: $47.52 over 44 days (95% of budget)
- **Never run the poller inside AgentCore Runtime**: Use Lambda free tier
- **Use SSM Parameter Store** for secrets (free), not Secrets Manager ($0.40/secret/mo)
- **Daily cap**: `BudgetedModel` in `orchestrator.py:75` enforces `DAILY_USD_CAP` (default $1.50)

### ADA Legal Basis
Two obligations per meeting:
1. **§35.160 (effective communication)**: Active since 1991—interpreter/CART coordination
2. **Subpart H (WCAG 2.1 AA)**: April 26 2027 (50k+ population) / April 26 2028 (smaller)—agenda documents only

### Legistar API
- No API key required
- Always use `$orderby`—`$top` alone returns oldest rows
- Verified namespaces: `seattle`, `alameda`, `oakland`, `sanjose`, `kingcounty`, `sacramento`

## Development Guidelines

### Do NOT Modify Without Review
- `policies/accessflow.cedar` — Verified authority model with 0 unguarded derefs
- `backend/app/agents/authority.py` — Verified against live Strands APIs
- `backend/app/tools/legistar.py` — Verified against live Legistar
- `backend/app/domain/state.py` — State machine with explicit transition table

### Tool Implementation Pattern
All tools in `case_tools.py` follow:
```python
@tool
def my_tool(idempotency_key: str, ...) -> dict[str, Any]:
    """Docstring becomes tool description for the LLM."""
    store = get_store()
    # ... validation ...
    if error:
        return _error(ErrorCode.XXX, "message")
    return MyResponse(...).model_dump()
```

### Testing Cedar Policies
The critical test that prevents fail-open:
```python
def test_close_case_denied_with_empty_enricher():
    cedar = CedarAuthorization(
        policies="policies/accessflow.cedar",
        principal_resolver=lambda s: {"type": "Coordinator", "id": "c1"},
        context_enricher=lambda ctx: {},  # empty = the failure case
        on_error="deny")
    assert denied(cedar, "close_case", {"verification_id": "totally_made_up"})
```

### AgentCore Deploy Troubleshooting
| Symptom | Cause |
|---|---|
| `exec format error` | Image not ARM64 |
| 504 Gateway Timeout | Missing port 8080 or `/invocations` endpoint |
| Bug appears unfixed after deploy | Reused session ID—use fresh `--session-id` |
| Cedar denials in agent loop | Check `context_enricher` returns expected keys |

## Environment Variables
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CORE_TABLE` | Yes | — | DynamoDB table for cases/actions |
| `MODEL_PROVIDER` | No | `bedrock` | `bedrock` or `anthropic` |
| `DAILY_USD_CAP` | No | `1.50` | Daily spend cap in USD |
| `BUDGET_TABLE` | No | `accessflow-budget` | DynamoDB table for budget tracking |

## Pinned Dependencies
```
strands-agents==1.53.0
strands-agents-tools==0.8.6
strands-agents[cedar]
boto3>=1.26.0
httpx>=0.24.0
```
