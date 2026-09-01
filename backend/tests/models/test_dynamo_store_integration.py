"""Integration tests for DynamoStore against a real DynamoDB table.

Skipped unless CORE_TABLE is set, so the normal suite stays fast and offline:

    CORE_TABLE=accessflow-core .venv/bin/pytest backend/tests/models -q

Every record these tests create is namespaced under a per-run event id and torn
down afterwards, so they never collide with real cases.
"""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.models.domain import CaseState, Obligation

CORE_TABLE = os.getenv("CORE_TABLE")

pytestmark = pytest.mark.skipif(
    not CORE_TABLE,
    reason="CORE_TABLE not set — DynamoDB integration tests skipped",
)


def _obligations() -> list[Obligation]:
    return [
        Obligation(
            category="effective_communication",
            basis="28 CFR 35.160",
            deadline="2026-09-06T14:00:00+00:00",
        ),
        Obligation(
            category="document_conformance",
            basis="28 CFR 35.200",
            deadline="2027-04-26",
        ),
    ]


@pytest.fixture
def store():
    from backend.app.models.dynamo_store import DynamoStore

    return DynamoStore(CORE_TABLE)


@pytest.fixture
def event_id() -> str:
    return f"pytest:{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
def _cleanup(store, event_id):
    """Delete every item this test created, whatever the outcome."""
    created: list[str] = []
    yield created
    for case_id in created:
        resp = store._table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"CASE#{case_id}"},
        )
        with store._table.batch_writer() as batch:
            for item in resp.get("Items", []):
                batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})


def test_case_round_trips_through_dynamodb(store, event_id, _cleanup):
    case = store.create_case(event_id=event_id, obligations=_obligations())
    _cleanup.append(case.case_id)

    fetched = store.get_case(case.case_id)

    assert fetched is not None
    assert fetched.case_id == case.case_id
    assert fetched.event_id == event_id
    assert fetched.state == CaseState.NEW
    assert [o.basis for o in fetched.obligations] == ["28 CFR 35.160", "28 CFR 35.200"]


def test_case_is_visible_to_a_fresh_store_instance(store, event_id, _cleanup):
    """The whole point of the change: state outlives the object that wrote it."""
    from backend.app.models.dynamo_store import DynamoStore

    case = store.create_case(event_id=event_id, obligations=_obligations())
    _cleanup.append(case.case_id)

    other = DynamoStore(CORE_TABLE)
    fetched = other.get_case(case.case_id)

    assert fetched is not None
    assert fetched.case_id == case.case_id


def test_idempotency_key_is_accepted_once(store):
    key = f"pytest_idem_{uuid.uuid4().hex}"

    assert store.check_idempotency(key) is True, "first use should be accepted"
    assert store.check_idempotency(key) is False, "replay must be rejected"
    assert store.is_duplicate(key) is True

    store._table.delete_item(Key={"PK": f"IDEM#{key}", "SK": "META"})


def test_increment_tool_calls_is_atomic_under_concurrency(store, event_id, _cleanup):
    case = store.create_case(event_id=event_id, obligations=_obligations())
    _cleanup.append(case.case_id)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: store.increment_tool_calls(case.case_id), range(10)))

    assert sorted(results) == list(range(1, 11)), (
        f"expected each caller to see a distinct value 1..10, got {sorted(results)}"
    )
    assert store.get_case(case.case_id).tool_calls == 10


def test_update_case_does_not_clobber_the_turn_counter(store, event_id, _cleanup):
    """Regression guard.

    update_case() once wrote tool_calls from the Case object. A caller holding a
    stale Case could roll the counter backwards and defeat the Cedar turn limit.
    increment_tool_calls() is now the only writer of that attribute.
    """
    case = store.create_case(event_id=event_id, obligations=_obligations())
    _cleanup.append(case.case_id)

    stale = store.get_case(case.case_id)      # tool_calls == 0
    assert stale.tool_calls == 0

    for _ in range(3):
        store.increment_tool_calls(case.case_id)

    stale.state = CaseState.COORDINATING
    store.update_case(stale)                   # must not write tool_calls

    after = store.get_case(case.case_id)
    assert after.state == CaseState.COORDINATING, "the state change must persist"
    assert after.tool_calls == 3, f"counter was clobbered — expected 3, got {after.tool_calls}"


def test_requests_are_queryable_by_case(store, event_id, _cleanup):
    case = store.create_case(event_id=event_id, obligations=_obligations())
    _cleanup.append(case.case_id)

    store.create_request(case_id=case.case_id, provider_id="prov_001")
    store.create_request(case_id=case.case_id, provider_id="prov_002")

    assert store.count_requests_for_case(case.case_id) == 2
    assert store.has_declined_request(case.case_id) is False
