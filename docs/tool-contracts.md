# Tool Contracts — AccessFlow

**Frozen 2026-08-27.** These 8 tools + `extract_accommodation_policy` are the complete set.

All mutating tools require `idempotency_key` and write one `AgentAction` audit row.
All tools return typed Pydantic models with structured error codes — never exceptions across the boundary.

---

## 1. get_case

**Read-only.** Retrieve a case by ID.

```json
// Input
{"case_id": "case_abc123"}

// Output — success
{
  "ok": true,
  "case": {
    "case_id": "case_abc123",
    "event_id": "seattle:6860",
    "state": "COORDINATING",
    "obligations": [
      {"category": "effective_communication", "basis": "28 CFR 35.160", "deadline": "2026-09-06T14:00:00Z"},
      {"category": "document_conformance", "basis": "28 CFR 35.200", "deadline": "2027-04-26"}
    ],
    "provider_requests": ["req_001"],
    "verification_id": null,
    "verification_passed": false,
    "created_at": "2026-08-27T10:00:00Z",
    "updated_at": "2026-08-27T12:00:00Z"
  }
}

// Output — error
{"ok": false, "error_code": "CASE_NOT_FOUND", "message": "No case with id case_xyz"}
```

---

## 2. get_event

**Read-only.** Retrieve a meeting/event by key.

```json
// Input
{"event_key": "seattle:6860"}

// Output — success
{
  "ok": true,
  "event": {
    "event_key": "seattle:6860",
    "client": "seattle",
    "event_id": 6860,
    "body_name": "City Council",
    "date": "2026-09-08",
    "time": "2:00 PM",
    "location": "Council Chamber, City Hall",
    "agenda_url": "https://...",
    "comment": "Cancellation Notice",
    "is_cancelled": true,
    "last_modified_utc": "2026-08-18T14:32:00Z",
    "content_fingerprint": "a1b2c3d4"
  }
}

// Output — error
{"ok": false, "error_code": "EVENT_NOT_FOUND", "message": "No event with key xyz:999"}
```

---

## 3. fetch_agenda_document

**Read-only.** Download and analyze an agenda PDF.

```json
// Input
{"agenda_url": "https://legistar.../agenda.pdf", "max_pages": 5}

// Output — success
{
  "ok": true,
  "document": {
    "url": "https://...",
    "page_count": 12,
    "text_preview": "CITY COUNCIL AGENDA...",
    "fetched_at": "2026-08-27T12:00:00Z",
    "content_hash": "sha256:abcd1234..."
  }
}

// Output — error
{"ok": false, "error_code": "FETCH_FAILED", "message": "HTTP 404"}
{"ok": false, "error_code": "PARSE_FAILED", "message": "Invalid PDF"}
```

---

## 4. search_providers

**Read-only.** Search the provider directory.

```json
// Input
{
  "service_type": "ASL_INTERPRETER",
  "jurisdiction": "seattle",
  "date": "2026-09-08",
  "time": "14:00"
}

// Output — success
{
  "ok": true,
  "providers": [
    {
      "provider_id": "prov_001",
      "name": "Pacific Interpreting",
      "service_types": ["ASL_INTERPRETER", "CART"],
      "jurisdictions": ["seattle", "kingcounty"],
      "approved": true,
      "rating": 4.8
    }
  ]
}

// Output — no matches
{"ok": true, "providers": []}
```

---

## 5. send_provider_request

**Mutating.** Send a coordination request to a provider.

```json
// Input
{
  "idempotency_key": "req_case123_prov001_v1",
  "case_id": "case_abc123",
  "provider_id": "prov_001",
  "service_type": "ASL_INTERPRETER",
  "event_date": "2026-09-08",
  "event_time": "14:00",
  "event_location": "Council Chamber, City Hall",
  "provider_approved": true
}

// Output — success
{
  "ok": true,
  "request": {
    "request_id": "req_001",
    "case_id": "case_abc123",
    "provider_id": "prov_001",
    "status": "SENT",
    "sent_at": "2026-08-27T12:00:00Z"
  }
}

// Output — errors
{"ok": false, "error_code": "CASE_NOT_FOUND", "message": "..."}
{"ok": false, "error_code": "PROVIDER_NOT_FOUND", "message": "..."}
{"ok": false, "error_code": "PROVIDER_NOT_APPROVED", "message": "Cannot send to unapproved provider"}
{"ok": false, "error_code": "DUPLICATE_REQUEST", "message": "Idempotency key already used"}
{"ok": false, "error_code": "INVALID_STATE", "message": "Case is CLOSED"}
```

---

## 6. request_human_decision

**Mutating.** Escalate a decision to the human coordinator.

```json
// Input
{
  "idempotency_key": "dec_case123_v1",
  "case_id": "case_abc123",
  "decision_type": "PROVIDER_SUBSTITUTION",
  "context": "Original provider unavailable",
  "options": [
    {"option_id": "A", "description": "Use Pacific Interpreting (similar rating)", "recommended": true},
    {"option_id": "B", "description": "Use SignOn (lower rating, available)"},
    {"option_id": "C", "description": "Postpone coordination"}
  ],
  "evidence": {
    "original_provider": "prov_002",
    "original_response": "DECLINED",
    "deadline": "2026-09-06T14:00:00Z"
  }
}

// Output — success
{
  "ok": true,
  "decision": {
    "decision_id": "dec_001",
    "case_id": "case_abc123",
    "status": "PENDING",
    "requested_at": "2026-08-27T12:00:00Z"
  }
}

// Output — errors
{"ok": false, "error_code": "CASE_NOT_FOUND", "message": "..."}
{"ok": false, "error_code": "DUPLICATE_REQUEST", "message": "Idempotency key already used"}
```

---

## 7. verify_fulfillment

**Read-only.** Check whether all obligations for a case are fulfilled.

```json
// Input
{"case_id": "case_abc123"}

// Output — success (all fulfilled)
{
  "ok": true,
  "verification": {
    "verification_id": "ver_001",
    "case_id": "case_abc123",
    "passed": true,
    "checked_at": "2026-08-27T12:00:00Z",
    "obligations": [
      {"category": "effective_communication", "fulfilled": true, "evidence": "req_001 CONFIRMED"},
      {"category": "document_conformance", "fulfilled": true, "evidence": "WCAG check passed"}
    ]
  }
}

// Output — success (not all fulfilled)
{
  "ok": true,
  "verification": {
    "verification_id": "ver_002",
    "case_id": "case_abc123",
    "passed": false,
    "checked_at": "2026-08-27T12:00:00Z",
    "obligations": [
      {"category": "effective_communication", "fulfilled": true, "evidence": "req_001 CONFIRMED"},
      {"category": "document_conformance", "fulfilled": false, "evidence": null}
    ]
  }
}

// Output — error
{"ok": false, "error_code": "CASE_NOT_FOUND", "message": "..."}
```

---

## 8. close_case

**Mutating.** Close a case after verification passes.

```json
// Input
{
  "idempotency_key": "close_case123_v1",
  "case_id": "case_abc123",
  "verification_id": "ver_001"
}

// Output — success
{
  "ok": true,
  "case": {
    "case_id": "case_abc123",
    "state": "CLOSED",
    "closed_at": "2026-08-27T12:00:00Z",
    "verification_id": "ver_001"
  }
}

// Output — errors
{"ok": false, "error_code": "CASE_NOT_FOUND", "message": "..."}
{"ok": false, "error_code": "VERIFICATION_NOT_FOUND", "message": "..."}
{"ok": false, "error_code": "VERIFICATION_FAILED", "message": "Cannot close: verification did not pass"}
{"ok": false, "error_code": "INVALID_STATE", "message": "Case already CLOSED"}
{"ok": false, "error_code": "DUPLICATE_REQUEST", "message": "Idempotency key already used"}
```

---

## 9. extract_accommodation_policy

**Read-only. CALLS MODEL.** Extract accommodation policy from agenda text.

This is the ONE tool that calls the LLM. It extracts accommodation needs based on agenda content.

```json
// Input
{
  "agenda_text": "PUBLIC HEARING: Proposed housing ordinance...",
  "body_name": "City Council",
  "jurisdiction": "seattle"
}

// Output — success
{
  "ok": true,
  "policy": {
    "recommended_accommodations": ["ASL_INTERPRETER", "SPANISH_INTERPRETER", "CART"],
    "priority": "HIGH",
    "reasoning": "Public hearing on housing ordinance with community input",
    "quote": "PUBLIC HEARING: Proposed housing ordinance",
    "quote_verified": null
  }
}
```

**Note:** `quote_verified` is ALWAYS `null` when returned from the tool. The `verify_quote()` function in `grounding.py` is called by the context_enricher, not by the model. The model cannot verify its own quotes.

---

## Error Codes

| Code | Meaning |
|------|---------|
| `CASE_NOT_FOUND` | No case with the given ID |
| `EVENT_NOT_FOUND` | No event with the given key |
| `PROVIDER_NOT_FOUND` | No provider with the given ID |
| `PROVIDER_NOT_APPROVED` | Provider exists but is not approved |
| `VERIFICATION_NOT_FOUND` | No verification with the given ID |
| `VERIFICATION_FAILED` | Verification exists but did not pass |
| `INVALID_STATE` | Operation not allowed in current case state |
| `DUPLICATE_REQUEST` | Idempotency key already used |
| `FETCH_FAILED` | HTTP error fetching resource |
| `PARSE_FAILED` | Could not parse document |
| `VALIDATION_ERROR` | Input validation failed |

---

## AgentAction Audit Row

Every mutating tool writes one row:

```json
{
  "action_id": "act_uuid",
  "tool_name": "send_provider_request",
  "idempotency_key": "req_case123_prov001_v1",
  "case_id": "case_abc123",
  "input_hash": "sha256:...",
  "output_hash": "sha256:...",
  "success": true,
  "error_code": null,
  "created_at": "2026-08-27T12:00:00Z"
}
```
