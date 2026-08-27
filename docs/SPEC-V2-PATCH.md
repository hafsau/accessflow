# AccessFlow v2 — patch to the Implementation Master Prompt

Apply these changes to `AccessFlow_Implementation_Master_Prompt.docx` before handing it to the implementation agent. Everything not listed here stays exactly as written — the state machine, the domain model, the tool contracts, the failure-mode table, the UI requirements and the security section are all good and survive unchanged.

Two changes. The first repairs the demo. The second repairs the originality score.

---

## CHANGE 1 — the inbound edge becomes real

### What was wrong
§0 instructed: *"use a deterministic simulated organization, provider directory, events dataset, case database, and communication layer."* §9.3 injected the hero disruption. §10 exposed a *"Simulate next event"* button. Every provider, every cancellation, every confirmation and all twenty seeded cases were data the author wrote. Nothing arrived from outside, so **Potential Impact** was demonstrated inside a simulator its own author built, and the video showed the presenter causing the event the agent then heroically resolved.

### What replaces it
Cases are opened by **real public meetings** published to the Granicus/Legistar Web API, which powers the legislative calendars of thousands of US state and local public bodies.

**Verified from this environment on 2026-08-25 — no API key, no registration:**

```
GET https://webapi.legistar.com/v1/seattle/Persons?$top=2      -> 200 JSON
GET https://webapi.legistar.com/v1/seattle/Events?$top=3       -> 200 JSON
```

The top event returned was, verbatim:

| Field | Value |
|---|---|
| `EventDate` | `2026-09-08` |
| `EventBodyName` | `City Council` |
| `EventTime` | `2:00 PM` |
| `EventLocation` | `Council Chamber, City Hall, 600 4th Avenue, Seattle, WA 98104` |
| `EventComment` | **`Cancellation Notice`** |
| `EventAgendaFile` | PDF URL |
| `EventInSiteURL` | meeting detail page |
| `EventLastModifiedUtc`, `EventRowVersion` | present — real change detection |

**Read that `EventComment` again.** §9.3's hero fixture B is *"Provider A cancels 24h before event,"* injected by a button. In the live feed a meeting genuinely gets cancelled and the field genuinely changes. The disruption the agent recovers from stops being staged.

### Sections to replace

| § | Change |
|---|---|
| **0 — MOCKED ENVIRONMENT** | Replace with: *"The inbound edge is real. Cases originate from live public meeting records fetched from the Legistar Web API. Provider coordination remains simulated — real interpreter procurement cannot be wired in a hackathon — and the UI must say so explicitly on every simulated interaction. The trigger, the deadline, the source document and the disruption are all real."* |
| **3 — journey step 1** | Was: *"Accommodation request is received."* Now: *"A real public meeting is published or changed. AccessFlow derives the accommodation obligations that meeting carries and opens a case."* The obligation exists whether or not anyone files a request. |
| **6.1 — entities** | `Event` gains: `source_client`, `source_event_id`, `agenda_url`, `insite_url`, `source_last_modified_utc`, `content_fingerprint`. `Organization` gains: `population_over_50k` (selects the ADA deadline) and `request_window_hours` (the body's own advance-notice rule). |
| **8 — tool contracts** | Add `poll_public_meetings() -> (new[], changes[])` and `fetch_agenda_document(url) -> document + conformance evidence record`. Keep every existing tool. |
| **9 — SIMULATION ENVIRONMENT** | Retitle **HYBRID ENVIRONMENT**. Real: meetings, bodies, dates, locations, agenda documents, cancellations, reschedules. Simulated: the provider directory and the provider communication layer. Seed 6 providers as before. Delete the 20 seeded cases — cases now come from the feed. |
| **9.3 — hero fixtures** | Delete the injected changes. Fixture A = a meeting whose agenda posts and whose obligations are coordinated to verified close. Fixture B = **a real cancellation or reschedule observed in the feed** — Seattle alone had one on 2026-09-08 at the time of writing. Fixture C = no approved provider for the body's language pair, producing a genuine ASK. Fixture D = **the agenda PDF is replaced after first review** — `content_fingerprint` changes, stale evidence is detected, re-verification is triggered. All four are real. |
| **10 — background loop** | Delete *"Expose a simple 'Simulate next event' control."* Replace with: the loop polls the feed, compares `content_fingerprint` and `EventLastModifiedUtc`, and re-runs the agent **only** on meetings that are new or that actually moved. That comparison is what makes this an agent rather than a cron job, and it is now enforced in `LegistarFeed.poll()` rather than asserted in a README. Keep a **"Replay yesterday's feed"** control for a reproducible fallback if the recording day is quiet — and label it honestly on screen. |
| **17.1 — demo script** | Replaced below. |
| **18.2 — target metrics** | Delete the invented *"16 routine cases completed automatically."* Report what the agent actually did against the real feed over the days it ran, with the date range on screen. |

### The impact case, now with sources

The old spec had **no sourced statistic in 43,000 characters**. It now has a dated legal obligation:

- The ADA Title II web rule requires **WCAG 2.1 Level AA** for *"all state and local governments… as well as special purpose districts, Amtrak, and other commuter authorities."* — [ada.gov](https://www.ada.gov/resources/2024-03-08-web-rule/)
- Compliance dates, **after** the extension published 2026-04-20 in [Federal Register 2026-07663](https://www.federalregister.gov/documents/2026/04/20/2026-07663/extension-of-compliance-dates-for-nondiscrimination-on-the-basis-of-disability-accessibility-of-web):
  - entities serving **50,000+** people — **April 26, 2027** (was April 24, 2026)
  - entities under 50,000 and special districts — **April 26, 2028** (was April 26, 2027)
- **Preexisting documents are excepted; new ones are not.** A meeting agenda posted today for a future meeting is new web content. Every agenda in the feed is a live obligation with a date attached.

Use the extended dates. The original ones are wrong as of April 2026 and a judge who checks will notice.

---

## CHANGE 2 — the authority model becomes policy, not Python

### What was wrong
§5.2's ACT / ASK / BLOCK matrix was a table in a document, to be implemented as branching code. That reads as a workflow engine, and §7.5's orchestrator-delegating-to-specialists-via-graph is the single most-sampled Strands pattern — it is what the official examples show. **Creativity & Originality** asks for a *non-obvious* use of Strands, and AWS's own engineering blog measures [steering hooks at 100% accuracy against graph workflows at 80.8%](https://strandsagents.com/blog/what-we-learned-from-one-year-of-building-production-agents/) while warning against multi-agent decomposition.

### What replaces it
The matrix becomes `policies/accessflow.cedar` — a real Cedar policy file evaluated by the **Cedar Authorization intervention** before every tool call, default-deny and fail-closed. Cedar is AWS's own policy language, it also underpins AgentCore's policy primitive, and it has close to zero public Strands samples.

The mapping:

| §5.2 mode | Cedar |
|---|---|
| **ACT** | an unconditional `permit` |
| **ASK** | the direct action is forbidden without a `decision_id`; `request_human_decision` is always permitted instead |
| **BLOCK** | `forbid` — and `forbid` beats every `permit`, with no escape |

The product's central invariant stops being a promise in a README:

```cedar
forbid(principal, action == Action::"close_case", resource)
unless {
  context.input has verification_id &&
  context.session.verification_passed == true
};
```

`verification_passed` is populated by `_enrich_context()` from persisted case state, never from model output. **The model cannot assert its way to a closed case.**

The same file carries the guard that keeps AccessFlow out of the trap that killed two earlier concepts — it reads records belonging to real named public bodies, so it must never emit a non-compliance verdict about one:

```cedar
forbid(principal, action, resource)
when { context.input has asserts_noncompliance && context.input.asserts_noncompliance == true };
```

### Sections to replace

| § | Change |
|---|---|
| **5.2** | Replace the table with a pointer to `policies/accessflow.cedar` and reproduce the file in an appendix. The table becomes generated documentation, not the source of truth. |
| **7.5 — agent interaction** | Was: *"choose workflow/graph for predictability."* Now: **one orchestrator agent**, with Requirement / Provider / Verification exposed as agents-as-tools. Cite the AWS 100% vs 80.8% measurement in the README as the reason. Fewer agents, deeper SDK surface. |
| **14 — Strands requirements** | Add: `CedarAuthorization` for the authority model; `LLMSteeringHandler` for what policy cannot express; `context_manager="auto"` (AWS benchmark: 55% cost reduction, accuracy 68% → 98%); interrupts with `S3SessionManager` so a case genuinely survives process death; OTEL to CloudWatch; a Strands Evals suite with chaos testing. |
| **15 — AgentCore** | Raise Cedar/policy from "medium" to **mandatory**. Never put the polling loop inside AgentCore Runtime — continuous CPU there costs ~$114 over 44 days. Poll on Lambda free tier, invoke AgentCore per case. **Never provision a NAT Gateway**: $0.045/hr × 1,056 hrs = **$47.52**, 95% of the budget, before a single GB. |
| **19.1 — unit tests** | Add: a policy test suite asserting each `forbid` actually denies. The headline test is `close_case` without `verification_id` — it must be denied by Cedar, not by application code. |

### New files

```
policies/accessflow.cedar                 the authority model, 162 lines
backend/app/agents/authority.py           Cedar + steering wiring, verified APIs
backend/app/tools/legistar.py             the real feed, change detection, ADA obligations
```

Verified API surface, current as of 2026-08-25:

```python
from strands.vended_interventions.cedar import CedarAuthorization
from strands.vended_plugins.steering import LLMSteeringHandler

agent = Agent(tools=[...], interventions=[cedar], plugins=[steering])
cedar.reload()   # atomic hot-swap; an invalid policy leaves the prior one in effect
```

`context.input.*` holds tool arguments. `context.session.*` holds `hour_utc`, `call_count`, plus whatever `context_enricher` adds. Steering is **Python-only**. Set `on_error="deny"` — never `"proceed"`; a policy-engine failure must close the gate.

---

## §17.1 — the new five-minute demo

Every beat below runs on real data. Nothing is injected.

| Time | On screen | Line |
|---|---|---|
| 0:00–0:25 | The console, live, with today's date | *"Every public meeting in America owes accessibility obligations it has to meet before the meeting happens. Nobody is tracking whether they actually get met."* |
| 0:25–1:00 | `curl` the Legistar feed on camera. Real meetings, real bodies, real dates | *"No API key. This is Seattle's actual council calendar, right now."* |
| 1:00–1:50 | A case opens from a real meeting. The agent derives the obligations, cites the April 26 2027 ADA deadline, coordinates a provider, verification passes, case closes | The routine path |
| 1:50–2:50 | **The real disruption.** A meeting in the feed carrying `EventComment: "Cancellation Notice"` — or a real reschedule caught by `content_fingerprint`. The agent re-plans without being asked | *"I did not stage this. The city cancelled this meeting."* |
| 2:50–3:40 | **The Cedar beat.** The agent tries to close a case. Cedar denies it: no `verification_id`. Open `accessflow.cedar` on screen, show the four-line `forbid`, call `cedar.reload()` live | *"It is not that the agent was told not to. It cannot. A policy file holds the gate, and the model never sees the key."* |
| 3:40–4:20 | The ASK: no approved provider for this body's language pair. Evidence, options, recommendation. Coordinator decides. Agent resumes and verifies | Human-in-the-loop |
| 4:20–4:45 | `kill -9` mid-batch. Restart. `S3SessionManager` resumes the case mid-flight | Durability |
| 4:45–5:00 | The dashboard: what actually ran, over the real date range | Say *"Strands Agents"* out loud |

**Fallback, and be honest about it on screen:** if the recording day is quiet, use "Replay yesterday's feed" — real captured records, replayed, labelled as such. That is still real data. It is not an injected fixture.

---

## Score movement

| Criterion | Before | After | Why |
|---|---|---|---|
| Technological Implementation | 4 | **5** | Cedar as a fail-closed authority layer, steering, durable interrupts, OTEL, evals. Deeper and rarer than an agent graph. |
| Design | 5 | **5** | Unchanged. Still the strongest part. |
| Potential Impact | 2 | **4** | A dated federal obligation with citations, applied to real meetings, replacing an unsourced persona. |
| Creativity & Originality | 2 | **4** | The authority model is a Cedar policy file, not `if` statements. Near-zero public samples. |
| Presentation | 3 | **5** | No "Simulate event" button. A real cancellation on camera. |
| **Weighted** | **3.2** | **4.6** | |

---

## The one thing this patch does NOT fix

**Constraint 5 — the portfolio-diversity rule.** AccessFlow is still an accessibility product: ASL interpreters, captioning, accessible materials, mobility access. Braille Tonight and Persona Lab were rejected on exactly that ground. This patch cannot repair it without changing what the product is.

It is also now more US-anchored, not less, because the ADA and Legistar are both US.

Two ways out, if that matters more than the score:

1. **Accept it.** This is a *civic infrastructure* product about public bodies meeting published obligations. It is not a product for disabled users, which is what the earlier two were. That is a defensible distinction — but it is a distinction, not a difference, and worth deciding deliberately rather than by default.
2. **Keep the engine, change the obligation.** Every line of the architecture — the state machine, the Cedar authority model, the verification invariant, the operations console, the real-feed inbound edge — is domain-agnostic. Point it at any recurring published obligation with a deadline and it works unchanged. That clears Constraint 5 and costs roughly two days of re-domaining.
