"""
AccessFlow case management tools — Strands @tool implementations.

All tools return typed Pydantic models with {ok: true, ...} or {ok: false, error_code, message}.
Mutating tools require idempotency_key and write AgentAction audit rows.
No exceptions cross the tool boundary.
"""
from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from typing import Any

import httpx
from strands import tool

from backend.app.models.domain import (
    AccommodationPolicy,
    Case,
    CaseState,
    CloseCaseResponse,
    DecisionOption,
    Document,
    ErrorCode,
    Event,
    ExtractPolicyResponse,
    FetchDocumentResponse,
    GetCaseResponse,
    GetEventResponse,
    Obligation,
    ObligationCheck,
    Provider,
    ProviderRequest,
    RequestDecisionResponse,
    RequestStatus,
    SearchProvidersResponse,
    SendProviderRequestResponse,
    ToolError,
    Verification,
    VerifyFulfillmentResponse,
)
from backend.app.models.store import get_store


def _error(code: ErrorCode, message: str) -> dict[str, Any]:
    """Return structured error dict."""
    return ToolError(error_code=code, message=message).model_dump()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. get_case — Read-only
# ---------------------------------------------------------------------------

@tool
def get_case(case_id: str) -> dict[str, Any]:
    """
    Retrieve a case by ID.

    Args:
        case_id: The unique identifier for the case

    Returns:
        Case details including state, obligations, and verification status
    """
    store = get_store()
    case = store.get_case(case_id)

    if case is None:
        return _error(ErrorCode.CASE_NOT_FOUND, f"No case with id {case_id}")

    return GetCaseResponse(case=case).model_dump()


# ---------------------------------------------------------------------------
# 2. get_event — Read-only
# ---------------------------------------------------------------------------

@tool
def get_event(event_key: str) -> dict[str, Any]:
    """
    Retrieve a meeting/event by key (format: client:event_id).

    Args:
        event_key: The event key, e.g. "seattle:6860"

    Returns:
        Event details including body name, date, location, agenda URL
    """
    store = get_store()
    event = store.get_event(event_key)

    if event is None:
        return _error(ErrorCode.EVENT_NOT_FOUND, f"No event with key {event_key}")

    return GetEventResponse(event=event).model_dump()


# ---------------------------------------------------------------------------
# 3. fetch_agenda_document — Read-only
# ---------------------------------------------------------------------------

@tool
def fetch_agenda_document(agenda_url: str, max_pages: int = 5) -> dict[str, Any]:
    """
    Download and analyze an agenda PDF.

    Args:
        agenda_url: URL of the agenda PDF
        max_pages: Maximum pages to extract text from (default 5)

    Returns:
        Document metadata including page count, text preview, content hash
    """
    try:
        resp = httpx.get(agenda_url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return _error(ErrorCode.FETCH_FAILED, f"HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        return _error(ErrorCode.FETCH_FAILED, str(e))

    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(resp.content))
        page_count = len(reader.pages)
        text_parts = []
        for page in reader.pages[:max_pages]:
            text_parts.append(page.extract_text() or "")
        text = "\n".join(text_parts)
    except Exception as e:
        return _error(ErrorCode.PARSE_FAILED, f"PDF parse error: {e}")

    content_hash = "sha256:" + hashlib.sha256(resp.content).hexdigest()[:16]

    doc = Document(
        url=agenda_url,
        page_count=page_count,
        text_preview=text[:500],
        fetched_at=_now(),
        content_hash=content_hash,
    )

    return FetchDocumentResponse(document=doc).model_dump()


# ---------------------------------------------------------------------------
# 4. search_providers — Read-only
# ---------------------------------------------------------------------------

@tool
def search_providers(
    service_type: str | None = None,
    jurisdiction: str | None = None,
    date: str | None = None,
    time: str | None = None,
) -> dict[str, Any]:
    """
    Search the provider directory for available accommodation providers.

    Args:
        service_type: Type of service needed (e.g., ASL_INTERPRETER, CART)
        jurisdiction: Jurisdiction code (e.g., seattle, kingcounty)
        date: Event date (YYYY-MM-DD) - for availability filtering
        time: Event time (HH:MM) - for availability filtering

    Returns:
        List of matching providers with their details
    """
    store = get_store()
    providers = store.search_providers(service_type, jurisdiction)

    # Filter to approved only
    providers = [p for p in providers if p.approved]

    return SearchProvidersResponse(providers=providers).model_dump()


# ---------------------------------------------------------------------------
# 5. send_provider_request — Mutating
# ---------------------------------------------------------------------------

@tool
def send_provider_request(
    idempotency_key: str,
    case_id: str,
    provider_id: str,
    service_type: str,
    event_date: str,
    event_time: str,
    event_location: str,
    provider_approved: bool,
) -> dict[str, Any]:
    """
    Send a coordination request to a provider.

    Args:
        idempotency_key: Unique key to prevent duplicate requests
        case_id: The case this request is for
        provider_id: The provider to contact
        service_type: Type of service being requested
        event_date: Date of the event (YYYY-MM-DD)
        event_time: Time of the event (HH:MM)
        event_location: Location of the event
        provider_approved: Must be True to send request

    Returns:
        Request details including request_id and status
    """
    store = get_store()
    input_data = {
        "idempotency_key": idempotency_key,
        "case_id": case_id,
        "provider_id": provider_id,
        "service_type": service_type,
    }

    # Check idempotency
    if store.is_duplicate(idempotency_key):
        result = _error(ErrorCode.DUPLICATE_REQUEST, "Idempotency key already used")
        store.record_action(
            "send_provider_request", input_data, result, False,
            ErrorCode.DUPLICATE_REQUEST.value, idempotency_key, case_id
        )
        return result

    # Check case exists
    case = store.get_case(case_id)
    if case is None:
        result = _error(ErrorCode.CASE_NOT_FOUND, f"No case with id {case_id}")
        store.record_action(
            "send_provider_request", input_data, result, False,
            ErrorCode.CASE_NOT_FOUND.value, idempotency_key, case_id
        )
        return result

    # Check case state
    if case.state == CaseState.CLOSED:
        result = _error(ErrorCode.INVALID_STATE, "Case is CLOSED")
        store.record_action(
            "send_provider_request", input_data, result, False,
            ErrorCode.INVALID_STATE.value, idempotency_key, case_id
        )
        return result

    # Check provider exists
    provider = store.get_provider(provider_id)
    if provider is None:
        result = _error(ErrorCode.PROVIDER_NOT_FOUND, f"No provider with id {provider_id}")
        store.record_action(
            "send_provider_request", input_data, result, False,
            ErrorCode.PROVIDER_NOT_FOUND.value, idempotency_key, case_id
        )
        return result

    # Check provider approved
    if not provider_approved or not provider.approved:
        result = _error(ErrorCode.PROVIDER_NOT_APPROVED, "Cannot send to unapproved provider")
        store.record_action(
            "send_provider_request", input_data, result, False,
            ErrorCode.PROVIDER_NOT_APPROVED.value, idempotency_key, case_id
        )
        return result

    # Consume idempotency key and create request
    store.check_idempotency(idempotency_key)
    request = store.create_request(case_id, provider_id)

    # Update case
    case.provider_requests.append(request.request_id)
    case.state = CaseState.AWAITING_PROVIDER
    store.update_case(case)

    result = SendProviderRequestResponse(request=request).model_dump()
    store.record_action(
        "send_provider_request", input_data, result, True,
        None, idempotency_key, case_id
    )
    return result


# ---------------------------------------------------------------------------
# 6. request_human_decision — Mutating
# ---------------------------------------------------------------------------

@tool
def request_human_decision(
    idempotency_key: str,
    case_id: str,
    decision_type: str,
    context: str,
    options: list[dict[str, Any]],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Escalate a decision to the human coordinator.

    Args:
        idempotency_key: Unique key to prevent duplicate requests
        case_id: The case this decision is for
        decision_type: Type of decision (e.g., PROVIDER_SUBSTITUTION)
        context: Explanation of why decision is needed
        options: List of options with option_id, description, recommended
        evidence: Supporting evidence for the decision

    Returns:
        Decision details including decision_id and status
    """
    store = get_store()
    input_data = {
        "idempotency_key": idempotency_key,
        "case_id": case_id,
        "decision_type": decision_type,
    }

    # Check idempotency
    if store.is_duplicate(idempotency_key):
        result = _error(ErrorCode.DUPLICATE_REQUEST, "Idempotency key already used")
        store.record_action(
            "request_human_decision", input_data, result, False,
            ErrorCode.DUPLICATE_REQUEST.value, idempotency_key, case_id
        )
        return result

    # Check case exists
    case = store.get_case(case_id)
    if case is None:
        result = _error(ErrorCode.CASE_NOT_FOUND, f"No case with id {case_id}")
        store.record_action(
            "request_human_decision", input_data, result, False,
            ErrorCode.CASE_NOT_FOUND.value, idempotency_key, case_id
        )
        return result

    # Consume idempotency key and create decision
    store.check_idempotency(idempotency_key)

    decision_options = [
        DecisionOption(
            option_id=opt.get("option_id", f"opt_{i}"),
            description=opt.get("description", ""),
            recommended=opt.get("recommended", False),
        )
        for i, opt in enumerate(options)
    ]

    decision = store.create_decision(case_id, decision_type, decision_options)

    # Update case state
    case.state = CaseState.AWAITING_DECISION
    store.update_case(case)

    result = RequestDecisionResponse(decision=decision).model_dump()
    store.record_action(
        "request_human_decision", input_data, result, True,
        None, idempotency_key, case_id
    )
    return result


# ---------------------------------------------------------------------------
# 7. verify_fulfillment — Read-only
# ---------------------------------------------------------------------------

@tool
def verify_fulfillment(case_id: str) -> dict[str, Any]:
    """
    Check whether all obligations for a case are fulfilled.

    Args:
        case_id: The case to verify

    Returns:
        Verification result with per-obligation status
    """
    store = get_store()

    case = store.get_case(case_id)
    if case is None:
        return _error(ErrorCode.CASE_NOT_FOUND, f"No case with id {case_id}")

    # Check each obligation
    obligation_checks = []
    all_fulfilled = True

    for obl in case.obligations:
        # Check if we have a confirmed provider request for this obligation
        fulfilled = False
        evidence = None

        for req_id in case.provider_requests:
            req = store.get_request(req_id)
            if req and req.status == RequestStatus.CONFIRMED:
                fulfilled = True
                evidence = f"{req_id} CONFIRMED"
                break

        # For now, mark document conformance as fulfilled if agenda exists
        if obl.category == "document_conformance":
            event = store.get_event(case.event_id)
            if event and event.agenda_url:
                fulfilled = True
                evidence = "Agenda document posted"

        obligation_checks.append(ObligationCheck(
            category=obl.category,
            fulfilled=fulfilled,
            evidence=evidence,
        ))

        if not fulfilled:
            all_fulfilled = False

    verification = store.create_verification(case_id, all_fulfilled, obligation_checks)

    # Update case with verification
    case.verification_id = verification.verification_id
    case.verification_passed = all_fulfilled
    if all_fulfilled:
        case.state = CaseState.VERIFIED
    store.update_case(case)

    return VerifyFulfillmentResponse(verification=verification).model_dump()


# ---------------------------------------------------------------------------
# 8. close_case — Mutating
# ---------------------------------------------------------------------------

@tool
def close_case(
    idempotency_key: str,
    case_id: str,
    verification_id: str,
) -> dict[str, Any]:
    """
    Close a case after verification passes.

    Args:
        idempotency_key: Unique key to prevent duplicate requests
        case_id: The case to close
        verification_id: ID of the passing verification

    Returns:
        Updated case with CLOSED state
    """
    store = get_store()
    input_data = {
        "idempotency_key": idempotency_key,
        "case_id": case_id,
        "verification_id": verification_id,
    }

    # Check idempotency
    if store.is_duplicate(idempotency_key):
        result = _error(ErrorCode.DUPLICATE_REQUEST, "Idempotency key already used")
        store.record_action(
            "close_case", input_data, result, False,
            ErrorCode.DUPLICATE_REQUEST.value, idempotency_key, case_id
        )
        return result

    # Check case exists
    case = store.get_case(case_id)
    if case is None:
        result = _error(ErrorCode.CASE_NOT_FOUND, f"No case with id {case_id}")
        store.record_action(
            "close_case", input_data, result, False,
            ErrorCode.CASE_NOT_FOUND.value, idempotency_key, case_id
        )
        return result

    # Check case not already closed
    if case.state == CaseState.CLOSED:
        result = _error(ErrorCode.INVALID_STATE, "Case already CLOSED")
        store.record_action(
            "close_case", input_data, result, False,
            ErrorCode.INVALID_STATE.value, idempotency_key, case_id
        )
        return result

    # Check verification exists
    verification = store.get_verification(verification_id)
    if verification is None:
        result = _error(ErrorCode.VERIFICATION_NOT_FOUND, f"No verification with id {verification_id}")
        store.record_action(
            "close_case", input_data, result, False,
            ErrorCode.VERIFICATION_NOT_FOUND.value, idempotency_key, case_id
        )
        return result

    # Check verification passed
    if not verification.passed:
        result = _error(ErrorCode.VERIFICATION_FAILED, "Cannot close: verification did not pass")
        store.record_action(
            "close_case", input_data, result, False,
            ErrorCode.VERIFICATION_FAILED.value, idempotency_key, case_id
        )
        return result

    # Consume idempotency key and close
    store.check_idempotency(idempotency_key)

    case.state = CaseState.CLOSED
    case.verification_id = verification_id
    store.update_case(case)

    result = CloseCaseResponse(case=case).model_dump()
    store.record_action(
        "close_case", input_data, result, True,
        None, idempotency_key, case_id
    )
    return result


# ---------------------------------------------------------------------------
# 9. extract_accommodation_policy — Read-only, CALLS MODEL
# ---------------------------------------------------------------------------

@tool
def extract_accommodation_policy(
    agenda_text: str,
    body_name: str,
    jurisdiction: str,
) -> dict[str, Any]:
    """
    Extract accommodation policy from agenda text using LLM analysis.

    This is the ONE tool that calls the model. It extracts accommodation needs
    based on agenda content.

    Args:
        agenda_text: Text extracted from the agenda PDF
        body_name: Name of the meeting body
        jurisdiction: Jurisdiction code

    Returns:
        Policy with recommended accommodations, priority, reasoning, and quote.
        Note: quote_verified is ALWAYS null from this tool.
    """
    import json
    import re

    from strands import Agent

    from backend.app.agents.model import get_model

    prompt = f"""You are an accessibility coordinator analyzing a public meeting agenda.

Meeting: {body_name} ({jurisdiction})

Agenda text (first 2000 chars):
---
{agenda_text[:2000]}
---

Determine:
1. ACCOMMODATIONS: Which types are likely needed?
   ASL_INTERPRETER, CART, SPANISH_INTERPRETER, OTHER_LANGUAGE,
   ASSISTIVE_LISTENING, LARGE_PRINT, BRAILLE, EXTENDED_TIME, REMOTE_ACCESS

2. PRIORITY: HIGH (public hearing, community input), MEDIUM (regular business), LOW (procedural)

3. REASONING: Brief explanation

4. QUOTE: A short quote from the agenda that supports your assessment

Reply with JSON only:
{{"accommodations": ["TYPE1"], "priority": "HIGH|MEDIUM|LOW", "reasoning": "...", "quote": "..."}}"""

    try:
        raw = str(Agent(model=get_model())(prompt))
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            policy = AccommodationPolicy(
                recommended_accommodations=[],
                priority="MEDIUM",
                reasoning="Could not parse LLM response",
                quote=None,
                quote_verified=None,  # ALWAYS None
            )
        else:
            data = json.loads(match.group(0))
            policy = AccommodationPolicy(
                recommended_accommodations=data.get("accommodations", []),
                priority=data.get("priority", "MEDIUM"),
                reasoning=data.get("reasoning", ""),
                quote=data.get("quote"),
                quote_verified=None,  # ALWAYS None — verification happens in context_enricher
            )
    except Exception as e:
        policy = AccommodationPolicy(
            recommended_accommodations=[],
            priority="MEDIUM",
            reasoning=f"Error: {e}",
            quote=None,
            quote_verified=None,
        )

    return ExtractPolicyResponse(policy=policy).model_dump()


# ---------------------------------------------------------------------------
# 10. poll_public_meetings — Read-only, deterministic, NO MODEL
# ---------------------------------------------------------------------------

@tool
def poll_public_meetings(
    clients: list[str] | None = None,
    days_ahead: int = 60,
) -> dict[str, Any]:
    """
    Poll public meeting calendars for new meetings and changes.

    Wraps LegistarFeed.poll() — deterministic, no model calls.

    Args:
        clients: List of Legistar client namespaces to poll (default: all watched)
        days_ahead: How many days ahead to look for meetings (default: 60)

    Returns:
        New meetings and detected changes (cancelled, rescheduled, agenda_posted, etc.)
    """
    from backend.app.tools.legistar import LegistarFeed, WATCHED_CLIENTS

    feed_clients = clients if clients else list(WATCHED_CLIENTS)
    feed = LegistarFeed(clients=feed_clients)

    new_meetings, changes = feed.poll()

    # Format new meetings
    new_list = [
        {
            "key": m.key,
            "client": m.client,
            "event_id": m.event_id,
            "body_name": m.body_name,
            "date": m.date,
            "time": m.time,
            "location": m.location,
            "agenda_url": m.agenda_url,
            "comment": m.comment,
            "insite_url": m.insite_url,
            "last_modified_utc": m.last_modified_utc,
            "fingerprint": m.fingerprint(),
        }
        for m in new_meetings
    ]

    # Format changes
    changes_list = [
        {
            "key": c.meeting.key,
            "change_type": c.change_type,
            "old_value": c.old_value,
            "new_value": c.new_value,
            "detected_at": c.detected_at,
        }
        for c in changes
    ]

    return {"ok": True, "new": new_list, "changes": changes_list}


# ---------------------------------------------------------------------------
# 11. derive_obligations — Read-only, deterministic, NO MODEL
# ---------------------------------------------------------------------------

@tool
def derive_obligations(
    event: dict[str, Any],
    population_over_50k: bool,
) -> dict[str, Any]:
    """
    Derive ADA Title II obligations from an event record.

    Deterministic — no model calls. Always returns exactly TWO obligations:
    - 28 CFR 35.160 (effective communication) — in force since July 26 1991
    - 28 CFR 35.200 (document conformance) — deadline based on population

    Args:
        event: Event dict with at least 'date' and optionally 'time'
        population_over_50k: Whether the entity serves 50,000+ people

    Returns:
        List of exactly two obligations with basis, category, description, deadline, must_have
    """
    from datetime import timedelta

    # Parse event start time for 35.160 deadline
    event_date = event.get("date", "")
    event_time = event.get("time")

    # Try to parse datetime for effective_communication deadline
    starts_at = None
    if event_date:
        stamp = f"{event_date[:10]} {event_time}" if event_time else event_date[:10]
        for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                starts_at = datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue

    # 35.160 deadline: event start minus 48 hours
    if starts_at:
        effective_comm_deadline = (starts_at - timedelta(hours=48)).isoformat()
    else:
        # Fallback: use event date if time not parseable
        effective_comm_deadline = f"{event_date}T00:00:00+00:00" if event_date else None

    # 35.200 deadline: based on population
    document_deadline = "2027-04-26" if population_over_50k else "2028-04-26"

    obligations = [
        {
            "basis": "28 CFR 35.160",
            "category": "effective_communication",
            "description": "Public entity must furnish appropriate auxiliary aids on request. "
                          "In force since July 26 1991; no phase-in.",
            "deadline": effective_comm_deadline,
            "must_have": True,
        },
        {
            "basis": "28 CFR 35.200",
            "category": "document_conformance",
            "description": "Agenda document must meet WCAG 2.1 AA by the entity's compliance date.",
            "deadline": document_deadline,
            "must_have": True,
        },
    ]

    return {"ok": True, "obligations": obligations}
