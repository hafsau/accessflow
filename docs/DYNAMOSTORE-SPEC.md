# Spec — DynamoDB-backed Store

**Status:** ready to implement · **Written:** 2026-08-31 · **Blocks:** Sep 1–2, Sep 3–4, Sep 5–7 phases

## Why

`backend/app/models/store.py` is an in-memory dict behind a `Lock`, with a module-level
singleton. Its own docstring says *"Swap for DynamoDB in production."* That swap never
happened. Every AgentCore invocation constructs a fresh empty `Store()`, runs the case
entirely in RAM, returns a summary, and exits. Nothing survives.

Four consequences, all of which block scheduled work:

| Broken today | Depends on it |
|---|---|
| No case survives an invocation | Ops console (Sep 5–7), async demo |
| No prior state to compare against | Change detection → re-planning (Sep 1–2) |
| Nothing to resume | `S3SessionManager` after `kill -9` (Sep 3–4) |
| `_idempotency_keys` is a `set` in RAM | Re-invoking a meeting duplicates every action |

The last one is a correctness bug, not just a missing feature.

## Non-negotiable constraints

**Do not modify these files.** They are verified against live APIs and against Cedar tests:

- `backend/app/tools/legistar.py`
- `backend/app/agents/authority.py`
- `backend/app/agents/orchestrator.py`
- `backend/app/tools/case_tools.py`
- `policies/accessflow.cedar`, `backend/app/agents/tool_permits.cedar`
- `backend/app/models/domain.py`

**Do not change any method signature on `Store`.** Callers stay untouched. This is the
entire point of the design below.

**Do not delete the in-memory `Store`.** Tests depend on it.

## Design

One table, `accessflow-core`, PK/SK. The access patterns are all case-scoped, so a single
table answers "everything about case X" in one `Query` instead of four round trips.

| Attribute | Type | Notes |
|---|---|---|
| `PK` | S | partition key |
| `SK` | S | sort key |
| `GSI1PK` / `GSI1SK` | S | listing index (dashboard, decision queue) |
| `data` | S | `model_dump_json()` of the Pydantic object |
| `entity` | S | `CASE` \| `EVENT` \| `PROVIDER` \| `REQUEST` \| `DECISION` \| `VERIFICATION` \| `ACTION` \| `IDEM` |

Billing mode **PAY_PER_REQUEST**. No TTL — records must survive through Oct 8 judging.

### Key map

| Entity | PK | SK | GSI1PK | GSI1SK |
|---|---|---|---|---|
| Case | `CASE#<case_id>` | `META` | `CASE` | `<created_at ISO>` |
| Request | `CASE#<case_id>` | `REQUEST#<request_id>` | — | — |
| Decision | `CASE#<case_id>` | `DECISION#<decision_id>` | `DECISION#<status>` | `<requested_at ISO>` |
| Verification | `CASE#<case_id>` | `VERIFICATION#<verification_id>` | — | — |
| Action | `CASE#<case_id>` | `ACTION#<ts>#<uuid8>` | — | — |
| Event | `EVENT#<event_key>` | `META` | — | — |
| Provider | `PROVIDER#<provider_id>` | `META` | `PROVIDER` | `<provider_id>` |
| Idempotency | `IDEM#<key>` | `META` | — | — |

Everything under one case shares a partition, so `count_requests_for_case`,
`has_declined_request` and `get_actions` become one `Query` with an `SK begins_with` filter.

Global secondary lookups by bare id (`get_request`, `get_decision`, `get_verification`)
receive only an id, not a case_id. Two acceptable options — **pick option A**:

- **A. Embed the case_id in the generated id** (`req_<case_id>_<uuid8>`), parse it back out
  on read. No extra index, no extra write. Ids are internal and opaque to callers.
- B. Add GSI2 keyed on the bare id. Costs an index; only take this if A breaks a Cedar test.

## Method contract

Implement `DynamoStore` in a **new** file `backend/app/models/dynamo_store.py`. Same 20
public methods, same signatures, same return types as `Store`. Behaviour notes where it
differs:

- `check_idempotency(key) -> bool` — `PutItem` on `IDEM#<key>` with
  `ConditionExpression="attribute_not_exists(PK)"`. Return `True` on success; catch
  `ConditionalCheckFailedException` and return `False`. **This is the fix for the
  duplicate-action bug** — it must be a conditional write, not a read-then-write.
- `is_duplicate(key) -> bool` — plain `GetItem`, no write.
- `increment_tool_calls(case_id) -> int` — `UpdateItem` with `ADD tool_calls :one` and
  `ReturnValues="UPDATED_NEW"`. Must be atomic; the Cedar turn limit depends on it.
- `update_case(case)` — set `updated_at` on write.
- `search_providers(service_type, jurisdiction)` — `Query` GSI1 on `GSI1PK = "PROVIDER"`,
  filter in Python. Provider count is small; do not build a filter index.
- `record_action(...)` — append-only, never overwrite.
- All reads return the same Pydantic objects via `Model.model_validate_json(item["data"])`.

Serialise with `model_dump_json()`, not `model_dump()` — it handles `datetime` and the
`str` Enums without a custom encoder, and avoids the DynamoDB `float`/`Decimal` trap on
`Provider.rating`.

## Wiring

`get_store()` picks the backend by environment, so no caller changes and tests keep using
the in-memory store:

```python
def get_store():
    global _store
    if _store is None:
        if os.getenv("CORE_TABLE"):
            from .dynamo_store import DynamoStore
            _store = DynamoStore(os.environ["CORE_TABLE"])
        else:
            _store = Store()
    return _store
```

`reset_store()` keeps working unchanged.

## Providers

`_seed_providers()` currently hardcodes fixtures in `__init__`. Move them to
`scripts/seed_providers.py`, which writes the same rows into the table once.

**This does not make them real providers.** They remain a seeded directory, and the README
must say so plainly. Do not let the demo or the Devpost description imply the agent is
contacting real accessibility vendors.

## Infrastructure

Table (one command, no CDK changes — keeps the deploy stack untouched):

```bash
aws dynamodb create-table --region us-west-2 \
  --table-name accessflow-core \
  --attribute-definitions \
      AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S \
      AttributeName=GSI1PK,AttributeType=S AttributeName=GSI1SK,AttributeType=S \
  --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --global-secondary-indexes \
    'IndexName=GSI1,KeySchema=[{AttributeName=GSI1PK,KeyType=HASH},{AttributeName=GSI1SK,KeyType=RANGE}],Projection={ProjectionType=ALL}'
```

Then:

1. Add `CORE_TABLE=accessflow-core` to the AgentCore runtime environment.
2. Copy `dynamo_store.py` into `runtime/backend/app/models/`.
3. Confirm `boto3` is in `runtime/pyproject.toml`.
4. Grant the runtime execution role `dynamodb:GetItem`, `PutItem`, `UpdateItem`, `Query` on
   `accessflow-core` and `accessflow-core/index/GSI1`.

## Verification — run all five, paste raw output

Not "it works." These are falsifiable.

1. **Tests still green with in-memory store** (`CORE_TABLE` unset):
   `pytest -q` — same pass count as before the change.
2. **Round-trip**: `CORE_TABLE=accessflow-core pytest -q` — same pass count.
3. **Case survives the process**: invoke the runtime once, then from a *separate* shell
   `aws dynamodb query --table-name accessflow-core --region us-west-2 \
   --key-condition-expression "PK = :p" --expression-attribute-values '{":p":{"S":"CASE#<id>"}}'`
   — must return the case plus its actions.
4. **Idempotency holds across invocations**: invoke the *same* meeting twice. Action count
   for that case must not double. This is the bug being fixed; prove it.
5. **Turn counter is atomic**: `increment_tool_calls` called 10× concurrently returns 10
   distinct values ending at 10.

## Out of scope

Not in this change — do not start them: `S3SessionManager`, the ops console, the async
queue, latency work on the 241s invoke, the `budget.py` / `poller/persistence.py` fork.
