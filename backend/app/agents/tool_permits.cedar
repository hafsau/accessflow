// Supplementary permits for tools not in the main policy file.
// These are all read-only or deterministic tools that don't require
// special authorization conditions.

// Read-only: polls the live Legistar feed
permit(principal, action == Action::"poll_public_meetings", resource);

// Deterministic: computes ADA obligations from event data
permit(principal, action == Action::"derive_obligations", resource);

// Model-bearing but read-only: extracts policy from agenda text
permit(principal, action == Action::"extract_accommodation_policy", resource);

// Creates a case for a meeting (requires idempotency_key via tool signature)
permit(principal, action == Action::"create_case", resource)
when { context has input && context.input has idempotency_key };

// Simulates provider confirmation for demo (requires idempotency_key)
permit(principal, action == Action::"confirm_provider_request", resource)
when { context has input && context.input has idempotency_key };

// Close case - the forbid...unless rule in main policy checks verification_passed,
// but Cedar still needs a permit (default-deny). This permit allows the action;
// the main policy's forbid will block if verification is not passed.
permit(principal, action == Action::"close_case", resource)
when { context has input && context.input has verification_id };

// ---------------------------------------------------------------------------
// BRACES — hard ceiling on tool calls per case.
//
// Belt: Strands SDK limits={turns: 12} stops the loop.
// Braces: This forbid catches any bypass — a looping agent that somehow evades
// the SDK limit will be blocked by Cedar before tool 16 runs.
//
// Every dereference is has-guarded as required by Cedar semantics.
// ---------------------------------------------------------------------------
forbid(principal, action, resource)
when {
    context has session &&
    context.session has turns &&
    context.session.turns > 15
};

// ---------------------------------------------------------------------------
// SEQUENTIAL PROVIDER CONTACT — one provider at a time.
//
// The §35.160 obligation needs ONE provider. Contact the best match, wait for
// response. Only contact a second provider after the first declines.
//
// This forbid blocks send_provider_request when 2+ requests already exist
// UNLESS a prior request was declined. Every dereference is has-guarded.
// ---------------------------------------------------------------------------
forbid(principal, action == Action::"send_provider_request", resource)
when {
    context has session &&
    context.session has provider_requests_for_case &&
    context.session.provider_requests_for_case >= 2
}
unless {
    context has session &&
    context.session has has_prior_decline &&
    context.session.has_prior_decline == true
};
