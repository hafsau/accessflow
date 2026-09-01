"""
AccessFlow data store — in-memory implementation.

Swap for DynamoDB in production. This layer handles:
- Case/Event/Provider persistence
- Idempotency key deduplication
- AgentAction audit trail
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any

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


class Store:
    """Thread-safe in-memory store with idempotency and audit."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._cases: dict[str, Case] = {}
        self._events: dict[str, Event] = {}
        self._providers: dict[str, Provider] = {}
        self._requests: dict[str, ProviderRequest] = {}
        self._decisions: dict[str, Decision] = {}
        self._verifications: dict[str, Verification] = {}
        self._idempotency_keys: set[str] = set()
        self._actions: list[AgentAction] = []

        # Seed some providers for testing
        self._seed_providers()

    def _seed_providers(self) -> None:
        """Seed the provider directory with test data."""
        providers = [
            Provider(
                provider_id="prov_pacific",
                name="Pacific Interpreting",
                service_types=["ASL_INTERPRETER", "CART"],
                jurisdictions=["seattle", "kingcounty"],
                approved=True,
                rating=4.8,
            ),
            Provider(
                provider_id="prov_signon",
                name="SignOn Services",
                service_types=["ASL_INTERPRETER"],
                jurisdictions=["seattle", "oakland", "sanjose"],
                approved=True,
                rating=4.2,
            ),
            Provider(
                provider_id="prov_linguava",
                name="Linguava Interpreters",
                service_types=["SPANISH_INTERPRETER", "OTHER_LANGUAGE"],
                jurisdictions=["seattle", "oakland", "alameda", "sanjose"],
                approved=True,
                rating=4.5,
            ),
            Provider(
                provider_id="prov_unapproved",
                name="QuickSign LLC",
                service_types=["ASL_INTERPRETER"],
                jurisdictions=["seattle"],
                approved=False,
                rating=3.0,
            ),
            Provider(
                provider_id="prov_accesstech",
                name="AccessTech Solutions",
                service_types=["ASSISTIVE_LISTENING", "LARGE_PRINT", "REMOTE_ACCESS"],
                jurisdictions=["seattle", "kingcounty", "oakland"],
                approved=True,
                rating=4.6,
            ),
            Provider(
                provider_id="prov_captionpro",
                name="Caption Pro Services",
                service_types=["CART", "REMOTE_ACCESS"],
                jurisdictions=["seattle", "sanjose"],
                approved=True,
                rating=4.4,
            ),
        ]
        for p in providers:
            self._providers[p.provider_id] = p

    # --- Idempotency ---

    def check_idempotency(self, key: str) -> bool:
        """Returns True if key is new, False if duplicate."""
        with self._lock:
            if key in self._idempotency_keys:
                return False
            self._idempotency_keys.add(key)
            return True

    def is_duplicate(self, key: str) -> bool:
        """Check without consuming the key."""
        with self._lock:
            return key in self._idempotency_keys

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
        action = AgentAction(
            action_id=f"act_{_uuid()}",
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            case_id=case_id,
            input_hash=_hash(input_data),
            output_hash=_hash(output_data),
            success=success,
            error_code=error_code,
            created_at=_now(),
        )
        with self._lock:
            self._actions.append(action)
        return action

    def get_actions(self) -> list[AgentAction]:
        with self._lock:
            return list(self._actions)

    # --- Cases ---

    def get_case(self, case_id: str) -> Case | None:
        with self._lock:
            return self._cases.get(case_id)

    def create_case(self, event_id: str, obligations: list[Obligation]) -> Case:
        case = Case(
            case_id=f"case_{_uuid()}",
            event_id=event_id,
            state=CaseState.NEW,
            obligations=obligations,
            provider_requests=[],
            verification_id=None,
            verification_passed=False,
            created_at=_now(),
            updated_at=_now(),
        )
        with self._lock:
            self._cases[case.case_id] = case
        return case

    def update_case(self, case: Case) -> Case:
        case.updated_at = _now()
        with self._lock:
            self._cases[case.case_id] = case
        return case

    def increment_tool_calls(self, case_id: str) -> int:
        """Increment and return the tool_calls counter for a case.

        Used by the Cedar turns limit (braces) to track how many
        tool calls have been made for this case.
        """
        with self._lock:
            case = self._cases.get(case_id)
            if case is None:
                return 0
            case.tool_calls += 1
            case.updated_at = _now()
            return case.tool_calls

    # --- Events ---

    def get_event(self, event_key: str) -> Event | None:
        with self._lock:
            return self._events.get(event_key)

    def upsert_event(self, event: Event) -> Event:
        with self._lock:
            self._events[event.event_key] = event
        return event

    # --- Providers ---

    def get_provider(self, provider_id: str) -> Provider | None:
        with self._lock:
            return self._providers.get(provider_id)

    def search_providers(
        self,
        service_type: str | None = None,
        jurisdiction: str | None = None,
    ) -> list[Provider]:
        with self._lock:
            results = list(self._providers.values())

        if service_type:
            results = [p for p in results if service_type in p.service_types]
        if jurisdiction:
            results = [p for p in results if jurisdiction in p.jurisdictions]

        return results

    # --- Provider Requests ---

    def get_request(self, request_id: str) -> ProviderRequest | None:
        with self._lock:
            return self._requests.get(request_id)

    def create_request(self, case_id: str, provider_id: str) -> ProviderRequest:
        req = ProviderRequest(
            request_id=f"req_{_uuid()}",
            case_id=case_id,
            provider_id=provider_id,
            status=RequestStatus.SENT,
            sent_at=_now(),
        )
        with self._lock:
            self._requests[req.request_id] = req
        return req

    def count_requests_for_case(self, case_id: str) -> int:
        """Count provider requests for a case (for Cedar sequential limit)."""
        with self._lock:
            return sum(1 for r in self._requests.values() if r.case_id == case_id)

    def has_declined_request(self, case_id: str) -> bool:
        """Check if any provider request for this case was declined.

        Used by Cedar to allow a second provider request only after decline.
        """
        with self._lock:
            return any(
                r.case_id == case_id and r.status == RequestStatus.DECLINED
                for r in self._requests.values()
            )

    # --- Decisions ---

    def get_decision(self, decision_id: str) -> Decision | None:
        with self._lock:
            return self._decisions.get(decision_id)

    def create_decision(
        self,
        case_id: str,
        decision_type: str,
        options: list,
    ) -> Decision:
        dec = Decision(
            decision_id=f"dec_{_uuid()}",
            case_id=case_id,
            decision_type=decision_type,
            status=DecisionStatus.PENDING,
            options=options,
            requested_at=_now(),
        )
        with self._lock:
            self._decisions[dec.decision_id] = dec
        return dec

    # --- Verifications ---

    def get_verification(self, verification_id: str) -> Verification | None:
        with self._lock:
            return self._verifications.get(verification_id)

    def create_verification(
        self,
        case_id: str,
        passed: bool,
        obligations: list,
    ) -> Verification:
        ver = Verification(
            verification_id=f"ver_{_uuid()}",
            case_id=case_id,
            passed=passed,
            checked_at=_now(),
            obligations=obligations,
        )
        with self._lock:
            self._verifications[ver.verification_id] = ver
        return ver


# Global store instance — picks DynamoDB when CORE_TABLE is set
_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        if os.getenv("CORE_TABLE"):
            from .dynamo_store import DynamoStore
            _store = DynamoStore(os.environ["CORE_TABLE"])
        else:
            _store = Store()
    return _store


def reset_store() -> None:
    """For testing. Clears the singleton so the next get_store() re-reads config.

    Must NOT construct Store() directly — that bypasses the CORE_TABLE switch and
    silently pins every caller to the in-memory backend regardless of environment.
    """
    global _store
    _store = None
