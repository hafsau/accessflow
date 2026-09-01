"""
AccessFlow — real inbound edge.

This module REPLACES the simulated inbound half of §9 of the master prompt.
Cases are no longer seeded by the author. They are opened by real public
meetings published to the Granicus/Legistar Web API, which powers the
legislative calendars of thousands of US state and local public bodies.

Verified 2026-08-25 from this environment:
    GET https://webapi.legistar.com/v1/seattle/Persons?$top=2   -> 200, JSON, no key
    GET https://webapi.legistar.com/v1/seattle/Events?$top=3    -> 200, JSON, no key
    Top event returned: EventDate 2026-09-08, EventBodyName "City Council",
    EventComment "Cancellation Notice", EventLocation "Council Chamber, City Hall,
    600 4th Avenue, Seattle, WA 98104", plus EventAgendaFile (PDF URL)
    and EventInSiteURL.

No API key. No registration. OData query syntax ($top, $filter, $orderby).

Why this matters for the demo: the master prompt's hero fixture B is
"Provider A cancels 24h before event", injected by a button. In the real feed,
`EventComment` genuinely becomes "Cancellation Notice" and
`EventLastModifiedUtc` genuinely moves. The disruption the agent recovers from
is a real disruption, observed, not staged.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx

log = logging.getLogger(__name__)

BASE = "https://webapi.legistar.com/v1"

# Public bodies to watch. Each is a live Legistar client namespace.
# Verify a namespace before adding it:  GET {BASE}/{client}/Bodies?$top=1
# Limited to jurisdictions with seeded provider coverage, plus sacramento
# (deliberately kept for PROVIDER_SHORTAGE demo beat).
WATCHED_CLIENTS: tuple[str, ...] = (
    "seattle", "oakland", "sanjose", "kingcounty", "alameda", "sacramento",
)

# Legistar is a shared public service. Be a good citizen.
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_HEADERS = {"User-Agent": "AccessFlow/0.1 (hackathon project; contact in repo README)"}


# ---------------------------------------------------------------------------
# Domain records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Meeting:
    """One real public meeting, normalised from a Legistar Event record."""

    client: str
    event_id: int
    body_name: str
    date: str                 # "2026-09-08"
    time: str | None          # "2:00 PM" — Legistar sometimes leaves this blank
    location: str | None
    agenda_url: str | None
    comment: str | None       # where cancellations actually show up
    insite_url: str | None
    last_modified_utc: str | None
    row_version: str | None

    @property
    def key(self) -> str:
        return f"{self.client}:{self.event_id}"

    @property
    def is_cancelled(self) -> bool:
        return bool(self.comment) and "cancel" in self.comment.lower()

    @property
    def starts_at(self) -> datetime | None:
        if not self.date:
            return None
        stamp = f"{self.date[:10]} {self.time}" if self.time else self.date[:10]
        for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d"):
            try:
                return datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    def fingerprint(self) -> str:
        """Content hash used to detect a real change when Legistar's own
        modification stamp is absent or unreliable."""
        payload = "|".join(
            str(x) for x in (self.date, self.time, self.location, self.agenda_url, self.comment)
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class MeetingChange:
    """A real, observed change to a real meeting. Feeds the ADAPT step of the
    core loop. Nothing here is injected."""

    meeting: Meeting
    change_type: str          # cancelled | rescheduled | relocated | agenda_posted | agenda_replaced
    old_value: str | None
    new_value: str | None
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LegistarFeed:
    """Polls real public meeting calendars and emits normalised meetings and
    genuine change events.

    This is the only part of AccessFlow that touches the outside world.
    Provider coordination stays simulated, and the UI says so — but the
    *trigger*, the *deadline* and the *disruption* are all real.
    """

    def __init__(self, clients: Iterable[str] = WATCHED_CLIENTS) -> None:
        self.clients = tuple(clients)
        self._seen: dict[str, str] = {}   # meeting.key -> fingerprint

    # -- fetch ------------------------------------------------------------

    def _get(self, client: str, path: str, **params: Any) -> list[dict[str, Any]]:
        url = f"{BASE}/{client}/{path}"
        with httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as http:
            resp = http.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        return data if isinstance(data, list) else [data]

    def upcoming(self, client: str, days_ahead: int = 45) -> list[Meeting]:
        """Real meetings scheduled in the window the accommodation deadline
        actually runs against."""
        today = datetime.now(timezone.utc).date()
        horizon = today + timedelta(days=days_ahead)
        odata = (
            f"EventDate ge datetime'{today.isoformat()}' and "
            f"EventDate le datetime'{horizon.isoformat()}'"
        )
        rows = self._get(
            client, "Events",
            **{"$filter": odata, "$orderby": "EventDate asc", "$top": "200"},
        )
        return [self._normalise(client, r) for r in rows]

    def upcoming_with_agenda(self, days_ahead: int = 10) -> list[Meeting]:
        """Meetings the agent can actually complete: agenda published, date within window.

        For demo prioritisation. Returns meetings across ALL watched clients that have:
        - A non-null agenda_url (agenda is published)
        - A date within the next `days_ahead` days

        Sorted by date ascending.
        """
        actionable: list[Meeting] = []
        for client in self.clients:
            try:
                meetings = self.upcoming(client, days_ahead=days_ahead)
                for m in meetings:
                    if m.agenda_url:
                        actionable.append(m)
            except Exception as exc:  # noqa: BLE001
                log.warning("legistar fetch failed for %s: %s", client, exc)
                continue

        # Sort by date
        actionable.sort(key=lambda m: m.date)
        return actionable

    @staticmethod
    def _normalise(client: str, r: dict[str, Any]) -> Meeting:
        return Meeting(
            client=client,
            event_id=int(r["EventId"]),
            body_name=(r.get("EventBodyName") or "").strip(),
            date=(r.get("EventDate") or "")[:10],
            time=(r.get("EventTime") or None),
            location=(r.get("EventLocation") or None),
            agenda_url=(r.get("EventAgendaFile") or None),
            comment=(r.get("EventComment") or None),
            insite_url=(r.get("EventInSiteURL") or None),
            last_modified_utc=(r.get("EventLastModifiedUtc") or None),
            row_version=(r.get("EventRowVersion") or None),
        )

    # -- change detection --------------------------------------------------

    def poll(self) -> tuple[list[Meeting], list[MeetingChange]]:
        """One background cycle. Returns (newly seen meetings, real changes).

        The background loop in §10 calls this. It does NOT re-run the agent on
        every meeting every cycle — only on meetings that are new, or whose
        content actually moved. That is the difference between an agent and a
        cron job, and it is enforced here rather than asserted in a README.
        """
        new: list[Meeting] = []
        changes: list[MeetingChange] = []

        for client in self.clients:
            try:
                meetings = self.upcoming(client)
            except Exception as exc:                      # noqa: BLE001
                log.warning("legistar poll failed for %s: %s", client, exc)
                continue

            for m in meetings:
                fp = m.fingerprint()
                prior = self._seen.get(m.key)

                if prior is None:
                    self._seen[m.key] = fp
                    new.append(m)
                    if m.agenda_url:
                        changes.append(MeetingChange(m, "agenda_posted", None, m.agenda_url))
                    continue

                if prior == fp:
                    continue                              # nothing moved; spend no tokens

                self._seen[m.key] = fp
                changes.append(MeetingChange(m, self._classify(m), prior, fp))

        return new, changes

    @staticmethod
    def _classify(m: Meeting) -> str:
        if m.is_cancelled:
            return "cancelled"
        return "rescheduled_or_relocated"


# ---------------------------------------------------------------------------
# Obligation derivation
# ---------------------------------------------------------------------------

# ADA Title II web rule, verified 2026-08-25:
#   Standard: WCAG 2.1 Level AA.
#   Covers all state and local governments, their agencies, and special districts.
#   Compliance dates AFTER the April 2026 extension (Federal Register 2026-07663):
#     - entities serving 50,000+ people ....... 2027-04-26   (was 2026-04-24)
#     - entities under 50,000 / special districts 2028-04-26 (was 2027-04-26)
#   Preexisting documents are excepted; NEW documents are not. A meeting agenda
#   posted today for a future meeting is new content.
ADA_TITLE_II_LARGE_ENTITY_DEADLINE = "2027-04-26"
ADA_TITLE_II_SMALL_ENTITY_DEADLINE = "2028-04-26"


def derive_obligations(m: Meeting, entity_population_over_50k: bool) -> list[dict[str, Any]]:
    """Turn a real meeting into the accommodation obligations it carries.

    Note what this function does NOT do: it does not judge anyone. It states
    what is owed and by when. Whether it has been met is a question of recorded
    evidence, decided later by the Verification Agent and gated by Cedar.
    """
    deadline = (
        ADA_TITLE_II_LARGE_ENTITY_DEADLINE
        if entity_population_over_50k
        else ADA_TITLE_II_SMALL_ENTITY_DEADLINE
    )
    out: list[dict[str, Any]] = []

    if m.agenda_url:
        out.append({
            "category": "accessible_materials",
            "description": f"Agenda document for {m.body_name} on {m.date} is new web content "
                           f"under the ADA Title II web rule and must meet WCAG 2.1 Level AA.",
            "must_have": True,
            "deadline": deadline,
            "evidence_required": "conformance_record",
            "source_url": m.agenda_url,
        })

    start = m.starts_at
    if start:
        # Most public bodies publish their own advance-notice window for
        # interpreter / CART requests. 48h is the common default; the real
        # window per body belongs in the org record, not in this function.
        out.append({
            "category": "request_window",
            "description": f"Accommodation request window for {m.body_name} closes 48 hours "
                           f"before the meeting.",
            "must_have": True,
            "deadline": (start - timedelta(hours=48)).isoformat(),
            "evidence_required": "coordination_record",
        })

    return out
