"""
AccessFlow domain models — Pydantic types for all tool inputs and outputs.

These models are the contract between tools and the agent. They enforce
structured JSON, never prose, and provide typed error codes.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CaseState(str, Enum):
    NEW = "NEW"
    COORDINATING = "COORDINATING"
    AWAITING_DECISION = "AWAITING_DECISION"
    AWAITING_PROVIDER = "AWAITING_PROVIDER"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ServiceType(str, Enum):
    ASL_INTERPRETER = "ASL_INTERPRETER"
    CART = "CART"
    SPANISH_INTERPRETER = "SPANISH_INTERPRETER"
    OTHER_LANGUAGE = "OTHER_LANGUAGE"
    ASSISTIVE_LISTENING = "ASSISTIVE_LISTENING"
    LARGE_PRINT = "LARGE_PRINT"
    BRAILLE = "BRAILLE"
    EXTENDED_TIME = "EXTENDED_TIME"
    REMOTE_ACCESS = "REMOTE_ACCESS"


class RequestStatus(str, Enum):
    SENT = "SENT"
    CONFIRMED = "CONFIRMED"
    DECLINED = "DECLINED"
    NO_RESPONSE = "NO_RESPONSE"


class DecisionStatus(str, Enum):
    PENDING = "PENDING"
    DECIDED = "DECIDED"
    EXPIRED = "EXPIRED"


class DecisionType(str, Enum):
    """Valid decision types for request_human_decision.

    The orchestrator may only escalate with one of these types.
    Model-invented types will be rejected.
    """
    PROVIDER_SHORTAGE = "PROVIDER_SHORTAGE"          # No providers serve jurisdiction
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"    # All providers declined/failed
    PROVIDER_SUBSTITUTION = "PROVIDER_SUBSTITUTION"  # Need to substitute a provider
    TOOL_ERROR = "TOOL_ERROR"                        # A tool call failed
    POLICY_BLOCKED = "POLICY_BLOCKED"                # Cedar policy denied action
    TURN_LIMIT_EXCEEDED = "TURN_LIMIT_EXCEEDED"      # Agent hit turn limit
    OTHER = "OTHER"                                  # Edge cases (must include context)


class Priority(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ErrorCode(str, Enum):
    CASE_NOT_FOUND = "CASE_NOT_FOUND"
    EVENT_NOT_FOUND = "EVENT_NOT_FOUND"
    PROVIDER_NOT_FOUND = "PROVIDER_NOT_FOUND"
    PROVIDER_NOT_APPROVED = "PROVIDER_NOT_APPROVED"
    VERIFICATION_NOT_FOUND = "VERIFICATION_NOT_FOUND"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    INVALID_STATE = "INVALID_STATE"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    FETCH_FAILED = "FETCH_FAILED"
    PARSE_FAILED = "PARSE_FAILED"
    VALIDATION_ERROR = "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Domain Objects
# ---------------------------------------------------------------------------

class Obligation(BaseModel):
    category: str
    basis: str
    deadline: str
    fulfilled: bool = False
    evidence: str | None = None


class Case(BaseModel):
    case_id: str
    event_id: str
    state: CaseState
    obligations: list[Obligation] = Field(default_factory=list)
    provider_requests: list[str] = Field(default_factory=list)
    verification_id: str | None = None
    verification_passed: bool = False
    tool_calls: int = 0  # Counter for Cedar turns limit (braces)
    created_at: datetime
    updated_at: datetime


class Event(BaseModel):
    event_key: str
    client: str
    event_id: int
    body_name: str
    date: str
    time: str | None = None
    location: str | None = None
    agenda_url: str | None = None
    comment: str | None = None
    is_cancelled: bool = False
    last_modified_utc: str | None = None
    content_fingerprint: str | None = None


class Document(BaseModel):
    url: str
    page_count: int
    text_preview: str
    fetched_at: datetime
    content_hash: str


class Provider(BaseModel):
    provider_id: str
    name: str
    service_types: list[str]
    jurisdictions: list[str]
    approved: bool
    rating: float | None = None


class ProviderRequest(BaseModel):
    request_id: str
    case_id: str
    provider_id: str
    status: RequestStatus
    sent_at: datetime
    confirmed_at: datetime | None = None


class DecisionOption(BaseModel):
    option_id: str
    description: str
    recommended: bool = False


class Decision(BaseModel):
    decision_id: str
    case_id: str
    decision_type: str
    status: DecisionStatus
    options: list[DecisionOption] = Field(default_factory=list)
    chosen_option: str | None = None
    requested_at: datetime
    decided_at: datetime | None = None


class ObligationCheck(BaseModel):
    category: str
    fulfilled: bool
    evidence: str | None = None


class Verification(BaseModel):
    verification_id: str
    case_id: str
    passed: bool
    checked_at: datetime
    obligations: list[ObligationCheck] = Field(default_factory=list)


class AccommodationPolicy(BaseModel):
    recommended_accommodations: list[str]
    priority: str
    reasoning: str
    quote: str | None = None
    quote_verified: bool | None = None  # ALWAYS None from tool


# ---------------------------------------------------------------------------
# Tool Responses — always {ok: true, ...} or {ok: false, error_code, message}
# ---------------------------------------------------------------------------

class ToolError(BaseModel):
    ok: bool = False
    error_code: ErrorCode
    message: str


class GetCaseResponse(BaseModel):
    ok: bool = True
    case: Case


class GetEventResponse(BaseModel):
    ok: bool = True
    event: Event


class FetchDocumentResponse(BaseModel):
    ok: bool = True
    document: Document


class SearchProvidersResponse(BaseModel):
    ok: bool = True
    providers: list[Provider]


class SendProviderRequestResponse(BaseModel):
    ok: bool = True
    request: ProviderRequest


class RequestDecisionResponse(BaseModel):
    ok: bool = True
    decision: Decision


class VerifyFulfillmentResponse(BaseModel):
    ok: bool = True
    verification: Verification


class CloseCaseResponse(BaseModel):
    ok: bool = True
    case: Case


class ExtractPolicyResponse(BaseModel):
    ok: bool = True
    policy: AccommodationPolicy


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class AgentAction(BaseModel):
    action_id: str
    tool_name: str
    idempotency_key: str | None = None
    case_id: str | None = None
    input_hash: str
    output_hash: str
    success: bool
    error_code: str | None = None
    created_at: datetime
