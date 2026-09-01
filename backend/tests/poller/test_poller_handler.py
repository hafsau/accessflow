"""
Tests for the Lambda poller handler.

The critical test:
> A poll where nothing moved produces zero SQS messages.

This is the difference between an agent and a cron job.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def test_no_changes_zero_queue_messages():
    """A poll with no changes queues zero messages.

    This is the critical invariant from the Day 7 spec.
    """
    with patch("backend.poller.poller_handler.load_fingerprints") as mock_load_fp:
        with patch("backend.poller.poller_handler.save_fingerprints_batch") as mock_save_fp:
            with patch("backend.poller.poller_handler.LegistarFeed") as MockFeed:
                with patch("backend.poller.poller_handler._queue_meeting") as mock_queue:
                    with patch("backend.poller.poller_handler.load_budget") as mock_load_budget:
                        mock_load_budget.return_value = {"date": "2026-09-01", "spent": 0.0, "calls": 0}

                        # Set up: fingerprints already match what feed will return
                        existing_fingerprints = {
                            "seattle:6860": "abc123",
                            "seattle:6861": "def456",
                        }
                        mock_load_fp.return_value = existing_fingerprints

                        # Feed returns no new meetings, no changes
                        mock_feed_instance = MagicMock()
                        mock_feed_instance._seen = existing_fingerprints.copy()
                        mock_feed_instance.poll.return_value = ([], [])
                        MockFeed.return_value = mock_feed_instance

                        from backend.poller.poller_handler import handler
                        result = handler({}, None)

                        body = json.loads(result["body"])
                        assert body["new_meetings"] == 0
                        assert body["changes"] == 0
                        assert body["queued"] == 0

                        # THE CRITICAL ASSERTION: SQS was never called
                        mock_queue.assert_not_called()


def test_new_meeting_queues_to_sqs():
    """A new meeting queues exactly one SQS message."""
    from backend.app.tools.legistar import Meeting

    with patch("backend.poller.poller_handler.load_fingerprints") as mock_load_fp:
        with patch("backend.poller.poller_handler.save_fingerprints_batch") as mock_save_fp:
            with patch("backend.poller.poller_handler.LegistarFeed") as MockFeed:
                with patch("backend.poller.poller_handler._queue_meeting") as mock_queue:
                    with patch("backend.poller.poller_handler.load_budget") as mock_load_budget:
                        mock_load_budget.return_value = {"date": "2026-09-01", "spent": 0.0, "calls": 0}
                        mock_load_fp.return_value = {}

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

                        mock_queue.return_value = True

                        from backend.poller.poller_handler import handler
                        result = handler({}, None)

                        body = json.loads(result["body"])
                        assert body["new_meetings"] == 1
                        assert body["queued"] == 1

                        # SQS queue was called exactly once
                        assert mock_queue.call_count == 1


def test_max_cases_per_invocation_caps_at_3():
    """At most 3 cases are queued per poller invocation."""
    from backend.app.tools.legistar import Meeting

    with patch("backend.poller.poller_handler.load_fingerprints") as mock_load_fp:
        with patch("backend.poller.poller_handler.save_fingerprints_batch") as mock_save_fp:
            with patch("backend.poller.poller_handler.LegistarFeed") as MockFeed:
                with patch("backend.poller.poller_handler._queue_meeting") as mock_queue:
                    with patch("backend.poller.poller_handler.load_budget") as mock_load_budget:
                        mock_load_budget.return_value = {"date": "2026-09-01", "spent": 0.0, "calls": 0}
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

                        mock_queue.return_value = True

                        from backend.poller.poller_handler import handler
                        result = handler({}, None)

                        body = json.loads(result["body"])
                        # Only 3 queued (MAX_CASES_PER_INVOCATION)
                        assert body["queued"] == 3

                        # Verify queue was called exactly 3 times
                        assert mock_queue.call_count == 3


def test_fingerprints_persisted_after_changes():
    """Updated fingerprints are saved to DynamoDB after processing."""
    from backend.app.tools.legistar import Meeting

    with patch("backend.poller.poller_handler.load_fingerprints") as mock_load_fp:
        with patch("backend.poller.poller_handler.save_fingerprints_batch") as mock_save_fp:
            with patch("backend.poller.poller_handler.LegistarFeed") as MockFeed:
                with patch("backend.poller.poller_handler._queue_meeting") as mock_queue:
                    with patch("backend.poller.poller_handler.load_budget") as mock_load_budget:
                        mock_load_budget.return_value = {"date": "2026-09-01", "spent": 0.0, "calls": 0}
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

                        mock_queue.return_value = True

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


def test_budget_exceeded_skips_queueing():
    """Budget at cap: zero messages queued."""
    from backend.app.tools.legistar import Meeting

    with patch("backend.poller.poller_handler.load_fingerprints") as mock_load_fp:
        with patch("backend.poller.poller_handler.save_fingerprints_batch") as mock_save_fp:
            with patch("backend.poller.poller_handler.LegistarFeed") as MockFeed:
                with patch("backend.poller.poller_handler._queue_meeting") as mock_queue:
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
                            assert body["queued"] == 0
                            assert body["budget_skipped"] is True
                            assert body["budget_spent_usd"] == 1.50

                            # Queue was NEVER called
                            mock_queue.assert_not_called()

                            # Fingerprints still saved (so we don't reprocess)
                            mock_save_fp.assert_called_once()
