# Cedar — Day 4: diagnostics FIRST, policies second

**Read this before writing a single policy.**

## Why the order matters

Cedar is default-deny, and it **silently skips any policy that errors during evaluation** — it treats an erroring policy as if it never existed ([why Cedar ignores errors](https://cedarland.blog/design/why-ignore-errors/content.html)).

Combine those two facts and you get the trap:

> **A typo in a `permit` is byte-identical to "no policy matched."** Both produce a Deny. In a 60-line file, that is a black box.

And the mirror image is worse. A `forbid` whose condition errors is **skipped**, so a `permit` elsewhere wins and the call goes through. **The gate fails OPEN.**

That already happened once in this project. The `close_case` invariant — the one rule whose failure makes the whole product a lie — passed the demo case and failed on two others, because `context.session.verification_passed` errors when the attribute is missing, and an erroring `forbid` is skipped. It was found by *executing* the policy, not by reading it.

So: **build the diagnostic harness before policy #2 exists.**

---

## Step 1 — the harness (do this first)

`backend/app/agents/cedar_debug.py`

```python
import logging
log = logging.getLogger("cedar")

def trace(response, tool_name: str, tool_input: dict) -> None:
    """Log EVERY evaluation: decision, which policies determined it, and any
    evaluation errors. Errors are the ones that matter — an erroring policy is
    invisible in the decision alone."""
    diag = getattr(response, "diagnostics", None)
    log.info(
        "cedar decision=%s tool=%s determining=%s errors=%s",
        getattr(response, "decision", "?"),
        tool_name,
        getattr(diag, "reason", None),      # which policy ids decided
        getattr(diag, "errors", None),      # ← the silent killers
    )
```

**Wire it so every evaluation is logged, and fail loudly in dev:** if `diagnostics.errors` is non-empty during a test run, raise. A skipped policy must never be silent again.

## Step 2 — a Cedar schema

Define the entity types, actions and context attributes as a Cedar schema so a typo fails at **startup**, not as a mysterious runtime denial. This is the second line of defence and it is cheap.

## Step 3 — three policies, each with a test, then stop

Not sixty lines today. Three policies:

1. `permit` the read-only tools (`get_case`, `get_event`, `search_providers`)
2. `permit send_provider_request` when `idempotency_key` present **and** `provider_approved == true`
3. **`forbid close_case`** unless verified — the invariant

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

**Every `context` dereference must sit behind a `has` guard in the same clause.** No exceptions. `policies/accessflow.cedar` is already written this way — 0 unguarded derefs across 22 policies. Keep it that way.

## Step 4 — the test that must exist

This is the one that caught the fail-open. It is not optional.

```python
def test_close_case_denied_with_empty_enricher():
    """context.input is MODEL-GENERATED — the model can invent verification_id.
       Only context.session is trustworthy, and a missing attribute ERRORS,
       and Cedar SKIPS erroring policies. Hence the has-guards."""
    cedar = CedarAuthorization(
        policies="policies/accessflow.cedar",
        principal_resolver=lambda s: {"type": "Coordinator", "id": "c1"},
        context_enricher=lambda ctx: {},          # ← the failure case
        on_error="deny",
    )
    assert denied(cedar, "close_case", {"verification_id": "totally_made_up"})


def test_close_case_denied_when_principal_unresolvable():
    """No principal = deny everything. Cedar's documented behaviour; assert it."""
    cedar = CedarAuthorization(
        policies="policies/accessflow.cedar",
        principal_resolver=lambda s: None,
        context_enricher=lambda ctx: {"verification_passed": True},
        on_error="deny",
    )
    assert denied(cedar, "close_case", {"verification_id": "ver_1"})


def test_no_policy_evaluation_errors():
    """A skipped policy is invisible in the decision. Catch it here."""
    # run every tool through the handler; assert diagnostics.errors is empty each time
```

## Three more traps, documented

| Trap | Consequence |
|---|---|
| `principal_resolver` returns `None` | **Every** tool call denied. Correct behaviour, baffling symptom |
| `context.session.call_count` is **1-based**, includes the current call, and **persists across sessions with no time reset** | Rate-limit policies drift; a long-lived session hits caps unexpectedly |
| Policies only refresh on explicit `cedar.reload()` | Editing the `.cedar` file changes nothing until reload — which is also the live demo beat |

## Wiring

`backend/app/agents/authority.py` is already written and verified against the docs:
- `CedarAuthorization(policies=<path>, principal_resolver=…, context_enricher=…, on_error="deny")`
- `Agent(tools=[...], interventions=[cedar])`
- `cedar.reload()` — atomic; an invalid policy leaves the previous one in effect

**`on_error` must be `"deny"`, never `"proceed"`.** A policy-engine failure closes the gate. Note this covers *engine* failure — it does **not** cover per-policy error-skip. That is what the `has` guards are for.

⚠️ `verification_passed` is populated by `_enrich_context()` **from persisted case state**, never from model output. That is the whole architecture in one line: the model cannot assert its way to a closed case.

## Day 4 exit criterion — hard stop at 6 hours

- [ ] `cedar_debug.trace()` logging on every evaluation, raising on `diagnostics.errors` in tests
- [ ] Cedar schema in place; a deliberate typo fails at startup
- [ ] 3 policies, each with a passing test
- [ ] `test_close_case_denied_with_empty_enricher` passes
- [ ] `test_close_case_denied_when_principal_unresolvable` passes
- [ ] `test_no_policy_evaluation_errors` passes
- [ ] `pytest backend/tests/` green

If incomplete at 6 hours, ship what works and move on. Day 5 finishes it.
