"""
Tests for the Lambda poller handler.

The critical test:
> A poll where nothing moved produces zero InvokeAgentRuntime calls.

This is the difference between an agent and a cron job.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def test_no_changes_zero_agent_invocations():
    """A poll with no changes makes zero InvokeAgentRuntime calls.

    This is the critical invariant from the Day 7 spec.
    """
    # Mock the dependencies
    with patch("backend.poller.poller_handler.load_fingerprints") as mock_load_fp:
        with patch("backend.poller.poller_handler.save_fingerprints_batch") as mock_save_fp:
            with patch("backend.poller.poller_handler.LegistarFeed") as MockFeed:
                with patch("backend.poller.poller_handler._get_bedrock_agent_runtime") as mock_bedrock:
                    # Set up: fingerprints already match what feed will return
                    existing_fingerprints = {
                        "seattle:6860": "abc123",
                        "seattle:6861": "def456",
                    }
                    mock_load_fp.return_value = existing_fingerprints

                    # Feed returns the same meetings with same fingerprints
                    mock_feed_instance = MagicMock()
                    mock_feed_instance._seen = existing_fingerprints.copy()
                    mock_feed_instance.poll.return_value = ([], [])  # No new, no changes
                    MockFeed.return_value = mock_feed_instance

                    # Import and run handler
                    from backend.poller.poller_handler import handler
                    result = handler({}, None)

                    # Verify result
                    body = json.loads(result["body"])
                    assert body["new_meetings"] == 0
                    assert body["changes"] == 0
                    assert body["agent_invocations"] == 0

                    # THE CRITICAL ASSERTION: Bedrock was never called
                    mock_bedrock.assert_not_called()


def test_new_meeting_invokes_agent():
    """A new meeting triggers exactly one agent invocation."""
    from backend.app.tools.legistar import Meeting

    with patch("backend.poller.poller_handler.load_fingerprints") as mock_load_fp:
        with patch("backend.poller.poller_handler.save_fingerprints_batch") as mock_save_fp:
            with patch("backend.poller.poller_handler.LegistarFeed") as MockFeed:
                with patch("backend.poller.poller_handler._invoke_agent") as mock_invoke:
                    with patch("backend.poller.poller_handler.AGENT_ID", "test-agent"):
                        # No existing fingerprints
                        mock_load_fp.return_value = {}

                        # Create a real Meeting object
                        new_meeting = Meeting(
                            client="seattle",
                            event_id=6860,
                            body_name="City Council",
                            date="2026-09-08",
                            time="2:00 PM",
                            location="City Hall",
                            agenda_url="https://example.com/agenda.pdf",
                            comment=None,
                            insite_url=None,
                            last_modified_utc=None,
                            row_version=None,
                        )

                        # Feed returns one new meeting
                        mock_feed_instance = MagicMock()
                        mock_feed_instance._seen = {}
                        mock_feed_instance.poll.return_value = ([new_meeting], [])
                        MockFeed.return_value = mock_feed_instance

                        # Mock agent invocation
                        mock_invoke.return_value = {"success": True}

                        from backend.poller.poller_handler import handler
                        result = handler({}, None)

                        body = json.loads(result["body"])
                        assert body["new_meetings"] == 1
                        assert body["agent_invocations"] == 1

                        # Agent was invoked exactly once
                        assert mock_invoke.call_count == 1


def test_max_cases_per_invocation_caps_at_3():
    """At most 3 cases are processed per invocation; rest are queued."""
    from backend.app.tools.legistar import Meeting, MeetingChange

    with patch("backend.poller.poller_handler.load_fingerprints") as mock_load_fp:
        with patch("backend.poller.poller_handler.save_fingerprints_batch") as mock_save_fp:
            with patch("backend.poller.poller_handler.LegistarFeed") as MockFeed:
                with patch("backend.poller.poller_handler._invoke_agent") as mock_invoke:
                    with patch("backend.poller.poller_handler._queue_overflow") as mock_queue:
                        with patch("backend.poller.poller_handler.AGENT_ID", "test-agent"):
                            mock_load_fp.return_value = {}

                            # Create 5 new meetings
                            meetings = []
                            for i in range(5):
                                m = Meeting(
                                    client="seattle",
                                    event_id=6860 + i,
                                    body_name="City Council",
                                    date="2026-09-08",
                                    time="2:00 PM",
                                    location="City Hall",
                                    agenda_url=f"https://example.com/agenda{i}.pdf",
                                    comment=None,
                                    insite_url=None,
                                    last_modified_utc=None,
                                    row_version=None,
                                )
                                meetings.append(m)

                            mock_feed_instance = MagicMock()
                            mock_feed_instance._seen = {}
                            mock_feed_instance.poll.return_value = (meetings, [])
                            MockFeed.return_value = mock_feed_instance

                            mock_invoke.return_value = {"success": True}

                            from backend.poller.poller_handler import handler
                            result = handler({}, None)

                            body = json.loads(result["body"])
                            # Only 3 processed, 2 queued
                            assert body["agent_invocations"] == 3
                            assert body["queued"] == 2

                            # Verify invoke was called exactly 3 times
                            assert mock_invoke.call_count == 3

                            # Verify queue was called with 2 meetings
                            mock_queue.assert_called_once()
                            queued_meetings = mock_queue.call_args[0][0]
                            assert len(queued_meetings) == 2


def test_fingerprints_persisted_after_changes():
    """Updated fingerprints are saved to DynamoDB after processing."""
    from backend.app.tools.legistar import Meeting

    with patch("backend.poller.poller_handler.load_fingerprints") as mock_load_fp:
        with patch("backend.poller.poller_handler.save_fingerprints_batch") as mock_save_fp:
            with patch("backend.poller.poller_handler.LegistarFeed") as MockFeed:
                with patch("backend.poller.poller_handler._invoke_agent") as mock_invoke:
                    mock_load_fp.return_value = {"seattle:6859": "old123"}

                    new_meeting = Meeting(
                        client="seattle",
                        event_id=6860,
                        body_name="City Council",
                        date="2026-09-08",
                        time="2:00 PM",
                        location="City Hall",
                        agenda_url="https://example.com/agenda.pdf",
                        comment=None,
                        insite_url=None,
                        last_modified_utc=None,
                        row_version=None,
                    )

                    mock_feed_instance = MagicMock()
                    mock_feed_instance._seen = {"seattle:6859": "old123"}
                    mock_feed_instance.poll.return_value = ([new_meeting], [])
                    MockFeed.return_value = mock_feed_instance

                    mock_invoke.return_value = {"success": True}

                    from backend.poller.poller_handler import handler
                    handler({}, None)

                    # Verify fingerprints were saved
                    mock_save_fp.assert_called_once()
                    saved_fps = mock_save_fp.call_args[0][0]

                    # Should include both old and new fingerprints
                    assert "seattle:6859" in saved_fps
                    assert "seattle:6860" in saved_fps


def test_structured_logging_format():
    """Verify structured JSON logging format for CloudWatch."""
    import io
    import logging

    with patch("backend.poller.poller_handler.load_fingerprints") as mock_load_fp:
        with patch("backend.poller.poller_handler.save_fingerprints_batch"):
            with patch("backend.poller.poller_handler.LegistarFeed") as MockFeed:
                with patch("backend.poller.poller_handler.load_budget") as mock_load_budget:
                    mock_load_fp.return_value = {}
                    mock_load_budget.return_value = {"date": "2026-09-01", "spent": 0.0, "calls": 0}

                    mock_feed_instance = MagicMock()
                    mock_feed_instance._seen = {}
                    mock_feed_instance.poll.return_value = ([], [])
                    MockFeed.return_value = mock_feed_instance

                    # Capture log output
                    log_capture = io.StringIO()
                    handler = logging.StreamHandler(log_capture)
                    handler.setLevel(logging.INFO)

                    from backend.poller.poller_handler import logger
                    logger.addHandler(handler)

                    try:
                        from backend.poller.poller_handler import handler as lambda_handler
                        lambda_handler({}, None)

                        log_output = log_capture.getvalue()

                        # Verify structured logs are JSON
                        for line in log_output.strip().split("\n"):
                            if line:
                                data = json.loads(line)
                                assert "event" in data
                                assert "timestamp" in data
                    finally:
                        logger.removeHandler(handler)


def test_budget_exceeded_queues_all_skips_invocations():
    """Budget at cap: zero agent invocations, all changes queued."""
    from backend.app.tools.legistar import Meeting

    with patch("backend.poller.poller_handler.load_fingerprints") as mock_load_fp:
        with patch("backend.poller.poller_handler.save_fingerprints_batch") as mock_save_fp:
            with patch("backend.poller.poller_handler.LegistarFeed") as MockFeed:
                with patch("backend.poller.poller_handler._invoke_agent") as mock_invoke:
                    with patch("backend.poller.poller_handler._queue_overflow") as mock_queue:
                        with patch("backend.poller.poller_handler.load_budget") as mock_load_budget:
                            with patch("backend.poller.poller_handler.DAILY_USD_CAP", 1.50):
                                # Budget already at cap
                                mock_load_budget.return_value = {
                                    "date": "2026-09-01",
                                    "spent": 1.50,  # At cap
                                    "calls": 16,
                                }

                                mock_load_fp.return_value = {}

                                # One new meeting
                                new_meeting = Meeting(
                                    client="seattle",
                                    event_id=6860,
                                    body_name="City Council",
                                    date="2026-09-08",
                                    time="2:00 PM",
                                    location="City Hall",
                                    agenda_url="https://example.com/agenda.pdf",
                                    comment=None,
                                    insite_url=None,
                                    last_modified_utc=None,
                                    row_version=None,
                                )

                                mock_feed_instance = MagicMock()
                                mock_feed_instance._seen = {}
                                mock_feed_instance.poll.return_value = ([new_meeting], [])
                                MockFeed.return_value = mock_feed_instance

                                from backend.poller.poller_handler import handler
                                result = handler({}, None)

                                body = json.loads(result["body"])

                                # THE CRITICAL ASSERTIONS
                                assert body["agent_invocations"] == 0
                                assert body["queued"] == 1
                                assert body["budget_skipped"] is True
                                assert body["budget_spent_usd"] == 1.50

                                # Agent was NEVER invoked
                                mock_invoke.assert_not_called()

                                # Change was queued
                                mock_queue.assert_called_once()
                                queued = mock_queue.call_args[0][0]
                                assert len(queued) == 1

                                # Fingerprints still saved
                                mock_save_fp.assert_called_once()
