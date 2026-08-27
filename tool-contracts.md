# Tool contracts — FROZEN 2026-08-28

**These JSON shapes do not change after today.** Freezing them is what lets the console and the agent be built in parallel from here. If a shape genuinely must change, it is a decision with a written reason — not a refactor.

Eight tools. Revised from `CUTS.md` after the Aug 27 grounding test moved agenda extraction to the centre of the product.

## The architectural rule these encode

| Layer | Owner | Why |
|---|---|---|
| Cancellation detection, deadline arithmetic, legal basis, state transitions | **deterministic Python** | Test 1: the model lost to 30 lines of rules and missed a literal "Cancellation Notice" string |
| Read the agenda, extract the body's published accommodation policy, quote the source | **the agent** | Test 2: 6/6 distinct profiles, 92% grounded. No rule engine does this |
| Refuse any claim whose quote does not verify against source text | **Cedar** | The grounding check becomes policy, not a promise |

**`verify_quote` is deliberately NOT a tool.** It is a deterministic helper feeding Cedar's `context_enricher`. If the model could call it, the model could game it. As policy context it is untouchable — that is the whole point.

---

## 1. `poll_public_meetings` — deterministic, no model

```json
// input
{ "clients": ["seattle","sanjose"], "days_ahead": 60 }

// output
{ "new": [ { "key":"seattle:6860", "client":"seattle", "event_id":6860,
             "body_name":"City Council", "date":"2026-09-08", "time":"2:00 PM",
             "location":"Council Chamber, City Hall, 600 4th Avenue",
             "agenda_url":"https://...pdf", "comment":"Cancellation Notice",
             "insite_url":"https://...", "last_modified_utc":"2026-08-18T...",
             "fingerprint":"a1b2c3d4e5f6a7b8" } ],
  "changes": [ { "key":"seattle:6860", "change_type":"cancelled",
                 "old_value":"a1b2c3d4e5f6a7b8", "new_value":"9f8e7d6c5b4a3928",
                 "detected_at":"2026-08-28T17:42:11Z" } ] }
```
`change_type` ∈ `cancelled` · `rescheduled_or_relocated` · `agenda_posted` · `agenda_replaced`
**Invariant:** a poll where nothing moved returns `{"new":[],"changes":[]}` and triggers **zero** agent invocations. That is what makes this an agent and not a cron job — say it on camera.

## 2. `fetch_agenda_document` — deterministic

```json
// input
{ "agenda_url": "https://...pdf", "max_chars": 12000 }

// output
{ "ok": true, "content_type": "pdf", "char_count": 48213,
  "text": "...", "sha256": "e3b0c44298fc1c14...", "truncated": true }
```
Handles PDF (`pypdf`) and HTML. `ok:false` + `error_code` on fetch failure — never a partial success.
**Cache every response to S3 keyed by sha256.** If Legistar rate-limits during the Sep 15–Oct 8 judging window, the demo serves from cache and survives.

## 3. `extract_accommodation_policy` — ⭐ THE AGENT'S REAL WORK

```json
// input
{ "case_id":"case_001", "agenda_text":"...", "body_name":"City Council", "date":"2026-09-08" }

// output
{ "claims": [
    { "type":"language_access",
      "detail":"Live translation available in 50+ languages on request",
      "quote":"For live translations in over 50 languages, please go to",
      "quote_verified": null },
    { "type":"alternative_format",
      "detail":"Alternative-format agendas available under the ADA",
      "quote":"To request an alternative format agenda under the Ameri",
      "quote_verified": null } ] }
```
`quote_verified` is **always null on output.** The agent does not get to assert it. It is filled in by the deterministic checker before anything downstream sees it.

`type` ∈ `language_access` · `alternative_format` · `ada_contact` · `remote_access` · `assistive_listening` · `interpreter_request` · `request_deadline` · `other`

**Prompt rule, non-negotiable:** every claim must copy an exact phrase from the agenda. No paraphrase, no outside knowledge. Measured 92% grounded on 38 real claims — that number goes in the README.

## 4. `derive_obligations` — deterministic, no model

```json
// input
{ "event": { ... }, "population_over_50k": true }

// output
{ "obligations": [
    { "basis":"28 CFR 35.160", "category":"effective_communication",
      "description":"Public entity must furnish appropriate auxiliary aids on request. In force since July 26 1991; no phase-in.",
      "deadline":"2026-09-06T14:00:00Z", "must_have":true },
    { "basis":"28 CFR 35.200", "category":"document_conformance",
      "description":"Agenda document must meet WCAG 2.1 AA by the entity's compliance date.",
      "deadline":"2027-04-26", "must_have":true } ] }
```
⚠️ **Two bases, always.** §35.160 governs interpreters/CART and has been in force since **1991** — it triggers now. Subpart H governs **web content only**, dated **April 26 2027** (50k+) / **2028** (smaller), as extended by FR 2026-07663 (an Interim Final Rule — say "as extended"). Citing the web-rule deadline while coordinating a human interpreter is the error a judge catches.

## 5. `search_providers` — deterministic, simulated directory
```json
// input  { "service_type":"asl_interpreter", "event_time":"2026-09-08T14:00:00Z", "location":"Seattle, WA" }
// output { "providers":[ {"id":"prov_a","name":"...","approved":true,"available":true,
//                        "services":["asl_interpreter"],"reliability_score":0.94} ] }
```
3 seeded providers, not 6. The provider layer is simulated — **the UI must say so on every simulated interaction.** The trigger, deadline, source document and disruption are all real; procurement cannot be.

## 6. `send_provider_request` — mutating
```json
// input  { "case_id":"case_001", "provider_id":"prov_a", "idempotency_key":"case_001:prov_a:v1",
//          "provider_approved": true, "request_payload": { ... } }
// output { "interaction_id":"int_009", "status":"sent", "sent_at":"..." }
```
Cedar denies without `idempotency_key`, and denies outright when `provider_approved` is false.

## 7. `request_human_decision` — always permitted
```json
// input  { "case_id":"case_001", "question":"...", "options":[{"id":"a","label":"...","consequence":"..."}],
//          "recommendation":"a", "evidence":[{"fact":"...","source":"..."}] }
// output { "decision_id":"dec_004", "state":"ASK" }
```
Must answer, in order: what changed · what was checked · safe options · recommendation · why the agent stopped.

## 8. `close_case` — the invariant
```json
// input  { "case_id":"case_001", "verification_id":"ver_012", "idempotency_key":"close:case_001:v1" }
// output { "case_id":"case_001", "state":"CLOSED", "closed_at":"..." }
```
Cedar `forbid` unless `context.session.verification_passed == true` **and** `context.input has verification_id`. `verification_passed` comes from persisted case state via `context_enricher` — never from model output.

⚠️ Every `context` dereference in the policy must be `has`-guarded in the same clause. Cedar **silently skips policies that error**, so an unguarded deref on a missing attribute fails **open**. This was found by execution, not by reading. Test it with an empty enricher.

---

## Rules for all eight

1. Typed Pydantic models in, typed out. Never prose.
2. Every mutating tool takes `idempotency_key` and writes one `AgentAction` audit row.
3. Structured error codes, never exceptions across the boundary.
4. Deterministic against fixtures — no tool fabricates success.
5. Validate every id; reject invalid state transitions.
6. **Zero model calls in tools 1, 2, 4, 5, 6, 8.** Only tool 3 invokes a model. That is the cost architecture and it is also the honest answer to "where is the LLM actually doing work?"

## Definition of done for Day 2
- [ ] All 8 implemented as Strands `@tool` functions in `backend/app/tools/`
- [ ] One pytest per tool: happy path · invalid id · duplicate idempotency key · malformed input
- [ ] `pytest backend/tests/tools/` green
- [ ] `verify_quote` exists as a **helper**, not a tool, with its own test
- [ ] These shapes committed and unchanged from this document
