# AccessFlow — build runbook

_Written 2026-08-25. Deadline Sep 14, 5:00pm PDT — **20 days**. Scope freeze Aug 27._

Everything below is ordered by what blocks what. Do Block 0 tonight; it contains every item with external latency you cannot compress later.

---

## BLOCK 0 — tonight, ~90 minutes

These five things gate the entire build. Nothing else matters until they are green.

### 0.1 — Pick your region and stay in it

Use **us-west-2 (Oregon)** or **us-east-1 (N. Virginia)**. AgentCore's July 2026 quota increase gives these two **5,000 concurrent runtime sessions** against **2,500** elsewhere ([AWS What's New, Jul 2026](https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-bedrock-agentcore-increases-default-runtime-quota-limits/)). Set it once and never think about it again:

```bash
aws configure set region us-west-2
aws sts get-caller-identity          # must return your account id
```

### 0.2 — Prove you can actually invoke Claude on Bedrock

This is the #1 blocker on the hackathon's own Devpost forum ("Bedrock model access blocked account-wide"). Two facts, both verified in the current AWS docs:

- *"Access to all Amazon Bedrock foundation models is enabled by default with the correct AWS Marketplace permissions."* Models auto-subscribe on first invoke; allow **up to 15 minutes** for the subscription to finalise.
- *"For Anthropic models, you must complete the First Time Use (FTU) form before invoking the model."* Once per account. You already submitted this form in an earlier session — this step is to confirm it took.

Your IAM user/role needs `aws-marketplace:Subscribe`, `aws-marketplace:Unsubscribe`, `aws-marketplace:ViewSubscriptions`, plus `bedrock:InvokeModel` and `bedrock:Converse`.

```bash
aws bedrock list-foundation-models --region us-west-2 \
  --query "modelSummaries[?contains(modelId,'anthropic')].modelId" --output table

aws bedrock-runtime converse \
  --region us-west-2 \
  --model-id anthropic.claude-haiku-4-5-20251001-v1:0 \
  --messages '[{"role":"user","content":[{"text":"reply with the word ready"}]}]'
```

If the second command returns text, you are unblocked. If it returns `AccessDeniedException`, open the Bedrock console → **Model catalog** → pick the Claude model → **Open in Playground**; that is what triggers the FTU form and the subscription.

**Model choice:** use **Claude Haiku 4.5** as the default (`$1 / $5` per million tokens) and reserve Sonnet for the verification step only. On a $50 budget that difference is the build.

### 0.3 — Install the AgentCore CLI (npm, not pip)

Verified in the current AWS docs. Prerequisites: **Node 20+**, **Python 3.10+**.

```bash
node --version && python3 --version
npm install -g @aws/agentcore
agentcore --version
```

⚠️ If you ever installed `bedrock-agentcore-starter-toolkit` via pip, **uninstall it first** — both packages claim the `agentcore` command and the pip one is the legacy path.

Smoke-test the whole chain now, on a throwaway agent, so you find IAM problems tonight rather than on Sep 7:

```bash
agentcore create        # scaffolds a project
cd MyAgent
agentcore dev           # runs locally
agentcore deploy        # needs CDK bootstrap role permissions
agentcore invoke --prompt "Hello, what can you do?"
```

`agentcore deploy` is where IAM failures surface. Fix them tonight.

### 0.4 — Confirm the feed still answers

```bash
curl -s "https://webapi.legistar.com/v1/seattle/Events?\$top=3&\$orderby=EventDate+desc" | head -c 600
```

Verified 200 + JSON with **no API key** on 2026-08-25. Then confirm the other namespaces you intend to watch — `seattle` is proven; `mountainview` and `oakland` in `WATCHED_CLIENTS` are unverified placeholders:

```bash
for c in seattle mountainview oakland sfgov nyc; do
  printf "%s " "$c"
  curl -s -o /dev/null -w "%{http_code}\n" "https://webapi.legistar.com/v1/$c/Bodies?\$top=1"
done
```

Keep the ones returning 200. You want 3–5.

### 0.5 — Start the measurement clock

You cannot pitch a rate you have not measured — that rule has killed a dozen concepts in this project. Start the poller tonight so you have 48 hours of real numbers by scope freeze:

```bash
pip install httpx 'strands-agents==1.53.0' 'strands-agents-tools==0.8.6' 'strands-agents[cedar]'
python -c "
from backend.app.tools.legistar import LegistarFeed
import json, time, datetime
f = LegistarFeed()
while True:
    new, changes = f.poll()
    print(json.dumps({'t': datetime.datetime.utcnow().isoformat(),
                      'new': len(new), 'changes': len(changes),
                      'kinds': [c.change_type for c in changes]}), flush=True)
    time.sleep(900)
" | tee -a feed-measure.jsonl
```

By Aug 27 you need three numbers for the video and the README: **new meetings/day**, **agenda documents posted/day**, **real changes detected/day**.

### Block 0 checklist

- [ ] Region pinned to us-west-2 or us-east-1
- [ ] `bedrock-runtime converse` returns text from Claude Haiku 4.5
- [ ] `agentcore deploy` succeeded once on a throwaway agent
- [ ] 3–5 Legistar namespaces returning 200
- [ ] Poller running and writing `feed-measure.jsonl`
- [ ] **AWS Builder ID created** — it is a pass/fail submission gate, takes 2 minutes
- [ ] $50 credits visible in Billing → Credits (already applied in an earlier session — just confirm)

---

## AWS SERVICES — what you actually need, and what to avoid

| Service | Role | Priority | Cost note |
|---|---|---|---|
| **Amazon Bedrock** | Claude Haiku 4.5 inference | **Mandatory** | ~$0.003/case at 3k in / 300 out |
| **Strands Agents SDK** | Agent orchestration | **Mandatory** (contest rule) | free |
| **AgentCore Runtime** | Hosts the agent; invoked per case | **High** | $0.0895/vCPU-hr + $0.00945/GB-hr, **billed on active consumption only — idle is free** |
| **Lambda + EventBridge** | The 15-minute feed poller | **High** | free tier: 1M requests + 400k GB-s/month, permanent |
| **S3** | `S3SessionManager` durable sessions + agenda documents | **High** | pennies |
| **DynamoDB** (on-demand) | Case / action / decision records | **High** | 25 GB always-free |
| **CloudWatch / AgentCore Observability** | OTEL traces — 10 seconds of trace waterfall in the video | **Medium-High** | low |
| **Secrets Manager** or SSM Parameter Store | No keys in the repo (organizers warned explicitly) | **High** | ~$0.40/secret/mo — Parameter Store is free |
| **IAM** | Least-privilege execution role | **High** | free |

### Three cost traps — each one can end the project

1. **Never provision a NAT Gateway.** $0.045/hour × 1,056 hours (Aug 25 → Oct 8) = **$47.52**, 95% of your budget, before a single GB of traffic. Run Lambda outside a VPC.
2. **Never run the poller inside AgentCore Runtime.** Continuous CPU there is ~$114 over 44 days. Poll on Lambda; invoke AgentCore per case.
3. **There is no AgentCore free tier.** Only the general AWS Free Tier applies.

Done right, the whole 44-day run — build plus the mandatory public window through Oct 8 — lands around **$18–27**.

### Secrets

```bash
aws ssm put-parameter --name /accessflow/bedrock_region --value us-west-2 --type String
```

Commit `.env.example` with placeholders only. Add a README section on secret handling — the organizers called this out by name. Run `git secrets --scan` or `trufflehog` before you make the repo public.

---

## THE SCHEDULE

| Dates | Phase | Exit criterion |
|---|---|---|
| **Aug 25 (tonight)** | Block 0 | Every checkbox above is ticked |
| Aug 26 | Domain + tools against the real feed | `poll_public_meetings` and `fetch_agenda_document` return real data; case records persist |
| **Aug 27** | **SCOPE FREEZE** | One real meeting goes NEW → CLOSED end to end with real tool calls. Feed numbers measured. |
| Aug 28–29 | Cedar authority layer | Every `forbid` in `accessflow.cedar` has a passing test. `close_case` without `verification_id` is denied **by Cedar**, not by app code. |
| Aug 30–31 | Orchestrator + agents-as-tools | Requirement / Provider / Verification exposed as tools on one orchestrator |
| Sep 1–2 | Background loop + real change detection | A real cancellation or reschedule in the feed triggers re-planning with no human input |
| Sep 3–4 | ASK flow + durable interrupts | `S3SessionManager` resumes a case after `kill -9` |
| Sep 5–7 | Operations console | Dashboard, case detail, decision queue, activity timeline |
| **Sep 7** | **CODE FREEZE** | AgentCore deployed, live URL up, README + architecture diagram + MIT licence in the repo `About` |
| Sep 8–9 | Video + Devpost page + 3 builder.aws posts | Video ≤5 min, public, "Not for Kids" |
| **Sep 11** | **SUBMIT** | Three days of buffer before the hard deadline |
| Sep 14, 5pm PDT | Hard deadline | — |
| Sep 15 – Oct 8 | Judging | Demo stays live and free; check email daily (2-day reply window) |

**The biggest schedule risk is the frontend.** Five screens in three days, solo, is the thing most likely to slip. If Sep 5 arrives and the agent loop is not solid, cut to **two screens** — the operations dashboard and the decision queue — and drop event detail, case detail and agent activity into a single expandable panel. A judge needs to see the portfolio and the decision. Everything else is polish.

**Do not skip the blog posts.** +0.6 on a 1–5 scale for ~6 hours of writing is the highest points-per-hour item in the contest, and it is near-certainly unclaimed. Titles must contain "Agents for Humans". Suggested, each doubling as a technical flex:

1. *Agents for Humans: gating my agent's tools with Cedar policies*
2. *Agents for Humans: the agent that cannot close a case it did not verify*
3. *Agents for Humans: what happens when the coordinator doesn't reply for three days*

---

## WHAT TO HAND THE IMPLEMENTATION AGENT

Give it, in this order:

1. `AccessFlow_Implementation_Master_Prompt.docx` — the original spec
2. `docs/SPEC-V2-PATCH.md` — **apply this first**; it supersedes §0, §3, §5.2, §6.1, §7.5, §9, §10, §14, §15, §17.1, §18.2 and §19.1
3. `policies/accessflow.cedar`, `backend/app/agents/authority.py`, `backend/app/tools/legistar.py` — do not rewrite these; they are verified against live APIs
4. This runbook

Opening instruction:

> Apply `docs/SPEC-V2-PATCH.md` to the master prompt before implementing anything. The three files under `policies/` and `backend/app/` are verified against live APIs as of 2026-08-25 — treat them as fixed contracts and build around them. Do not reintroduce the simulated inbound edge or the "Simulate event change" control. Build in the phase order in `docs/BUILD-RUNBOOK.md`, and stop at each exit criterion until it is met.

### Pin these versions

```
strands-agents==1.53.0
strands-agents-tools==0.8.6
strands-agents[cedar]
```

⚠️ The SDK now lives in the **`strands-agents/harness-sdk`** monorepo — `sdk-typescript`, `docs` and `agent-builder` are archived. Cite the monorepo in your README, not `sdk-python`.

⚠️ Two research passes disagreed on whether `EdgeConditionWithContext` exists. Do not build against it. Moot here — the patch drops graphs in favour of one orchestrator with agents-as-tools.

---

## The pass/fail gates — miss one and the score is zero

- [ ] New project, built Aug 10 – Sep 14; disclose any pre-existing code
- [ ] Strands Agents SDK used meaningfully
- [ ] **Public** repo with MIT or Apache licence **visible in the GitHub About section**
- [ ] README + architecture diagram
- [ ] Video ≤5 minutes, public on YouTube/Vimeo, covering problem / who / why
- [ ] AWS Builder ID on the submission
- [ ] Project free to access, with no restrictions, through **Oct 8**
- [ ] No secrets anywhere in the repo, fixtures, logs, screenshots or the video
