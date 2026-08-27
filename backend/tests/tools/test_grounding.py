"""
Tests for AccessFlow grounding verification.

verify_quote() is a plain function, NOT a tool. The model cannot call it.
It is invoked by context_enricher to populate quote_verified for Cedar.
"""
from __future__ import annotations

import pytest

from backend.app.agents.grounding import enrich_with_grounding, normalize_text, verify_quote


class TestNormalizeText:
    def test_lowercase(self):
        assert normalize_text("PUBLIC HEARING") == "public hearing"

    def test_collapse_whitespace(self):
        assert normalize_text("public   \n  hearing") == "public hearing"

    def test_remove_punctuation(self):
        assert normalize_text("Hello, World!") == "hello world"

    def test_preserve_hyphens(self):
        assert normalize_text("co-chair") == "co-chair"


class TestVerifyQuote:
    def test_exact_match(self):
        assert verify_quote(
            "PUBLIC HEARING",
            "Agenda: PUBLIC HEARING on housing ordinance"
        ) is True

    def test_case_insensitive(self):
        assert verify_quote(
            "public hearing",
            "Agenda: PUBLIC HEARING on housing"
        ) is True

    def test_fuzzy_match(self):
        # Small typo should still match with default threshold
        assert verify_quote(
            "public heering",  # typo
            "Agenda: PUBLIC HEARING on housing",
            threshold=0.80
        ) is True

    def test_no_match(self):
        assert verify_quote(
            "The city is non-compliant",
            "Regular meeting agenda for City Council"
        ) is False

    def test_empty_quote(self):
        assert verify_quote("", "Some source text") is False

    def test_empty_source(self):
        assert verify_quote("Some quote", "") is False

    def test_none_quote(self):
        assert verify_quote(None, "Source text") is False

    def test_none_source(self):
        assert verify_quote("Quote", None) is False

    def test_quote_longer_than_source(self):
        assert verify_quote(
            "This is a very long quote that exceeds the source",
            "Short"
        ) is False

    def test_sentence_matching(self):
        # Quote should match against a sentence in source
        source = """
        The meeting will begin at 2:00 PM.
        PUBLIC HEARING on the housing ordinance.
        All are welcome to attend.
        """
        # Exact substring after normalization
        assert verify_quote("public hearing on the housing ordinance", source) is True
        # Shorter phrase also matches
        assert verify_quote("public hearing", source) is True

    def test_paraphrase_match(self):
        # Similar content should match with fuzzy threshold
        source = "Public hearing regarding proposed housing policy changes"
        quote = "public hearing on housing policy"
        # This requires a lower threshold due to differences
        assert verify_quote(quote, source, threshold=0.70) is True

    def test_completely_different(self):
        assert verify_quote(
            "accessibility accommodations required",
            "Regular business meeting with no public comment"
        ) is False


class TestEnrichWithGrounding:
    def test_adds_quote_verified_true(self):
        ctx = {"session": {}}
        result = enrich_with_grounding(
            ctx,
            quote="PUBLIC HEARING",
            source_text="Agenda: PUBLIC HEARING on housing"
        )
        assert result["session"]["quote_verified"] is True

    def test_adds_quote_verified_false(self):
        ctx = {"session": {}}
        result = enrich_with_grounding(
            ctx,
            quote="fabricated content",
            source_text="Actual meeting agenda text"
        )
        assert result["session"]["quote_verified"] is False

    def test_none_quote(self):
        ctx = {"session": {}}
        result = enrich_with_grounding(ctx, quote=None, source_text="Some text")
        assert result["session"]["quote_verified"] is None

    def test_none_source(self):
        ctx = {"session": {}}
        result = enrich_with_grounding(ctx, quote="Quote", source_text=None)
        assert result["session"]["quote_verified"] is None

    def test_creates_session_if_missing(self):
        ctx = {}
        result = enrich_with_grounding(
            ctx,
            quote="PUBLIC HEARING",
            source_text="PUBLIC HEARING"
        )
        assert "session" in result
        assert result["session"]["quote_verified"] is True

    def test_preserves_existing_session_data(self):
        ctx = {"session": {"existing_key": "existing_value"}}
        result = enrich_with_grounding(
            ctx,
            quote="PUBLIC HEARING",
            source_text="PUBLIC HEARING"
        )
        assert result["session"]["existing_key"] == "existing_value"
        assert result["session"]["quote_verified"] is True
