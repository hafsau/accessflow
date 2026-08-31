"""
Persistence layer for Lambda — DynamoDB for state, S3 for cache.

Lambda is stateless. This module handles:
1. Fingerprints (meeting key -> fingerprint hash) — DynamoDB
2. Budget ledger (daily spend tracking) — DynamoDB
3. Agenda cache (sha256 -> PDF text) — S3

All operations are idempotent and handle missing resources gracefully.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

# Table/bucket names from environment
FINGERPRINT_TABLE = os.getenv("FINGERPRINT_TABLE", "accessflow-fingerprints")
BUDGET_TABLE = os.getenv("BUDGET_TABLE", "accessflow-budget")
AGENDA_CACHE_BUCKET = os.getenv("AGENDA_CACHE_BUCKET", "accessflow-agenda-cache")

# Lazy-init clients (Lambda reuses across warm invocations)
_dynamodb = None
_s3 = None


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


# ---------------------------------------------------------------------------
# Fingerprints — meeting key -> content hash
# ---------------------------------------------------------------------------


def load_fingerprints() -> dict[str, str]:
    """Load all fingerprints from DynamoDB.

    Returns:
        dict mapping meeting key (e.g., "seattle:6860") to fingerprint hash.
    """
    try:
        table = _get_dynamodb().Table(FINGERPRINT_TABLE)
        response = table.scan(ProjectionExpression="meeting_key, fingerprint")
        items = response.get("Items", [])

        # Handle pagination
        while "LastEvaluatedKey" in response:
            response = table.scan(
                ProjectionExpression="meeting_key, fingerprint",
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))

        return {item["meeting_key"]: item["fingerprint"] for item in items}
    except ClientError as e:
        log.warning("Failed to load fingerprints: %s", e)
        return {}


def save_fingerprint(meeting_key: str, fingerprint: str) -> None:
    """Save a single fingerprint to DynamoDB."""
    try:
        table = _get_dynamodb().Table(FINGERPRINT_TABLE)
        table.put_item(
            Item={
                "meeting_key": meeting_key,
                "fingerprint": fingerprint,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except ClientError as e:
        log.error("Failed to save fingerprint for %s: %s", meeting_key, e)
        raise


def save_fingerprints_batch(fingerprints: dict[str, str]) -> None:
    """Save multiple fingerprints in batch."""
    if not fingerprints:
        return

    try:
        table = _get_dynamodb().Table(FINGERPRINT_TABLE)
        now = datetime.now(timezone.utc).isoformat()

        with table.batch_writer() as batch:
            for meeting_key, fingerprint in fingerprints.items():
                batch.put_item(
                    Item={
                        "meeting_key": meeting_key,
                        "fingerprint": fingerprint,
                        "updated_at": now,
                    }
                )
    except ClientError as e:
        log.error("Failed to batch save fingerprints: %s", e)
        raise


# ---------------------------------------------------------------------------
# Budget ledger — daily spend tracking
# ---------------------------------------------------------------------------


def load_budget() -> dict[str, Any]:
    """Load today's budget from DynamoDB.

    Returns:
        dict with keys: date, spent, calls
    """
    today = date.today().isoformat()
    try:
        table = _get_dynamodb().Table(BUDGET_TABLE)
        response = table.get_item(Key={"date": today})
        item = response.get("Item")
        if item:
            return {
                "date": item["date"],
                "spent": float(item.get("spent", 0.0)),
                "calls": int(item.get("calls", 0)),
            }
    except ClientError as e:
        log.warning("Failed to load budget: %s", e)

    return {"date": today, "spent": 0.0, "calls": 0}


def save_budget(budget: dict[str, Any]) -> None:
    """Save budget to DynamoDB."""
    try:
        table = _get_dynamodb().Table(BUDGET_TABLE)
        table.put_item(
            Item={
                "date": budget["date"],
                "spent": str(budget["spent"]),  # DynamoDB doesn't like float
                "calls": budget["calls"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except ClientError as e:
        log.error("Failed to save budget: %s", e)
        raise


# ---------------------------------------------------------------------------
# Agenda cache — S3 keyed by sha256
# ---------------------------------------------------------------------------


def agenda_cache_key(url: str) -> str:
    """Generate S3 key for an agenda URL."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    return f"agendas/{url_hash}.json"


def get_cached_agenda(url: str) -> dict[str, Any] | None:
    """Get cached agenda from S3.

    Returns:
        dict with keys: url, text, page_count, fetched_at, content_hash
        or None if not cached.
    """
    key = agenda_cache_key(url)
    try:
        s3 = _get_s3()
        response = s3.get_object(Bucket=AGENDA_CACHE_BUCKET, Key=key)
        data = json.loads(response["Body"].read().decode("utf-8"))
        log.info("Cache hit for agenda: %s", url[:80])
        return data
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return None
        log.warning("Failed to get cached agenda: %s", e)
        return None


def cache_agenda(url: str, text: str, page_count: int, content_hash: str) -> None:
    """Cache agenda to S3."""
    key = agenda_cache_key(url)
    data = {
        "url": url,
        "text": text,
        "page_count": page_count,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": content_hash,
    }
    try:
        s3 = _get_s3()
        s3.put_object(
            Bucket=AGENDA_CACHE_BUCKET,
            Key=key,
            Body=json.dumps(data).encode("utf-8"),
            ContentType="application/json",
        )
        log.info("Cached agenda: %s", url[:80])
    except ClientError as e:
        log.error("Failed to cache agenda: %s", e)
        # Don't raise — caching failure shouldn't block the workflow
