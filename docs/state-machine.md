# Case state machine — Day 3 spec

**Ten states. Every transition explicit. Zero model calls.**

State lives in a persisted record, never in model prose. That is what makes `close_case` provable rather than promised — Cedar reads `verification_passed` from this record via `context_enricher`, and the model can never write it.

## States

| State | Meaning | Allowed next |
|---|---|---|
| `NEW` | Meeting seen in the feed; nothing derived yet | `ANALYZING` |
| `ANALYZING` | Deriving obligations, fetching the agenda | `PLANNING` · `ASK` · `BLOCKED` |
| `PLANNING` | Building the action plan from obligations | `COORDINATING` · `ASK` · `BLOCKED` |
| `COORDINATING` | Contacting providers, extracting the published policy | `WAITING` · `VERIFYING` · `ASK` · `BLOCKED` |
| `WAITING` | External response pending | `COORDINATING` · `ASK` · `CANCELLED` |
| `VERIFYING` | Checking evidence of fulfilment | `CLOSED` · `COORDINATING` · `ASK` · `BLOCKED` |
| `ASK` | Human decision required | `COORDINATING` · `BLOCKED` · `CLOSED`¹ |
| `BLOCKED` | Cannot proceed — no authority, no evidence, no qualified resource | `ASK` · `COORDINATING`² |
| `CLOSED` | Verified fulfilment | `REOPENED` |
| `CANCELLED` | The meeting itself was cancelled in the feed | `REOPENED` |
| `REOPENED` | A later change reopened a settled case | `ANALYZING` |

¹ `ASK → CLOSED` only after an explicit human decision **and** a passing verification. Both, never one.
² `BLOCKED → COORDINATING` only after the blocking condition is recorded as resolved, with the resolving fact stored.

## The transition function

```python
# backend/app/domain/state.py
ALLOWED: dict[str, set[str]] = {
    "NEW":          {"ANALYZING"},
    "ANALYZING":    {"PLANNING", "ASK", "BLOCKED"},
    "PLANNING":     {"COORDINATING", "ASK", "BLOCKED"},
    "COORDINATING": {"WAITING", "VERIFYING", "ASK", "BLOCKED"},
    "WAITING":      {"COORDINATING", "ASK", "CANCELLED"},
    "VERIFYING":    {"CLOSED", "COORDINATING", "ASK", "BLOCKED"},
    "ASK":          {"COORDINATING", "BLOCKED", "CLOSED"},
    "BLOCKED":      {"ASK", "COORDINATING"},
    "CLOSED":       {"REOPENED"},
    "CANCELLED":    {"REOPENED"},
    "REOPENED":     {"ANALYZING"},
}

def transition(case, to_state, reason, actor, evidence_id=None):
    """Raises InvalidTransition. Never silently corrects.
    Every call appends a CaseTransition row — that list IS the audit trail."""
```

**Invariants, each its own test:**
1. Any transition not in `ALLOWED` raises `InvalidTransition`. No silent correction, no "closest valid state".
2. `→ CLOSED` requires a non-null `evidence_id` **and** `case.verification_passed is True`. Enforced here as well as in Cedar — belt and braces, because this is the one rule whose failure makes the product a lie.
3. Every transition appends a `CaseTransition{from, to, reason, actor, at, evidence_id}`. The list is append-only; nothing is ever edited or removed.
4. `actor` ∈ `system` · `agent` · `human`. A `CLOSED` transition with `actor="agent"` and no human decision on an `ASK` path is a bug — test it.
5. Re-applying the same transition with the same idempotency key is a no-op, not a duplicate row.

## Feed changes → transitions (deterministic, no model)

| `change_type` from `poll_public_meetings` | Case is in | Goes to |
|---|---|---|
| `cancelled` | anything except `CLOSED` | `CANCELLED` |
| `cancelled` | `CLOSED` | `REOPENED` |
| `rescheduled_or_relocated` | `CLOSED` | `REOPENED` |
| `rescheduled_or_relocated` | anything else | `ANALYZING` |
| `agenda_posted` | `NEW` · `ANALYZING` | `ANALYZING` |
| `agenda_replaced` | `CLOSED` · `VERIFYING` | `REOPENED` — evidence is now stale |
| `agenda_replaced` | anything else | `COORDINATING` |

That last row is the one worth being careful about: **an agenda replaced after verification invalidates the evidence.** A case that silently stayed CLOSED there would be exactly the false completion the product exists to prevent.

## Day 3 exit criterion

`scripts/walk_one_case.py` takes one real meeting from the live feed and walks it `NEW → CLOSED`, printing the full audit trail. **No agent, no model call.** Every state change is deterministic and every row is real.

```
$ python scripts/walk_one_case.py
case_001  seattle:6860  "City Council" 2026-09-08

  NEW          -> ANALYZING     system  meeting ingested from feed
  ANALYZING    -> PLANNING      system  2 obligations derived (35.160, 35.200)
  PLANNING     -> COORDINATING  system  plan built: 2 requirements
  COORDINATING -> WAITING       system  provider request sent (prov_a)
  WAITING      -> COORDINATING  system  provider confirmed
  COORDINATING -> VERIFYING     system  evidence collected
  VERIFYING    -> CLOSED        system  verification ver_012 passed

  7 transitions · 0 model calls · verification_passed=True
```

## Checklist
- [ ] `backend/app/domain/state.py` with `ALLOWED` and `transition()`
- [ ] `InvalidTransition` raised on every illegal move
- [ ] `→ CLOSED` blocked without evidence_id + verification_passed
- [ ] Append-only `CaseTransition` audit rows
- [ ] Feed-change mapping implemented per the table, including `agenda_replaced` → `REOPENED`
- [ ] `scripts/walk_one_case.py` runs on a real meeting, zero model calls
- [ ] `pytest backend/tests/` green
