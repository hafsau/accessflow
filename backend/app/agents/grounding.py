"""
AccessFlow grounding verification — quote verification for Cedar context enrichment.

This module provides verify_quote(), a PLAIN FUNCTION (not a tool) that checks
whether a claimed quote actually appears in source text. It feeds into Cedar's
context_enricher so that:

1. The model cannot verify its own quotes (it can only claim them)
2. Cedar policies can gate actions based on quote_verified status
3. Grounding is enforced at the policy layer, not via prompts

The model calls extract_accommodation_policy which returns quote_verified=None.
The context_enricher then calls verify_quote() with the quote and source text.
Cedar sees quote_verified=true/false and can block ungrounded claims.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalize_text(text: str) -> str:
    """Normalize text for fuzzy matching."""
    # Lowercase
    text = text.lower()
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove punctuation except essential
    text = re.sub(r"[^\w\s\-]", "", text)
    return text.strip()


def verify_quote(quote: str, source_text: str, threshold: float = 0.85) -> bool:
    """
    Verify that a claimed quote actually appears in source text.

    This is NOT a tool — it cannot be called by the model. It is invoked by
    the context_enricher to populate context.session.quote_verified for Cedar.

    Args:
        quote: The claimed quote from extract_accommodation_policy
        source_text: The original text the quote should come from
        threshold: Minimum similarity ratio (0.0-1.0) for fuzzy match

    Returns:
        True if the quote appears in source_text (exact or fuzzy match)
        False otherwise

    Examples:
        >>> verify_quote("PUBLIC HEARING", "Agenda: PUBLIC HEARING on housing")
        True
        >>> verify_quote("public hearing on housing", "PUBLIC HEARING ON HOUSING POLICY")
        True
        >>> verify_quote("The city is non-compliant", "Regular meeting agenda")
        False
    """
    if not quote or not source_text:
        return False

    # Normalize both
    norm_quote = normalize_text(quote)
    norm_source = normalize_text(source_text)

    if not norm_quote:
        return False

    # Exact substring match (after normalization)
    if norm_quote in norm_source:
        return True

    # Fuzzy match: slide a window of quote length across source
    quote_len = len(norm_quote)
    if quote_len > len(norm_source):
        return False

    # Check similarity at each position
    for i in range(len(norm_source) - quote_len + 1):
        window = norm_source[i : i + quote_len]
        ratio = SequenceMatcher(None, norm_quote, window).ratio()
        if ratio >= threshold:
            return True

    # Also try matching against source split into sentences/segments
    # This handles cases where the quote is a paraphrase of a longer segment
    sentences = re.split(r"[.!?\n]", source_text)
    for sentence in sentences:
        norm_sentence = normalize_text(sentence)
        if not norm_sentence:
            continue
        ratio = SequenceMatcher(None, norm_quote, norm_sentence).ratio()
        if ratio >= threshold:
            return True

    return False


def enrich_with_grounding(
    ctx: dict,
    quote: str | None,
    source_text: str | None,
) -> dict:
    """
    Helper for context_enricher to add quote verification to session context.

    Args:
        ctx: The existing context dict
        quote: The claimed quote (may be None)
        source_text: The source text to verify against (may be None)

    Returns:
        Updated context with quote_verified added to session
    """
    if "session" not in ctx:
        ctx["session"] = {}

    if quote is None or source_text is None:
        ctx["session"]["quote_verified"] = None
    else:
        ctx["session"]["quote_verified"] = verify_quote(quote, source_text)

    return ctx
