# Day 5 — the orchestrator

**One agent. Not a graph.**

## Why one agent

AWS's own engineering blog measured **steering hooks at 100% accuracy against graph workflows at 80.8%**, and explicitly warns against decomposing tasks into separate agents within graph workflows ([One Year of Building Production Agents](https://strandsagents.com/blog/what-we-learned-from-one-year-of-building-production-agents/)).

Two more reasons specific to this entry:
- `GraphBuilder` / `Swarm` are the **most-sampled** Strands surface. Every official example shows them. Creativity asks for a *non-obvious* use — a graph is the obvious one.
- The 1st-place winner of the last AWS agent hackathon used **one** agent.

Put the AWS figure in the README as the stated reason. A judge who reads their own team's blog will register that you did too.

## Shape

```
Case Orchestrator (one Agent)
├── interventions=[TracingCedarAuthorization]     ← the hard boundary
└── tools:
    ├── deterministic (10)   poll_public_meetings · get_case · get_event ·
    │                        fetch_agenda_document · derive_obligations ·
    │                        search_providers · send_provider_request ·
    │                        request_human_decision · verify_fulfillment · close_case
    └── model-bearing (1)    extract_accommodation_policy
```

**Ten of eleven tools call no model.** That is the cost architecture and the honest answer to "where is the LLM actually doing work?"

## The system prompt

Keep it in `backend/app/agents/prompts/orchestrator.md`, versioned, not inline. It must say:

- You own the case from meeting to verified closure.
- Obligations, deadlines, cancellations and state transitions are **computed by tools**. Never assert them yourself; call the tool and use what it returns.
- Your judgment is used for exactly one thing: reading the agenda and extracting the accommodation policy that body has published, with an exact quote for every claim.
- You cannot close a case. `close_case` is gated by policy. If verification has not passed, do not attempt it — request a human decision instead.
- When you cannot proceed safely, call `request_human_decision` with: what changed · what you checked · the safe options · your recommendation · why you stopped.
- Never invent a provider, an availability, a confirmation, or an agenda item.

## Config
```python
Agent(
    model=get_model(),
    tools=[...11...],
    system_prompt=Path("backend/app/agents/prompts/orchestrator.md").read_text(),
    interventions=[TracingCedarAuthorization(...)],
    context_manager="auto",     # offloads large tool results, compresses old turns
)
```

`context_manager="auto"` matters here because `fetch_agenda_document` returns up to 12,000 characters and the loop resends history every turn. Without it, a 15-turn case is where the $50 goes.

## Cost control — wire it in today, not later

A multi-step loop resends the whole history each turn: roughly **$1 per case at Sonnet rates**, and twenty demo rehearsals is $200+. Before every Bedrock invocation:

```python
from backend.app.agents.budget import check_and_charge, estimate
check_and_charge(estimate(in_tokens, out_tokens, cached_in))
```

`DAILY_USD_CAP` defaults to $1.50. Raise it deliberately or not at all.

## Exit criterion

One real meeting from the live feed, start to finish, on **Bedrock Haiku 4.5**:

```bash
MODEL_PROVIDER=bedrock python scripts/run_one_case.py
```

Must print, and all four must be true:
```
case_xxx  seattle:6860  "City Council" 2026-09-08
  ... transitions ...
  N model calls · $0.0xx spent · CLOSED · verification_passed=True
```

**Then record the number.** Cost-per-case × rehearsal count is your real budget, and it decides how many takes you can afford on Sep 9. That single figure is the most useful thing today produces.

## Checklist
- [ ] `orchestrator.md` system prompt, versioned in its own file
- [ ] One `Agent` with 11 tools and the Cedar intervention attached
- [ ] `context_manager="auto"` set
- [ ] `check_and_charge()` called before every Bedrock invocation
- [ ] `scripts/run_one_case.py` walks a real meeting end to end
- [ ] **Cost per case measured and written into `docs/CUTS.md`**
- [ ] A test asserting the agent cannot close a case when `verification_passed` is False — the Cedar denial must appear in the trace
- [ ] `pytest backend/tests/` green
