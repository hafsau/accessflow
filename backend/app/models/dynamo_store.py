"""
AccessFlow data store — DynamoDB implementation.

Same interface as Store (in-memory), backed by a single-table DynamoDB design.
See docs/DYNAMOSTORE-SPEC.md for the full design.

Key schema:
- Case: PK=CASE#<case_id>, SK=META
- Request: PK=CASE#<case_id>, SK=REQUEST#<request_id>
- Decision: PK=CASE#<case_id>, SK=DECISION#<decision_id>
- Verification: PK=CASE#<case_id>, SK=VERIFICATION#<verification_id>
- Action: PK=CASE#<case_id>, SK=ACTION#<ts>#<uuid8>
- Event: PK=EVENT#<event_key>, SK=META
- Provider: PK=PROVIDER#<provider_id>, SK=META
- Idempotency: PK=IDEM#<key>, SK=META

IDs embed the case_id for reverse lookup without GSI:
  req_<case_id>_<uuid8>, dec_<case_id>_<uuid8>, ver_<case_id>_<uuid8>
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .domain import (
    AgentAction,
    Case,
    CaseState,
    Decision,
    DecisionStatus,
    Event,
    Obligation,
    Provider,
    ProviderRequest,
    RequestStatus,
    Verification,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(data: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(data, default=str).encode()).hexdigest()[:16]


def _uuid() -> str:
    return str(uuid.uuid4())[:8]


def _extract_case_id(compound_id: str) -> str:
    """Extract case_id from compound ID like req_<case_id>_<uuid8>."""
    parts = compound_id.split("_")
    if len(parts) >= 3:
        # req_case_abc123_xyz -> case_abc123
        return "_".join(parts[1:-1])
    return ""


class DynamoStore:
    """DynamoDB-backed store with idempotency and audit."""

    def __init__(self, table_name: str) -> None:
        self._table_name = table_name
        self._dynamodb = boto3.resource("dynamodb")
        self._table = self._dynamodb.Table(table_name)

    # --- Idempotency ---

    def check_idempotency(self, key: str) -> bool:
        """Returns True if key is new, False if duplicate.

        Uses conditional write — atomic check-and-set.
        """
        try:
            self._table.put_item(
                Item={
                    "PK": f"IDEM#{key}",
                    "SK": "META",
                    "entity": "IDEM",
                    "data": json.dumps({"key": key, "created_at": _now().isoformat()}),
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def is_duplicate(self, key: str) -> bool:
        """Check without consuming the key."""
        response = self._table.get_item(
            Key={"PK": f"IDEM#{key}", "SK": "META"},
            ProjectionExpression="PK",
        )
        return "Item" in response

    # --- Audit ---

    def record_action(
        self,
        tool_name: str,
        input_data: Any,
        output_data: Any,
        success: bool,
        error_code: str | None = None,
        idempotency_key: str | None = None,
        case_id: str | None = None,
    ) -> AgentAction:
        now = _now()
        action = AgentAction(
            action_id=f"act_{_uuid()}",
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            case_id=case_id,
            input_hash=_hash(input_data),
            output_hash=_hash(output_data),
            success=success,
            error_code=error_code,
            created_at=now,
        )

        # Use case partition if available, else a global actions partition
        pk = f"CASE#{case_id}" if case_id else "ACTIONS#GLOBAL"
        sk = f"ACTION#{now.isoformat()}#{_uuid()}"

        self._table.put_item(
            Item={
                "PK": pk,
                "SK": sk,
                "entity": "ACTION",
                "data": action.model_dump_json(),
            }
        )
        return action

    def get_actions(self) -> list[AgentAction]:
        """Get all actions. For DynamoDB this queries global actions partition."""
        # Query global actions partition
        actions: list[AgentAction] = []

        response = self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": "ACTIONS#GLOBAL",
                ":sk": "ACTION#",
            },
        )
        for item in response.get("Items", []):
            actions.append(AgentAction.model_validate_json(item["data"]))

        # Also need to get actions under case partitions - scan with filter
        # This is acceptable for audit logs which are infrequently accessed
        scan_response = self._table.scan(
            FilterExpression="entity = :entity AND begins_with(PK, :pk)",
            ExpressionAttributeValues={
                ":entity": "ACTION",
                ":pk": "CASE#",
            },
        )
        for item in scan_response.get("Items", []):
            actions.append(AgentAction.model_validate_json(item["data"]))

        return sorted(actions, key=lambda a: a.created_at)

    # --- Cases ---

    def get_case(self, case_id: str) -> Case | None:
        response = self._table.get_item(
            Key={"PK": f"CASE#{case_id}", "SK": "META"},
        )
        item = response.get("Item")
        if item:
            case = Case.model_validate_json(item["data"])
            # Use top-level tool_calls if present (from atomic updates)
            if "tool_calls" in item:
                case.tool_calls = int(item["tool_calls"])
            return case
        return None

    def create_case(self, event_id: str, obligations: list[Obligation]) -> Case:
        now = _now()
        case = Case(
            case_id=f"case_{_uuid()}",
            event_id=event_id,
            state=CaseState.NEW,
            obligations=obligations,
            provider_requests=[],
            verification_id=None,
            verification_passed=False,
            created_at=now,
            updated_at=now,
        )
        self._table.put_item(
            Item={
                "PK": f"CASE#{case.case_id}",
                "SK": "META",
                "entity": "CASE",
                "data": case.model_dump_json(),
                "GSI1PK": "CASE",
                "GSI1SK": now.isoformat(),
                "tool_calls": 0,
            }
        )
        return case

    def update_case(self, case: Case) -> Case:
        """Update a case WITHOUT touching the top-level tool_calls attribute.

        Uses update_item, not put_item: put_item replaces the whole item, which
        would delete tool_calls and let the atomic counter restart from zero.
        increment_tool_calls() is the only writer of that attribute.
        "data" is a DynamoDB reserved word, hence the name placeholder.
        """
        case.updated_at = _now()
        self._table.update_item(
            Key={"PK": f"CASE#{case.case_id}", "SK": "META"},
            UpdateExpression=(
                "SET #data = :data, entity = :entity, "
                "GSI1PK = :g1, GSI1SK = :g2, updated_at = :now"
            ),
            ExpressionAttributeNames={"#data": "data"},
            ExpressionAttributeValues={
                ":data": case.model_dump_json(),
                ":entity": "CASE",
                ":g1": "CASE",
                ":g2": case.created_at.isoformat(),
                ":now": case.updated_at.isoformat(),
            },
        )
        return case

    def increment_tool_calls(self, case_id: str) -> int:
        """Increment and return the tool_calls counter for a case.

        Uses atomic UpdateItem with ADD. Must be atomic for Cedar turns limit.
        """
        try:
            response = self._table.update_item(
                Key={"PK": f"CASE#{case_id}", "SK": "META"},
                UpdateExpression="ADD tool_calls :one SET updated_at = :now",
                ExpressionAttributeValues={
                    ":one": 1,
                    ":now": _now().isoformat(),
                },
                ReturnValues="UPDATED_NEW",
                ConditionExpression="attribute_exists(PK)",
            )
            return int(response["Attributes"].get("tool_calls", 0))
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return 0
            raise

    # --- Events ---

    def get_event(self, event_key: str) -> Event | None:
        response = self._table.get_item(
            Key={"PK": f"EVENT#{event_key}", "SK": "META"},
        )
        item = response.get("Item")
        if item:
            return Event.model_validate_json(item["data"])
        return None

    def upsert_event(self, event: Event) -> Event:
        self._table.put_item(
            Item={
                "PK": f"EVENT#{event.event_key}",
                "SK": "META",
                "entity": "EVENT",
                "data": event.model_dump_json(),
            }
        )
        return event

    # --- Providers ---

    def get_provider(self, provider_id: str) -> Provider | None:
        response = self._table.get_item(
            Key={"PK": f"PROVIDER#{provider_id}", "SK": "META"},
        )
        item = response.get("Item")
        if item:
            return Provider.model_validate_json(item["data"])
        return None

    def search_providers(
        self,
        service_type: str | None = None,
        jurisdiction: str | None = None,
    ) -> list[Provider]:
        """Query GSI1 for all providers, filter in Python."""
        response = self._table.query(
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk",
            ExpressionAttributeValues={":pk": "PROVIDER"},
        )

        results: list[Provider] = []
        for item in response.get("Items", []):
            p = Provider.model_validate_json(item["data"])
            results.append(p)

        if service_type:
            results = [p for p in results if service_type in p.service_types]
        if jurisdiction:
            results = [p for p in results if jurisdiction in p.jurisdictions]

        return results

    # --- Provider Requests ---

    def get_request(self, request_id: str) -> ProviderRequest | None:
        # Extract case_id from compound ID: req_<case_id>_<uuid8>
        case_id = _extract_case_id(request_id)
        if not case_id:
            return None

        response = self._table.get_item(
            Key={"PK": f"CASE#{case_id}", "SK": f"REQUEST#{request_id}"},
        )
        item = response.get("Item")
        if item:
            return ProviderRequest.model_validate_json(item["data"])
        return None

    def create_request(self, case_id: str, provider_id: str) -> ProviderRequest:
        # Embed case_id in request_id for reverse lookup
        req = ProviderRequest(
            request_id=f"req_{case_id}_{_uuid()}",
            case_id=case_id,
            provider_id=provider_id,
            status=RequestStatus.SENT,
            sent_at=_now(),
        )
        self._table.put_item(
            Item={
                "PK": f"CASE#{case_id}",
                "SK": f"REQUEST#{req.request_id}",
                "entity": "REQUEST",
                "data": req.model_dump_json(),
            }
        )
        return req

    def count_requests_for_case(self, case_id: str) -> int:
        """Count provider requests for a case (for Cedar sequential limit)."""
        response = self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"CASE#{case_id}",
                ":sk": "REQUEST#",
            },
            Select="COUNT",
        )
        return response.get("Count", 0)

    def has_declined_request(self, case_id: str) -> bool:
        """Check if any provider request for this case was declined."""
        response = self._table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"CASE#{case_id}",
                ":sk": "REQUEST#",
            },
        )
        for item in response.get("Items", []):
            req = ProviderRequest.model_validate_json(item["data"])
            if req.status == RequestStatus.DECLINED:
                return True
        return False

    # --- Decisions ---

    def get_decision(self, decision_id: str) -> Decision | None:
        # Extract case_id from compound ID: dec_<case_id>_<uuid8>
        case_id = _extract_case_id(decision_id)
        if not case_id:
            return None

        response = self._table.get_item(
            Key={"PK": f"CASE#{case_id}", "SK": f"DECISION#{decision_id}"},
        )
        item = response.get("Item")
        if item:
            return Decision.model_validate_json(item["data"])
        return None

    def create_decision(
        self,
        case_id: str,
        decision_type: str,
        options: list,
    ) -> Decision:
        now = _now()
        # Embed case_id in decision_id for reverse lookup
        dec = Decision(
            decision_id=f"dec_{case_id}_{_uuid()}",
            case_id=case_id,
            decision_type=decision_type,
            status=DecisionStatus.PENDING,
            options=options,
            requested_at=now,
        )
        self._table.put_item(
            Item={
                "PK": f"CASE#{case_id}",
                "SK": f"DECISION#{dec.decision_id}",
                "entity": "DECISION",
                "data": dec.model_dump_json(),
                "GSI1PK": f"DECISION#{dec.status.value}",
                "GSI1SK": now.isoformat(),
            }
        )
        return dec

    # --- Verifications ---

    def get_verification(self, verification_id: str) -> Verification | None:
        # Extract case_id from compound ID: ver_<case_id>_<uuid8>
        case_id = _extract_case_id(verification_id)
        if not case_id:
            return None

        response = self._table.get_item(
            Key={"PK": f"CASE#{case_id}", "SK": f"VERIFICATION#{verification_id}"},
        )
        item = response.get("Item")
        if item:
            return Verification.model_validate_json(item["data"])
        return None

    def create_verification(
        self,
        case_id: str,
        passed: bool,
        obligations: list,
    ) -> Verification:
        # Embed case_id in verification_id for reverse lookup
        ver = Verification(
            verification_id=f"ver_{case_id}_{_uuid()}",
            case_id=case_id,
            passed=passed,
            checked_at=_now(),
            obligations=obligations,
        )
        self._table.put_item(
            Item={
                "PK": f"CASE#{case_id}",
                "SK": f"VERIFICATION#{ver.verification_id}",
                "entity": "VERIFICATION",
                "data": ver.model_dump_json(),
            }
        )
        return ver
