# Scope freeze — AccessFlow

**Frozen 2026-08-27.** Nothing on the CUT list gets rebuilt. Nothing new gets added. From here the only permitted work is making what's on the KEEP list better.

Scope was ~170 hours against ~85 hours of capacity to Sep 11. That is a 2× arithmetic failure, not a slippage risk. These cuts are what make it shippable.

## CUT — decided, not up for renegotiation

| # | Cut | Was | Now | Hours saved | Score impact |
|---|---|---|---|---|---|
| 1 | Tools | 15 | **8** | 6 | **none** |
| 2 | Seeded providers | 6 | **3** | 2 | **none** |
| 3 | Cedar policy | 162 lines | **60–80 lines, 6 tested forbids** | 8 | **none** — demos identically |
| 4 | Strands Evals + chaos testing | full suite | **one README paragraph** | 6 | −0.1 |
| 5 | Durable resume | SDK mid-tool resume | **case-record durability at state boundaries** | 8 | −0.05, arguably + |
| 6 | `LLMSteeringHandler` | in scope | **dropped** | 4 | −0.1 |
| 7 | Console screens | 5 | **2 — dashboard + decision queue** | 8 | −0.05 |
| 8 | Verification | separate agent | **a tool** (Cedar keeps the invariant) | 3 | −0.05 |

**Total saved: ~45 hours. Total score cost: ~0.35 of 5.**

### The 8 tools — this list is closed
`get_case` · `get_event` · `poll_public_meetings` · `fetch_agenda_document` · `search_providers` · `send_provider_request` · `request_human_decision` · `verify_fulfillment` · `close_case`

### Why cut 5 is not a loss
[harness-sdk #859](https://github.com/strands-agents/harness-sdk/issues/859): *"Session management fails to resume when previous session ended during tool execution."* The session persists a `tool_use` with no matching `tool_result` and Bedrock throws `ValidationException` on resume. The original demo beat — `kill -9` **mid-batch** — is definitionally that bug.

Kill the process at a **state-machine boundary you persist yourself**. The claim becomes *"the case survives process death, and every tool is idempotent, so re-execution is safe."* True, stronger, and 2 hours instead of 10.

## KEEP — protect in this order

1. **Three builder.aws.com blog posts** — +0.6 on a 1–5 scale for ~1.5 days. Worth more than the entire Cedar centrepiece (+0.4) at a third of the cost and none of the risk. Titles must contain "Agents for Humans."
2. **The video** — 20% of the score, and the first honest end-to-end test of the product. Budget a full day.
3. **Cedar as the authority model** — the Creativity differentiator. Near-zero public samples.
4. **The two console screens** — Design is the 5/5 and it is 20%.
5. **The real Legistar feed** — what makes the demo truthful.

## Known trade-off accepted 2026-08-27

**Constraint 5 (portfolio diversity) is knowingly waived.** AccessFlow is an accessibility product — ASL interpreters, captioning, accessible materials, mobility access — and it joins Braille Tonight and Persona Lab as a third accessibility-adjacent concept. Re-domaining the engine (~2 days) was available Aug 26 and was declined.

**Portfolio framing consequence:** present AccessFlow as **civic infrastructure — public bodies meeting published obligations** — not as a product for disabled users. That is a true and materially different framing from the two rejected concepts, and it is the one to use on the portfolio site and in interviews.

## Checkpoint rule

At the **Sep 5 checkpoint**, if the agent loop is not solid, cut further before adding anything:
- console 2 screens → **1** (dashboard with an inline decision panel)
- Cedar 6 forbids → **4**
- watched jurisdictions → **1** (Seattle only)

An agent will always try to build everything. That decision is a human one.
