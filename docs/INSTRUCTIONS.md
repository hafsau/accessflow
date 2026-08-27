# AccessFlow — build instructions

_Aug 26 2026. Deadline Sep 14, 5:00pm PDT. **19 days.** Submit Sep 11._

Work top to bottom. Every step has a command and a way to know it worked. Where a step says **STOP**, do not continue past it until it passes.

---

# BEFORE DAY 0 — set up your machine. ~15 min, once.

**Where do these commands go?** On **your own Mac, in Terminal** — or inside **Claude Code**, which runs bash on your machine and can read errors back to you. Not in the Claude chat window; that runs in a cloud sandbox with no access to your AWS account.

Open Terminal: `Cmd + Space` → type `Terminal` → Enter.

## P.1 Homebrew (skip if `brew --version` works)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

## P.2 The four tools

```bash
brew install awscli node python@3.11 git
aws --version      # need 2.x
node --version     # need 20+
python3 --version  # need 3.10+
```
If `node` is older than 20: `brew install node@20 && brew link --overwrite node@20`

## P.3 AWS credentials

You need an access key. In the AWS console:

1. Search **IAM** → **Users** → **Create user** → name it `accessflow-dev`
2. **Attach policies directly** → for a solo hackathon account, `AdministratorAccess` is the pragmatic choice. (Least privilege is correct in general; scoping IAM policies is not where your 19 days should go.)
3. Create the user → click into it → **Security credentials** tab → **Create access key** → choose **Command Line Interface (CLI)** → acknowledge → **Create**
4. Copy both values. The secret is shown **once**.

Then:

```bash
aws configure
# AWS Access Key ID:     paste it
# AWS Secret Access Key: paste it
# Default region name:   us-west-2
# Default output format: json
```

Verify:
```bash
aws sts get-caller-identity
```
✅ Returns `UserId`, `Account`, `Arn`. If it errors, the key is wrong — redo step 3.

🔴 **The key now lives in `~/.aws/credentials` in plain text.** Never paste it into a file inside your repo, never into a `.env` that gets committed, never into a screenshot or the demo video. The organizers called this out by name: *"A public repo with an exposed key is an open invitation to run up charges on your account."*

Add this to `.gitignore` on day one:
```
.env
.env.local
*.pem
.aws/
.budget.json
feed-measure.jsonl
```

## P.4 Your project folder

```bash
mkdir -p ~/Projects/accessflow && cd ~/Projects/accessflow
git init
```
Unzip the patch bundle here — `policies/`, `backend/`, `docs/` should sit at the top level.

Then open Claude Code in this folder and run everything below from there.

---

# DAY 0 — TODAY. ~2 hours. Do not skip any of it.

Four of these five have external latency you cannot buy back later. One of them (the poller) is a dependency for your demo video.

## 0.1 Region + identity — 5 min

```bash
aws configure set region us-west-2
aws sts get-caller-identity
```
✅ Returns your account id. Use **us-west-2** or **us-east-1** only — AgentCore's July 2026 quota bump gives those two 5,000 concurrent sessions against 2,500 elsewhere.

Create your **AWS Builder ID** now at https://profile.aws.amazon.com/ — 2 minutes, and it is a pass/fail submission gate.

## 0.2 🔴 Bedrock smoke test — 10 min. THE ONE THAT CAN KILL THE PROJECT.

Multiple AWS re:Post reports of new accounts with **zero** Claude quota, needing a Support ticket with no published resolution time. Find out today, not on Sep 12.

```bash
aws bedrock list-foundation-models --region us-west-2 \
  --query "modelSummaries[?contains(modelId,'claude')].modelId" --output table

aws bedrock-runtime converse --region us-west-2 \
  --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --messages '[{"role":"user","content":[{"text":"say ready"}]}]'
```

✅ Second command returns text.
❌ `AccessDeniedException` → console → Bedrock → **Model catalog** → Claude Haiku 4.5 → **Open in Playground**. That triggers the Anthropic First-Time-Use form and the Marketplace subscription. Wait 15 min, retry.
❌ `ThrottlingException` / 429 "Too many tokens per day" → **open an AWS Support case immediately.** This is the zero-quota trap. Do it before you do anything else today.

Your IAM principal needs: `bedrock:InvokeModel`, `bedrock:Converse`, `bedrock:ListFoundationModels`, `aws-marketplace:Subscribe`, `aws-marketplace:ViewSubscriptions`.

**STOP.** If this does not pass today, nothing else matters.

## 0.3 Spend guard — 10 min

```bash
ACCT=$(aws sts get-caller-identity --query Account --output text)
cat > /tmp/budget.json <<EOF
{"BudgetName":"accessflow","BudgetLimit":{"Amount":"25","Unit":"USD"},
 "TimeUnit":"MONTHLY","BudgetType":"COST"}
EOF
cat > /tmp/notify.json <<EOF
[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN",
  "Threshold":80,"ThresholdType":"PERCENTAGE"},
  "Subscribers":[{"SubscriptionType":"EMAIL","Address":"YOUR@EMAIL"}]}]
EOF
aws budgets create-budget --account-id $ACCT \
  --budget file:///tmp/budget.json \
  --notifications-with-subscribers file:///tmp/notify.json
```
✅ Alarm at $20 of $25. You have $50 total; this leaves headroom to react.

## 0.4 AgentCore CLI + a throwaway deploy — 45 min

This is where IAM and container problems surface. They must surface tonight, not on Sep 4.

```bash
node --version    # need 20+
python3 --version # need 3.10+
npm install -g @aws/agentcore
agentcore --version

cd /tmp && agentcore create   # name it "throwaway"
cd throwaway
agentcore dev                 # runs locally — Ctrl-C when it responds
agentcore deploy              # THIS is the real test
agentcore invoke --prompt "say ready"
```

If `deploy` fails, the error will be uninformative. The five known causes, in order of likelihood:

| Symptom | Cause | Fix |
|---|---|---|
| `exec /bin/sh: exec format error` or *"binary files incompatible with Linux ARM64"* | x86 image | AgentCore is **ARM64 only**. `uv pip install --python-platform aarch64-manylinux2014 --only-binary=:all:` |
| **504 Gateway Timeout**, no other detail | container contract | Must expose **port 8080** and a **`/invocations`** endpoint |
| `403 RuntimeClientError` | container crash or execution role | CloudWatch → `/aws/bedrock-agentcore/runtimes/<agent_id>-<endpoint>/runtime-logs` |
| `ValidationException: This runtime is not MMDSv2-enabled` | pre-June-2026 tutorial | Enable MMDSv2. Any older guide walks you into this |
| Env vars missing in deployed runtime but fine locally | [agentcore-cli #1378](https://github.com/aws/agentcore-cli/issues/1378) | `.env.local` works for `dev`, **not** `deploy`. Pass config explicitly |

Also: **reused session ids pin old code.** You fix a bug, redeploy, and it looks unfixed. Use a fresh `--session-id` after every deploy or you will lose two hours to it.

```bash
cd /tmp && rm -rf throwaway   # tear it down after it works
```

**STOP.** If `agentcore deploy` does not succeed today, spend tomorrow on it and nothing else.

## 0.5 🔴 Start the poller — 20 min. THIS IS A VIDEO DEPENDENCY.

Seattle averages **1.09 feed changes/day**. The chance one lands inside a 5-minute recording is **~1%**. You are not going to catch one live — you are going to catch one over two weeks and *show the log*. That history only exists if the poller starts now.

First verify which namespaces work — only 2 of 5 tested do:

```bash
for c in seattle alameda oakland sanjose longbeach mountainview sfgov berkeley; do
  printf "%-14s " $c
  curl -s -o /dev/null -w "%{http_code}\n" "https://webapi.legistar.com/v1/$c/Bodies?\$top=1"
done
```
Keep the 200s. Aim for 4–6. **Do not fan out to hundreds** — no published rate limit exists, `nyc` already 403s, and a block during the Sep 15–Oct 8 judging window would take your demo down.

⚠️ **Always `$orderby`.** `$top` alone returns the OLDEST rows — Seattle gives you 2015 meetings:
```bash
curl -s "https://webapi.legistar.com/v1/seattle/Events?\$top=3&\$orderby=EventLastModifiedUtc+desc"
```

Then:
```bash
mkdir -p ~/accessflow && cd ~/accessflow
python3 -m venv .venv && source .venv/bin/activate
pip install httpx 'strands-agents==1.53.0' 'strands-agents-tools==0.8.6' 'strands-agents[cedar]' anthropic
# drop backend/app/tools/legistar.py in, set WATCHED_CLIENTS to your verified list
nohup python -c "
from backend.app.tools.legistar import LegistarFeed
import json, time, datetime
f = LegistarFeed()
while True:
    new, ch = f.poll()
    print(json.dumps({'t': datetime.datetime.utcnow().isoformat(),
                      'new': len(new), 'changes': len(ch),
                      'detail': [{'k': c.meeting.key, 'type': c.change_type,
                                  'body': c.meeting.body_name} for c in ch]}), flush=True)
    time.sleep(900)
" >> feed-measure.jsonl 2>&1 &
echo $! > poller.pid
```
✅ `tail -f feed-measure.jsonl` shows a line every 15 minutes. **Leave it running for 16 days.** Back it up daily — this file is your demo.

## Day 0 done when

- [ ] `bedrock-runtime converse` returns text
- [ ] `$25` budget alarm created
- [ ] `agentcore deploy` + `invoke` both succeeded once
- [ ] 4–6 Legistar namespaces returning 200
- [ ] Poller running, writing to `feed-measure.jsonl`
- [ ] AWS Builder ID created

---

# THE MODEL STRATEGY — how the $50 survives

**Bedrock is not required.** Official Rules, verbatim: *"Deploying with Amazon Bedrock AgentCore is a smart architectural choice and will strengthen your Technical Implementation score, but it's not required."* An organizer confirmed the same in the forum to a participant with zero AgentCore quota.

| Tier | Work | Provider | Cost |
|---|---|---|---|
| **0** | Cedar policy + tests, 8 tool contracts, state machine, feed client, obligation derivation, idempotency, audit trail, console, repo hygiene | **no model at all** | **$0** |
| **1** | Agent loop shape, prompts, tool-selection debugging | **Anthropic API direct** | your key |
| **2** | Final integration, demo rehearsals, judging window | **Bedrock Haiku 4.5** | the $50 |

**Tier 0 is ~70% of the build.** Cedar is a policy engine — no model in the loop. That is the saving; it needs no cleverness.

**Do NOT use Ollama.** `ollama.py:325` calls `warn_on_tool_choice_not_supported()` — it **silently ignores `tool_choice`**, which Bedrock enforces. Your ASK path forces `request_human_decision`; that would work on Bedrock and be ignored locally. Anthropic-direct shares Bedrock's tool-call semantics.

`backend/app/agents/model.py`:
```python
import os
from strands.models import BedrockModel
from strands.models.anthropic import AnthropicModel

def get_model():
    if os.getenv("MODEL_PROVIDER", "anthropic") == "bedrock":
        return BedrockModel(
            model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",  # us. prefix required
            region_name=os.getenv("AWS_REGION", "us-west-2"),
            cache_prompt="default",       # cache reads are 10% of input price
        )
    return AnthropicModel(
        client_args={"api_key": os.environ["ANTHROPIC_API_KEY"]},
        model_id="claude-haiku-4-5-20251001",                        # no prefix
    )
```
`MODEL_PROVIDER=bedrock` only when you mean it.

**Hard per-day cap — in code, not discipline.** A 15-turn loop resends history each turn: ~$1/case on Sonnet. Twenty rehearsals = $200+.
```python
# backend/app/agents/budget.py
import json, datetime, pathlib
LEDGER, DAILY_USD = pathlib.Path(".budget.json"), 1.50
def check_and_charge(est_usd: float):
    today = datetime.date.today().isoformat()
    d = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    if d.get("date") != today: d = {"date": today, "spent": 0.0}
    if d["spent"] + est_usd > DAILY_USD:
        raise RuntimeError(f"daily cap hit: ${d['spent']:.2f}/{DAILY_USD}")
    d["spent"] += est_usd; LEDGER.write_text(json.dumps(d))
```
Call it before every Bedrock invocation. **Rehearse on Haiku, cap to 3 cases, never 20.**

**Cost traps:** never a **NAT Gateway** ($0.045/hr = **$47.52** over 44 days — 95% of budget). Never run the poller *inside* AgentCore Runtime. Public IPv4 is $0.005/hr = $5.28. Use **SSM Parameter Store** (free), not Secrets Manager ($0.40/secret/mo). Set CloudWatch log retention to 7 days.

---

# DAY 1 — Aug 27. The one-hour test, then scope freeze.

## 1.1 🔴 Is the LLM actually load-bearing? — 1 hour. Run this FIRST.

Nobody has tested whether the agent is doing work a rule engine couldn't. This hits two equally-weighted criteria (Technical Implementation and Creativity) and a judge feels it without being able to name it.

Pull 20 real events from your feed. Write 30 lines of deterministic rules — population ≥50k → April 2027 deadline; `EventComment` contains "Cancellation" → re-plan; fingerprint changed → re-verify. Run both. Compare.

**If ≥18/20 outputs match, the LLM is decoration.** The fix is to move the agent onto work that genuinely is ambiguous:
- parsing free-text `EventComment` into intent (agencies write these inconsistently across every jurisdiction)
- **reading the agenda PDF to infer which accommodations that specific agenda item needs** — a public hearing on a housing ordinance in a high-LEP district is a different profile from a procedural consent calendar

That second one is irreducible and it is a better product. Decide today which one the agent owns.

## 1.2 Fix the legal foundation

My earlier version was wrong and a judge who checks will catch it. **§35.201(b) excepts documents available before the compliance date** — so an agenda posted today is *excepted*, and DOJ's own example of an excepted document is *"PDF minutes from past city council meetings."* And **Subpart H covers web content only** — interpreters live in §35.160, effective **1991**, no deadline.

Derive **two** obligations per meeting:

```python
# §35.160 — effective communication. Active since July 26 1991. Triggered NOW.
{"basis": "28 CFR 35.160", "category": "effective_communication",
 "description": "Public entity must furnish appropriate auxiliary aids on request "
                "for this meeting. In effect since 1991; no phase-in.",
 "deadline": start - timedelta(hours=48)}   # the body's own request window

# Subpart H — web content conformance. Dated.
{"basis": "28 CFR 35.200", "category": "document_conformance",
 "description": "Agenda document must meet WCAG 2.1 AA by the entity's compliance date.",
 "deadline": "2027-04-26" if pop_over_50k else "2028-04-26"}
```
Say "**as extended**" — FR 2026-07663 is an Interim Final Rule with comments still open.

## 1.3 SCOPE FREEZE — write it down and stop renegotiating

The full spec is ~170 hours. You have ~85. Write `docs/CUTS.md` today with these already cut:

| Cut | Hours saved | Score impact |
|---|---|---|
| 15 tools → **8** | 6 | zero |
| 6 providers → **3** | 2 | zero |
| Cedar 162 lines → **60–80** | 8 | zero — demos identically |
| Strands Evals + chaos → one README paragraph | 6 | −0.1 |
| SDK mid-tool resume → **case-record durability** | 8 | −0.05, arguably + |
| `LLMSteeringHandler` | 4 | −0.1 |
| 5 console screens → **2** | 8 | −0.05 |
| Verification *agent* → verification *tool* | 3 | −0.05 |

**Protect, in order: the three blog posts · the video · Cedar · the two console screens · the real feed.**

**Exit:** `poll_public_meetings` and `fetch_agenda_document` return real data; Event/Case/Obligation persist; obligations return both bases with correct dates.

---

# DAY 2 — Aug 28. Tool contracts. **API FREEZE.**

Eight tools, typed, structured JSON, idempotency key on every mutation, one audit event per call, one unit test each. **Zero model calls — this is Tier 0.**

`get_case` · `get_event` · `poll_public_meetings` · `fetch_agenda_document` · `search_providers` · `send_provider_request` · `request_human_decision` · `verify_fulfillment` · `close_case`

**Freeze the JSON shapes today.** That single act is what lets you run the console build and the agent build in parallel worktrees from here.

**Paste into Claude Code:**
> Implement the 8 tool contracts in `docs/tool-contracts.md` as Strands `@tool` functions in `backend/app/tools/`. Every mutating tool takes an `idempotency_key` and writes an `AgentAction` audit row. Return typed Pydantic models, never prose. Validate every id and reject invalid state transitions with a structured error code. Write a pytest per tool covering: happy path, invalid id, duplicate idempotency key, and malformed input. Do not call any model. Do not touch `policies/` or `legistar.py`.

**Exit:** `pytest backend/tests/tools/` green. JSON shapes frozen in `docs/tool-contracts.md`.

---

# DAY 3 — Aug 29. Case state machine.

Ten states, explicit transitions, persisted to DynamoDB (or SQLite locally — decide once). Still zero model calls.

**Exit:** one real meeting from your feed walks NEW → CLOSED in a plain script, printing a full audit trail. No agent involved yet.

---

# DAY 4 — Aug 30. Cedar, day one. **Diagnostics before policies.**

⚠️ **Cedar silently skips any policy that errors during evaluation** — it *"treat[s] the situation as if the policy never existed."* Combined with default-deny, a typo in a `permit` is byte-identical to "no policy matched." In a 160-line file that is a black box.

**Build the harness before you write policy #2:**

```python
# backend/app/agents/cedar_debug.py — log EVERY evaluation
import logging
log = logging.getLogger("cedar")

def trace(response, tool_name, tool_input):
    log.info("cedar decision=%s tool=%s determining=%s errors=%s",
             response.decision, tool_name,
             getattr(response.diagnostics, "reason", None),
             getattr(response.diagnostics, "errors", None))
```

Then a Cedar **schema** so typos fail at startup, not as a runtime denial.

**The test that must exist first** — this is the one that caught my own fail-open:

```python
def test_close_case_denied_with_empty_enricher():
    """context.input is MODEL-GENERATED. The model can invent verification_id.
       Only context.session is trustworthy. A missing attribute ERRORS, and
       Cedar SKIPS erroring policies — so every deref must be `has`-guarded."""
    cedar = CedarAuthorization(
        policies="policies/accessflow.cedar",
        principal_resolver=lambda s: {"type": "Coordinator", "id": "c1"},
        context_enricher=lambda ctx: {},          # the failure case
        on_error="deny")
    assert denied(cedar, "close_case", {"verification_id": "totally_made_up"})
```

The shipped policy is already patched — **0 unguarded derefs across all 22 policies**. Keep it that way. Every `context.x.y` sits behind `context has x && context.x has y` in the same clause.

Three more traps from the one public Cedar+Strands writeup: `principal_resolver` returning `None` denies everything · `call_count` is 1-based, includes the current call, and **persists across sessions with no time reset** · policies only refresh on explicit `reload()`.

**Exit:** diagnostics harness logging on every call; Cedar schema in place; 3 policies passing with tests.

---

# DAY 5 — Aug 31. Cedar, day two. **Hard stop at 6 hours.**

**Exit:** `close_case` without `verification_id` denied **by Cedar** (not app code), proven by test · the empty-enricher test passes · `cedar.reload()` works live · file ≤80 lines. If incomplete at 6h, ship 4 policies and move on.

---

# DAY 6 — Sep 1. Orchestrator.

**One** agent. Requirement / Provider / Verification as **agents-as-tools**, not a graph. Cite AWS's own measurement in your README: [steering hooks 100% vs graph workflows 80.8%](https://strandsagents.com/blog/what-we-learned-from-one-year-of-building-production-agents/). Set `context_manager="auto"`.

**Exit:** one real case end to end against **Bedrock Haiku 4.5** with `MODEL_PROVIDER=bedrock`. Note the cost. That number × your rehearsal count is your real budget.

---

# DAY 7 — Sep 2. Background loop.

EventBridge → Lambda every 15 min → diff on `content_fingerprint` + `EventLastModifiedUtc` → invoke AgentCore **only** on a real change. **Lambda outside a VPC.**

**Exit:** a no-op poll produces **zero** agent invocations. That is what makes this an agent and not a cron job, and it is the thing to say on camera.

---

# DAY 8 — Sep 3. ASK flow + durability.

⚠️ **Do not attempt SDK mid-tool resume.** [harness-sdk #859](https://github.com/strands-agents/harness-sdk/issues/859) is exactly that: *"Session management fails to resume when previous session ended during tool execution"* — the session persists a `tool_use` with no `tool_result` and Bedrock throws `ValidationException`.

Kill the process at a **state-machine boundary you persist yourself**. The claim becomes: *"the case survives process death, and every tool is idempotent, so re-execution is safe."* True, better story, 2 hours instead of 10.

**Exit:** an ASK creates a durable decision record; a human answer resumes the case; `kill -9` between states loses nothing.

---

# DAY 9 — Sep 4. **DEPLOY.** (Moved up from Sep 7 deliberately.)

Public demo proxy — `InvokeAgentRuntime` needs SigV4, so you need ~40 lines:

```python
# lambda_proxy.py — Lambda Function URL, AuthType: NONE, CORS open
import boto3, json, os
rt = boto3.client("bedrock-agentcore")
def handler(event, _ctx):
    body = json.loads(event.get("body") or "{}")
    r = rt.invoke_agent_runtime(
        agentRuntimeArn=os.environ["AGENT_ARN"],
        runtimeSessionId=body.get("session_id", "demo"),
        payload=json.dumps({"prompt": body.get("prompt", "")}).encode())
    return {"statusCode": 200,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": r["response"].read().decode()}
```
Execution role needs `bedrock-agentcore:InvokeAgentRuntime`.

⚠️ **Do not use [sample-expose-agentcore-via-api-gateway](https://github.com/aws-samples/sample-expose-agentcore-via-api-gateway)** — it uses a private VPC with a VPC endpoint, which means the **NAT Gateway that costs 95% of your budget.** And skip streaming; that is where the day disappears.

**Exit:** real agent live on AgentCore, `agentcore invoke` works against it, Function URL responds from a browser.

---

# DAY 10 — Sep 5. **BUFFER / CHECKPOINT.**

If everything above is green, start the console. If not, spend the whole day catching up and execute the cut list. **Exit: an honest written status against `docs/CUTS.md`.** Do not spend this day early.

---

# DAYS 11–12 — Sep 6–7. Console, two screens.

**Dashboard** and **decision queue**. Case detail is an expandable panel, not a route. Build on your existing 60+ component library. Real loading / empty / error / waiting / decision-needed states — Design is your 5/5 and it is 20% of the score. **Do not delegate this.**

---

# DAY 13 — Sep 8. **CODE FREEZE, 6pm.**

- [ ] Console publicly deployed
- [ ] README + architecture diagram
- [ ] **MIT licence visible in the GitHub About section** (explicit rule, not just a LICENSE file)
- [ ] Repo public
- [ ] Secret scan clean: `pip install trufflehog && trufflehog filesystem . --only-verified` and `git secrets --scan-history`
- [ ] 13 days of measured feed numbers written into the README

---

# DAYS 14–15 — Sep 9–10. Video.

**8–16 hours for a first-timer.** Script 2h · staging demo data 2h · recording 3–4h (expect 8–15 takes) · editing 3–5h · upload 1h.

**Recording is the first honest end-to-end test of your product.** Every take exposes something — an empty state nobody designed, 40-second agent latency that is unwatchable, a case that will not reset. On Sep 9 those are a two-hour fix. On Sep 13 they are unfixable and Presentation is 20%.

**Script to 4:30, not 5:00.**

| Time | Beat |
|---|---|
| 0:00–0:25 | The pitch. Say it before anything else |
| 0:25–1:00 | `curl` the feed live — **with `$orderby=EventLastModifiedUtc desc`** |
| 1:00–1:50 | A case opens from a real meeting, both obligations derived, coordinated, verified, closed |
| 1:50–2:50 | **The detection you already caught.** Show the log: *"this fired at 14:32 on Sep 6 while I was asleep."* `EventLastModifiedUtc` before/after, fingerprint delta, the re-plan, the audit trail. Do not gamble on a live change — it is a 1% shot |
| 2:50–3:40 | **The Cedar beat.** Agent tries to close → **DENIED: no verification_id** in red. Policy file on screen for 5 seconds, `cedar.reload()`, retry, passes. Show the *denial*, not the file |
| 3:40–4:20 | The ASK. Evidence, options, recommendation. You decide. Agent resumes and verifies |
| 4:20–4:30 | Say **"Strands Agents"** out loud. Live URL on screen |

Upload **unlisted by 3pm** Sep 10, watch it end to end on your phone, then set public. YouTube's HD rendition lags 2.5–5 min behind the low-res one.

---

# DAY 16 — Sep 11. **SUBMIT by 2pm PDT.**

Then run the judge checklist as a stranger: open the repo in a private window · click the video link · open the demo URL. Three days of buffer remain.

---

# DAYS 17–18 — Sep 12–13. Blog posts.

**+0.6 on a 1–5 scale for ~1.5 days.** That is more than the entire Cedar centrepiece is worth (+0.4) at a third of the cost and none of the risk. The bonus is assessed during judging, so publishing after submitting is fine.

Titles must contain **"Agents for Humans"**:
1. *Agents for Humans: gating my agent's tools with Cedar policies*
2. *Agents for Humans: my authorization policy failed open, and how I found it* ← the best article of the three, because nobody else has written it
3. *Agents for Humans: detecting real change in a public data feed*

Have Claude Code draft all three from your commit history, then edit. 8 hours of writing becomes 3 of editing.

---

# DAY 19 — Sep 14. **HARD BUFFER.** Do nothing unless something is broken.

---

# PARALLELISATION

**Delegate to Claude Code, run unattended:** the 8 tool contracts + tests · provider directory · test scaffolding and fixtures · README prose, Mermaid diagram, `.env.example`, MIT licence · **first drafts of all three blog posts** · frontend component assembly once the API is frozen · secret scanning and lint config.

**Cannot be delegated:** AWS console/IAM debugging and the first `agentcore deploy` (Claude Code cannot see your CloudWatch log group) · **Cedar policy design** (near-zero public samples = thin model priors, and silent-skip means a wrong policy *looks* like a working default-deny) · the two console screens · the demo script's narrative order · recording · **the Sep 5 cut decision** (an agent will always try to build everything).

**Never parallelise debugging.** When `agentcore deploy` fails, three agents guessing produces three wrong answers. Read the CloudWatch log.

---

# PASS/FAIL GATES — verify each with evidence

- [ ] New project, built Aug 10 – Sep 14; pre-existing code disclosed
- [ ] Strands Agents SDK used meaningfully
- [ ] **Public** repo, MIT/Apache licence **in the GitHub About section**
- [ ] README + architecture diagram
- [ ] Video ≤5 min, **public**, on YouTube/Vimeo, covering problem / who / why
- [ ] AWS Builder ID on the submission
- [ ] Project free and unrestricted through **Oct 8**
- [ ] No secrets in repo, fixtures, logs, screenshots or the video
- [ ] "Strands Agents" in the project description, Built With section, **and spoken in the video**

---

# THE DECISION THAT EXPIRES TODAY

`hackathon-decision.md` still names **JORNALERO** as the idea. AccessFlow still fails your portfolio-diversity rule, and re-domaining the engine — same state machine, same Cedar model, same feed pattern, different obligation — costs ~2 days.

**Those two days exist today. They do not exist on Sep 2.** Pick one before you start Day 1.
