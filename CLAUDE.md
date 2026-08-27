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
      authority.py    # Cedar + LLM steering wiring (CedarAuthorization, LLMSteeringHandler)
    tools/
      legistar.py     # Real-time Legistar feed client, change detection, obligation derivation
policies/
  accessflow.cedar    # THE authority model—ACT/ASK/BLOCK matrix as enforceable policy
  entities.json       # Cedar entity store stub
docs/
  INSTRUCTIONS.md     # Day-by-day build runbook with exit criteria
  SPEC-V2-PATCH.md    # Spec changes: real feed, Cedar authority, updated demo script
  BUILD-RUNBOOK.md    # AWS setup, cost guards, deployment steps
```

## Commands

### Environment Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install httpx 'strands-agents==1.53.0' 'strands-agents-tools==0.8.6' 'strands-agents[cedar]' anthropic
```

### Verify Legistar Feed Access
```bash
# Must use $orderby or you get 2015 data
curl -s "https://webapi.legistar.com/v1/seattle/Events?\$top=3&\$orderby=EventLastModifiedUtc+desc"
```

### Run the Poller (Required for Demo)
```bash
python -c "
from backend.app.tools.legistar import LegistarFeed
import json, time, datetime
f = LegistarFeed()
while True:
    new, ch = f.poll()
    print(json.dumps({'t': datetime.datetime.utcnow().isoformat(),
                      'new': len(new), 'changes': len(ch)}), flush=True)
    time.sleep(900)
" >> feed-measure.jsonl
```

### AgentCore CLI
```bash
npm install -g @aws/agentcore
agentcore create        # scaffold new agent
agentcore dev           # run locally
agentcore deploy        # deploy to AWS (ARM64 only)
agentcore invoke --prompt "test"
```

### AWS Bedrock Test
```bash
aws bedrock-runtime converse --region us-west-2 \
  --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --messages '[{"role":"user","content":[{"text":"say ready"}]}]'
```

## Key Technical Constraints

### Cedar Policy
- **Default-deny, fail-closed**: Every tool call must match a `permit`; `forbid` always wins
- **`context.input.*` is model-controlled**: Never trust it for authorization. Only `context.session.*` (from `context_enricher`) is trustworthy
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

### ADA Legal Basis
Two obligations per meeting:
1. **§35.160 (effective communication)**: Active since 1991—interpreter/CART coordination
2. **Subpart H (WCAG 2.1 AA)**: April 26 2027 (50k+ population) / April 26 2028 (smaller)—agenda documents only

### Legistar API
- No API key required
- Always use `$orderby`—`$top` alone returns oldest rows
- Verified namespaces: `seattle`, `alameda`. Check others with `GET {BASE}/{client}/Bodies?$top=1`

## Development Guidelines

### Do NOT Modify
- `policies/accessflow.cedar` — Verified authority model with 0 unguarded derefs
- `backend/app/agents/authority.py` — Verified against live Strands APIs
- `backend/app/tools/legistar.py` — Verified against live Legistar

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

## Pinned Dependencies
```
strands-agents==1.53.0
strands-agents-tools==0.8.6
strands-agents[cedar]
```
