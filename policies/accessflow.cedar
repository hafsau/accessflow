// ============================================================================
// AccessFlow — Authority Model as Policy
//
// This file REPLACES the ACT / ASK / BLOCK matrix in §5.2 of the master prompt.
// It is not documentation of the rules. It IS the rules: Strands evaluates every
// tool call against this file before the tool runs, and denies fail-closed.
//
// Cedar semantics that matter here:
//   - Default is DENY. A tool call runs only if some `permit` matches.
//   - `forbid` always beats `permit`. Blocks are absolute.
//   - If the principal cannot be resolved, every call is denied.
//
// Mapping to the §5.2 matrix:
//   ACT   -> an unconditional `permit`
//   ASK   -> the direct action is forbidden without a human decision id;
//            `request_human_decision` is always permitted instead
//   BLOCK -> `forbid`, with no escape hatch
//
// Reload without restarting the agent:  cedar.reload()
// ============================================================================


// ---------------------------------------------------------------------------
// ACT — read-only information gathering. Always safe.
// ---------------------------------------------------------------------------

permit(principal, action == Action::"get_case",     resource);
permit(principal, action == Action::"get_event",    resource);
permit(principal, action == Action::"search_providers", resource);
permit(principal, action == Action::"check_provider_availability", resource);
permit(principal, action == Action::"check_provider_response", resource);
permit(principal, action == Action::"get_material_accessibility_status", resource);
permit(principal, action == Action::"fetch_agenda_document", resource);


// ---------------------------------------------------------------------------
// ACT — routine coordination and internal state, gated on an idempotency key
// so a retry or a re-delivered webhook can never double-send.
// ---------------------------------------------------------------------------

permit(principal, action == Action::"create_case", resource)
when { context has input && context.input has idempotency_key };

permit(principal, action == Action::"update_case", resource)
when { context has input && context.input has idempotency_key };

permit(principal, action == Action::"update_event_notes", resource)
when {
  context has input &&
  context.input has idempotency_key &&
  context.input has based_on_verified_state &&
  context.input.based_on_verified_state == true
};

permit(principal, action == Action::"send_provider_request", resource)
when {
  context has input &&
  context.input has idempotency_key &&
  context.input has provider_approved &&
  context.input.provider_approved == true
};

// Follow-ups are permitted, but the cadence rule is policy, not prompt.
// One reminder per case per 24h, and never before the request has aged 24h.
permit(principal, action == Action::"send_reminder", resource)
when {
  context has input && context.input has idempotency_key &&
  context has session &&
  context.session has reminders_sent_24h && context.session.reminders_sent_24h == 0 &&
  context.session has hours_since_request && context.session.hours_since_request >= 24
};

permit(principal, action == Action::"verify_fulfillment", resource);
permit(principal, action == Action::"record_event_change", resource)
when { context has input && context.input has idempotency_key };


// ---------------------------------------------------------------------------
// ASK — the agent may always ask. Asking is never gated.
// ---------------------------------------------------------------------------

permit(principal, action == Action::"request_human_decision", resource);

permit(principal, action == Action::"apply_human_decision", resource)
when { context has input && context.input has decision_id };


// ---------------------------------------------------------------------------
// ASK, enforced — actions that require a human decision to already exist.
// The agent cannot talk its way past these; it has to go get the decision.
// ---------------------------------------------------------------------------

// Substitution is ACT only when the org's policy allows it AND the replacement
// is a confirmed comparable service. Otherwise it needs a decision.
permit(principal, action == Action::"replace_provider", resource)
when {
  context has input &&
  context.input has idempotency_key &&
  context.input has replacement_approved && context.input.replacement_approved == true &&
  context.input has substitution_policy_permits && context.input.substitution_policy_permits == true &&
  context.input has comparable_service_confirmed && context.input.comparable_service_confirmed == true
};

permit(principal, action == Action::"replace_provider", resource)
when {
  context has input &&
  context.input has idempotency_key &&
  context.input has decision_id &&
  context.input has replacement_approved && context.input.replacement_approved == true
};

// Changing the *mode* of an accommodation (in-person -> remote, ASL -> CART)
// is preference-sensitive. Never autonomous.
permit(principal, action == Action::"choose_accommodation_mode", resource)
when { context has input && context.input has decision_id };


// ---------------------------------------------------------------------------
// BLOCK — absolute. `forbid` overrides every permit above.
// ---------------------------------------------------------------------------

// The core product invariant: a case cannot close without verified evidence.
// This is the one rule that, if it ever fails, makes the whole product a lie.
forbid(principal, action == Action::"close_case", resource)
unless {
  context has session &&
  context.session has verification_passed &&
  context.session.verification_passed == true &&
  context has input &&
  context.input has verification_id
};

// An unapproved provider is never contacted, decision or no decision.
forbid(principal, action == Action::"send_provider_request", resource)
when { context has input && context.input has provider_approved && context.input.provider_approved == false };

forbid(principal, action == Action::"replace_provider", resource)
when { context has input && context.input has replacement_approved && context.input.replacement_approved == false };

// Organizational accessibility policy is outside the agent's authority. Full stop.
forbid(principal, action == Action::"override_policy", resource);

// ---------------------------------------------------------------------------
// BLOCK — the third-party judgment guard.
//
// AccessFlow reads real public meeting records belonging to real named public
// bodies. It must never emit a claim that a named entity is non-compliant,
// and must never publish outside the coordinator's own organization.
// The agent works FOR the entity, not against it.
// ---------------------------------------------------------------------------

forbid(principal, action, resource)
when { context has input && context.input has asserts_noncompliance && context.input.asserts_noncompliance == true };

forbid(principal, action, resource)
when { context has input && context.input has publishes_externally && context.input.publishes_externally == true };

// Any statement about a document's accessibility must be phrased as recorded
// evidence, never as a verdict. Enforced by requiring the evidence id.
forbid(principal, action == Action::"record_material_finding", resource)
unless { context has input && context.input has evidence_id };


// ---------------------------------------------------------------------------
// Rate ceiling — a runaway loop cannot spam a real provider or a real inbox.
// ---------------------------------------------------------------------------

forbid(principal, action == Action::"send_provider_request", resource)
when { context has session && context.session has call_count && context.session.call_count > 25 };

forbid(principal, action == Action::"send_reminder", resource)
when { context has session && context.session has call_count && context.session.call_count > 10 };
