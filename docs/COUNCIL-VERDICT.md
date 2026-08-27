# Council verdict — AccessFlow

_Convened 2026-08-26. Four seats: cost engineer, rules auditor, delivery risk, adversary. The adversary installed `strands-agents==1.53.0` and `cedarpy==4.8.7` and executed the policy rather than reading it._

**Headline: three of my own claims were wrong. Two of them were load-bearing. All three now have tested fixes, already applied.**

Also: **today is Aug 26, not Aug 25.** 19 days, not 20.

---

## PART 1 — MY ERRORS

### E1 · The Cedar invariant failed OPEN. Proven by execution.

The whole pitch was *"it cannot close a case it did not verify — the model never sees the key."* The adversary ran my exact policy through the real handler with `on_error="deny"`:

| Scenario | Result |
|---|---|
| verified case, enricher OK | `Proceed` ✅ |
| no `verification_id` — **the demo beat** | `Deny` ✅ |
| enricher says not verified | `Deny` ✅ |
| **enricher silent, model invents an id** | **`Proceed` ❌** |
| **no enricher configured** | **`Proceed` ❌** |

**The case closes, unverified, on a `verification_id` the model made up.**

Mechanism, confirmed at three levels:
1. `context["input"] = event.tool_use.get("input")` — `context.input` **is** model-generated tool arguments. The model controls `verification_id` completely. Only `context.session.*` is trustworthy.
2. Dereferencing a **missing** attribute *errors* in Cedar, and [Cedar silently skips any policy that errors](https://cedarland.blog/design/why-ignore-errors/content.html) — *"treating the situation as if the policy never existed."*
3. `&&` short-circuits, which is why case 2 passed and hid the bug.

`on_error="deny"` does not save it — that covers *engine* failure, not per-policy error-skip.

**Fix, tested, all five cases now correct — already applied to `policies/accessflow.cedar`:**

```cedar
forbid(principal, action == Action::"close_case", resource)
unless {
  context has session &&
  context.session has verification_passed &&
  context.session.verification_passed == true &&
  context has input &&
  context.input has verification_id
};
```

Every other `context.*` deref in the file is now `has`-guarded too — **verified 0 unguarded across all 22 policies.**

**The test that would have caught it, and must exist before Cedar policy #2 is written:**

```python
def test_close_case_denied_with_empty_enricher():
    """The model can invent verification_id. Only session state is trustworthy."""
    cedar = CedarAuthorization(policies="policies/accessflow.cedar",
                               principal_resolver=lambda s: {"type":"Coordinator","id":"c1"},
                               context_enricher=lambda ctx: {},   # <-- the failure case
                               on_error="deny")
    assert deny(cedar, "close_case", {"verification_id": "totally_made_up"})
```

**And a sequencing instruction from the delivery seat:** because Cedar skips erroring policies silently, a typo in a `permit` is *byte-identical* to "no policy matched." **Build the diagnostic harness — log decision + determining policy IDs + evaluation errors on every evaluation — before writing policy #2.** Then define a Cedar schema so typos fail at startup.

### E2 · The ADA reading is backwards, and I cited the wrong statute for the action.

Two separate errors.

**(a) "Preexisting excepted, new covered" is inverted.** [§35.201(b)](https://www.ecfr.gov/current/title-28/chapter-I/part-35/subpart-H/section-35.201) excepts documents available *"before the date the public entity is required to comply."* The cutoff is **April 2027/2028 — not today.** An agenda posted today is *preexisting*, hence excepted. [DOJ's own guidance](https://www.ada.gov/resources/web-rule-first-steps/) confirms it, and its canonical example of an excepted document is *"PDF minutes from past city council meetings"* — literally this corpus.

**(b) Wrong rule for the action.** Subpart H covers **web content and mobile apps only**. Interpreters and CART live in [§35.160](https://www.ecfr.gov/current/title-28/chapter-I/part-35/subpart-E/section-35.160) — effective **July 26 1991**, no phase-in, no deadline. Having the agent coordinate a *provider* while citing the April 2027 web deadline cites the wrong rule. Any accessibility professional catches it instantly.

**Salvage — and it is genuinely stronger.** Derive **two** obligations per meeting:
- **§35.160 effective communication** — for interpreter/CART coordination. Active since 1991. Triggered *now*, not in 2027.
- **Subpart H conformance** — for the agenda document. April 26 2027 (50k+) / April 26 2028 (smaller).

That is more accurate and more impressive than one deadline.

✅ The Federal Register extension and both dates are correct. Note it is an **Interim Final Rule** with comments open — say "as extended," not "final."

### E3 · My "verified" curl does not reproduce.

```
GET https://webapi.legistar.com/v1/seattle/Events?$top=3
```
returns **EventIds 1326/1328/1329 — dated 2015-02-03, 2015-02-09, 2015-02-11.** `$top` without `$orderby` returns the **oldest** rows.

The 2026-09-08 `"Cancellation Notice"` row is real (EventId 6860) and every field I claimed exists. But it is not what that query returns. **Run my command on camera and 2015 meetings appear.**

Always: `$orderby=EventLastModifiedUtc desc`.

### E4 · Only 2 of 5 Legistar namespaces work.

`seattle` ✅ · `alameda` ✅ · `nyc` **403** · `chicago` **500** · `mcpb` **500**.

No published ToS, no rate limits, no robots.txt anywhere. **Absence of a ToS is a risk, not a permission** — an undocumented API can close without notice, and the NYC 403 shows access is already differentiated. Polling a handful every 15 minutes is defensible; fanning out to hundreds is what gets an IP blocked mid-judging.

### E5 · I misattributed a statistic.

The *"55% cost reduction, 68%→98% accuracy"* figure is **not** in the post I cited. It is in [a different one](https://strandsagents.com/blog/reduced-cost-better-isolation-more-resilience/), and the benchmark is **code investigation** — long-context search, not AccessFlow's short structured per-case work. Do not cite it as an AccessFlow benefit.

✅ The **100% vs 80.8% steering-versus-graph** figure is real and correctly attributed.

### E6 · I overstated the AgentCore poller cost by ~5.7×.

[AgentCore bills active CPU only](https://aws.amazon.com/bedrock/agentcore/pricing/) — *"if your agent consumes no CPU during I/O wait, there are no CPU charges."* A sleeping poller is ~$20 in memory over 44 days, not $114. **Right conclusion (poll on Lambda), wrong arithmetic.** The NAT Gateway warning at $47.52 stands and is correct.

### Settled: `EdgeConditionWithContext` **exists**, at `strands.multiagent` (`graph.py:74`) — not `strands.experimental`. Both earlier passes were half-right.

---

## PART 2 — THE COST ARCHITECTURE (your question)

### The rule that unlocks everything: **Bedrock is not mandatory.**

From the Official Rules, Project Requirements, verbatim:

> *"Deploying with Amazon Bedrock AgentCore is a smart architectural choice and will strengthen your Technical Implementation score, but it's not required."*

And it is confirmed on the record: in the forum thread *"AgentCore Runtime quota stuck at 0,"* a participant reported zero AgentCore quota across UI, CLI and API. **Shawni Devpost (Manager)** replied pointing to Section 4 and told him he can build with Strands Agents **without losing eligibility**.

**Required:** Strands Agents SDK used meaningfully · an AWS account · AWS Builder ID · public repo + MIT/Apache licence + README + architecture diagram · a ≤5-minute public video · free access through Oct 8.
**Not required:** Bedrock. AgentCore.

So your instinct is right and it is fully legal.

### The three-tier model strategy

| Tier | What | Provider | Cost |
|---|---|---|---|
| **0 — free** | Cedar policy tests, 8 tool contracts, state machine, feed client + change detection, obligation derivation, idempotency, audit trail, the React console, repo hygiene | **no model at all** | **$0** |
| **1 — dev** | Agent loop shape, prompt iteration, tool-selection debugging | **Anthropic API direct** | your own key, off the $50 |
| **2 — capped** | Final integration, demo rehearsals, the judging window | **Bedrock** | the $50 |

**Tier 0 is roughly 70% of the build hours and costs nothing.** Cedar is a policy engine — no model in the loop. Tool tests, state transitions and the console are all deterministic. That is the single biggest saving available and it needs no cleverness.

### ⚠️ Do NOT develop against Ollama

The adversary read the provider source. **`ollama.py:325` calls `warn_on_tool_choice_not_supported()` — Ollama silently ignores `tool_choice`.** Bedrock and Anthropic both honour it. If your ASK path forces `request_human_decision`, that is **enforced on Bedrock and ignored on Ollama** — opposite behaviour in the exact system you are demoing.

Structured output also differs: Ollama uses constrained decoding (essentially cannot produce invalid JSON); Bedrock implements it as a forced tool call with `ensure_strict_json_schema`. Code that never fails locally throws on Bedrock.

**Use Anthropic-direct for Tier 1.** It shares `tool_choice` and tool-call semantics with Bedrock.

### The swap pattern

```python
import os
from strands.models import BedrockModel, AnthropicModel

def get_model():
    if os.getenv("MODEL_PROVIDER", "anthropic") == "bedrock":
        return BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                            region_name="us-west-2")
    return AnthropicModel(client_args={"api_key": os.environ["ANTHROPIC_API_KEY"]},
                          model_id="claude-haiku-4-5-20251001")
```

One env var. `MODEL_PROVIDER=bedrock` for the demo, unset for development. Note the Bedrock id needs the `us.` inference-profile prefix; the Anthropic id does not.

### Does $50 hold?

**Not on the expected case.** The cost seat modelled **$76.03 expected**, $16.98 best case. The adversary independently found that a multi-step agent loop resends history each turn — ~15 turns × ~20k context ≈ $1/case at Sonnet rates, so **20–40 rehearsal runs = $200–400, four to eight times the entire budget.**

It holds only with these five caps:

1. **Rehearse on Haiku 4.5, demo on Sonnet.** ~6× cheaper per run.
2. **Cap rehearsals to 3 cases**, not 20.
3. **Enable prompt caching** — cache reads are 10% of input price.
4. **Hard per-day token cap in code**, enforced in the agent wrapper, not by discipline.
5. **CloudWatch billing alarm at $25**, set today:
```bash
aws budgets create-budget --account-id <ACCT> \
  --budget '{"BudgetName":"accessflow","BudgetLimit":{"Amount":"25","Unit":"USD"},
             "TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL",
     "ComparisonOperator":"GREATER_THAN","Threshold":80},
     "Subscribers":[{"SubscriptionType":"EMAIL","Address":"<you>"}]}]'
```

### 🔴 Smoke-test Bedrock TODAY

Multiple AWS re:Post reports of **new accounts with effectively zero Bedrock Claude quota** — [429 "Too many tokens per day"](https://repost.aws/questions/QUmfeTj3cNRJGuelOAsFiFvg/bedrock-anthropic-claude-models-return-429-too-many-tokens-per-day-quasi-zero-quota-request-aws-support-escalation) requiring **AWS Support escalation with no documented resolution time.** Discovering this on Sep 12 ends the project. One `invoke_model` call today tells you.

### Cost leaks beyond NAT Gateway

| Leak | Rate | 44 days |
|---|---|---|
| **NAT Gateway** | $0.045/hr | **$47.52** — never provision one |
| **Public IPv4 address** | $0.005/hr | **$5.28** per address |
| CloudWatch Logs ingestion | $0.50/GB | set 7-day retention |
| Secrets Manager | $0.40/secret/mo | use **SSM Parameter Store** (free) |
| ECR storage for AgentCore images | $0.10/GB/mo | prune old images |
| Anything left running after Oct 8 | — | calendar reminder to tear down |

---

## PART 3 — THE DEMO IS A 1% SHOT

The adversary pulled Seattle's 50 most recent modifications: **2026-07-06 → 2026-08-21 = 1.09 changes/day**, clustered in business hours.

**P(a genuine change lands in a 5-minute recording) ≈ 1.0%.** Reaching a coin flip needs ~70 jurisdictions; 95% needs ~300 — and only 2 of 5 tested namespaces work, so 300 working endpoints means probing 600–750 slugs, which is exactly the fan-out that gets an IP blocked.

**And it is already broken:** Seattle's newest modification is **Aug 21**. Today is **Aug 26**. Five days of silence — council summer recess. The 2026-09-08 cancellation row was last modified **Aug 18**; by demo day it is a month-old static row. Saying *"the city just cancelled this"* over it is technically true and rhetorically misleading, and a judge who reads `EventLastModifiedUtc` on camera sees the date.

**Fix — reframe from luck to capability.** Do not gamble on a live change. Show a change **already detected and handled while she slept**, with the timeline on screen: `EventLastModifiedUtc` before/after, the fingerprint delta, the re-plan, the audit trail — *"this fired at 14:32 on Sept 9 while I was asleep; here is the log."* That is stronger than a coin flip and it is honest, because the agent genuinely did it unattended. Keep a live poll running in a corner as garnish.

**This makes the poller a video dependency, not just a metrics one.** It must run continuously from today. There is no way to buy that history back later.

---

## PART 4 — THE ARITHMETIC NOBODY RAN

Five criteria, equally weighted, on a 1–5 scale. So moving one criterion by a full point = **+0.2 weighted**.

| Investment | Gain | Cost | Risk |
|---|---|---|---|
| The entire Cedar centrepiece (Creativity 2→4) | **+0.4** | ~5 days | high — rare API, no samples, one proven fail-open |
| **Three builder.aws blog posts** | **+0.6** | **~1.5 days** | **none** |

**Three blog posts outscore the whole Cedar bet by 50%, at a third of the cost and none of the risk.** Sixteen rounds of review missed this.

Keep Cedar — the panel is **all AWS**, Cedar is **AWS's own language**, and 2–3 of the five judges are agent-literate (not one, as I said). But **write the posts first**, and note the delivery seat's lever: **the bonus is assessed during judging, so posts can be published Sep 12–13 after submitting Sep 11** — reclaiming a day and a half from the pre-submission budget. Verify against the rules text before relying on it.

---

## PART 5 — THE UNEXAMINED ASSUMPTION

**Is the LLM load-bearing at all?**

Strip AccessFlow down: poll feed → diff a timestamp → derive an obligation (a two-row lookup) → coordinate a *simulated* provider → check a boolean → close. And Cedar deliberately removes the model's authority over the only irreversible action.

So what does the model actually *decide*? Nobody tested it. This hits **two** equally-weighted criteria — Technical Implementation and Creativity — and a judge feels it without being able to name it.

**One-hour test, run it before Aug 28:** pull 20 real events. Write a 30-line deterministic rule engine (population → deadline; comment contains "Cancellation" → re-plan; fingerprint changed → re-verify). Run both. **If ≥18/20 outputs match, the LLM is decoration.**

If it is, the fix is to move the agent onto the genuinely ambiguous work: parsing free-text `EventComment` into intent, and **reading the agenda PDF to infer which accommodations a specific agenda item requires** — a public hearing on a housing ordinance in a district with high LEP population is a different accommodation profile from a procedural consent calendar. That is irreducible, and it is a better product.

---

## PART 6 — DELIVERY

**170h midpoint scope against ~85h capacity to Sep 11.** Not slippage — a 2× arithmetic failure.

**Verdict: ships with cuts. Does not ship as specified.**

Cut list, in order, with score impact:

| # | Cut | Hours | Impact |
|---|---|---:|---|
| 1 | 15 tools → **8** | 6 | **zero** |
| 2 | 6 providers → **3** | 2 | **zero** |
| 3 | Cedar 162 lines → **60–80**, 6 tested forbids | 8 | **zero** — demos identically |
| 4 | Strands Evals + chaos → one README paragraph | 6 | −0.1 |
| 5 | SDK mid-tool resume → **case-record durability** | 8 | −0.05, arguably **+** |
| 6 | `LLMSteeringHandler` | 4 | −0.1 |
| 7 | 5 screens → **2** (dashboard + decision queue) | 8 | −0.05 |
| 8 | Verification *agent* → verification *tool* (keep the Cedar invariant) | 3 | −0.05 |

**Protect, in this order:** the three blog posts · the video · Cedar · the two console screens · the real feed.

### On durable resume — reframe, do not fight

[harness-sdk #859](https://github.com/strands-agents/harness-sdk/issues/859) is *precisely* the planned demo beat: *"Session management fails to resume when previous session ended during tool execution"* — the session persists a `tool_use` with no matching `tool_result`, and Bedrock throws `ValidationException` on resume. My §17.1 beat was `kill -9` **mid-batch**, which is definitionally that failure.

**Kill the process at a state-machine boundary you persist yourself**, not mid-tool. The claim becomes *"the case survives process death, and every tool is idempotent, so re-execution is safe."* True, better, and 2 hours instead of 10.

### Deploy moves from Sep 7 to **Sep 4**

The AgentCore first-deploy failure surface is wide and every error is uninformative: **ARM64-only** (`exec format error`), the container must expose **port 8080 + `/invocations`** (miss it → a silent 504), **MMDSv2 required after June 30 2026**, [`.env.local` works for `agentcore dev` but not `agentcore deploy`](https://github.com/aws/agentcore-cli/issues/1378), and reused session ids **pin old code** so a fixed bug looks unfixed. Those need three days of slack behind them, not zero.

### The public demo proxy

`InvokeAgentRuntime` requires SigV4/OAuth. **Minimum viable: a Lambda Function URL with `AuthType: NONE`** calling `boto3.client('bedrock-agentcore').invoke_agent_runtime(...)`. ~40 lines, 2–4 hours, non-streaming, CORS open.

**Do not use [sample-expose-agentcore-via-api-gateway](https://github.com/aws-samples/sample-expose-agentcore-via-api-gateway)** — it drags in a private VPC with a bedrock-agentcore VPC endpoint, i.e. the **NAT Gateway that costs 95% of your budget.** And cut streaming; that is where the day disappears.

---

## PART 7 — TOP FIVE FAILURE MODES

| # | Failure | P | Damage | Mitigation |
|---|---|---|---|---|
| 1 | Rehearsal cost exhausts $50 | high | fatal | Haiku for rehearsals, 3 cases max, prompt caching, $25 alarm |
| 2 | No live change in the recording window | **~99%** | severe | Do not gamble — show the logged unattended detection |
| 3 | Zero Bedrock quota found at the last mile | medium | fatal | **One `invoke_model` today.** Support escalation has no documented SLA |
| 4 | A judge checks the ADA citation | medium | severe | Split §35.160 / Subpart H as above |
| 5 | Cedar fail-open ships | **certain if unpatched** | severe | **Already patched.** Add the empty-enricher test |

**Runner-up:** Legistar 403s the polling IP during the 24-day unattended judging window. **Cache every response to S3 and serve the demo from cache** — a block then degrades the live feed but never the judged artifact.

---

## THE UNRESOLVED DECISION

`hackathon-decision.md` still names **JORNALERO** as "THE IDEA" (5/5/5/4/5). The AccessFlow docs are three hours newer. From a delivery position there is capacity for **one** concept, decided **now**.

And AccessFlow still hard-fails constraint 5 (portfolio diversity — the ground that killed Braille Tonight and Persona Lab). The v2 patch prices re-domaining at ~2 days. **Those two days exist on Aug 26. They do not exist on Sep 2.**

Decide today: **AccessFlow as-is · AccessFlow re-domained · JORNALERO.** Not on Sep 2.
