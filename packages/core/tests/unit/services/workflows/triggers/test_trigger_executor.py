# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TriggerExecutor pure functions."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.workflows.triggers.engine.executor import (
    TriggerExecutor,
    _make_json_safe,
)


# ============================================================================
# _make_json_safe
# ============================================================================


class TestMakeJsonSafe:
    """Tests for the _make_json_safe utility function."""

    def test_converts_datetime_to_isoformat(self) -> None:
        dt = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
        assert _make_json_safe(dt) == "2026-03-23T12:00:00+00:00"

    def test_recurses_into_dicts(self) -> None:
        data = {
            "key": datetime(2026, 1, 1, tzinfo=UTC),
            "nested": {"dt": datetime(2026, 6, 1, tzinfo=UTC)},
        }
        result = _make_json_safe(data)
        assert isinstance(result["key"], str)
        assert isinstance(result["nested"]["dt"], str)

    def test_recurses_into_lists(self) -> None:
        data = [datetime(2026, 1, 1, tzinfo=UTC), "text", 42]
        result = _make_json_safe(data)
        assert isinstance(result[0], str)
        assert result[1] == "text"
        assert result[2] == 42

    def test_passes_through_primitives(self) -> None:
        assert _make_json_safe("hello") == "hello"
        assert _make_json_safe(42) == 42
        assert _make_json_safe(3.14) == 3.14
        assert _make_json_safe(True) is True
        assert _make_json_safe(None) is None

    def test_converts_unknown_types_to_str(self) -> None:
        result = _make_json_safe(object())
        assert isinstance(result, str)


# ============================================================================
# _filters_match
# ============================================================================


class TestFiltersMatch:
    """Tests for TriggerExecutor._filters_match."""

    @pytest.fixture
    def executor(self):
        """Create a minimal TriggerExecutor for testing _filters_match."""
        return TriggerExecutor(
            trigger_service=MagicMock(),
            workflow_service=MagicMock(),
            tool_service=MagicMock(),
            llm_service=MagicMock(),
            graph_repository=MagicMock(),
            search_repository=MagicMock(),
            database_name="test_db",
            execute_workflow_fn=MagicMock(),
        )

    def test_empty_filters_match_everything(self, executor) -> None:
        assert executor._filters_match({}, {"any": "data"}) is True

    def test_exact_match(self, executor) -> None:
        assert executor._filters_match({"file_type": "pdf"}, {"file_type": "pdf"}) is True

    def test_wrong_value_no_match(self, executor) -> None:
        assert executor._filters_match({"file_type": "pdf"}, {"file_type": "csv"}) is False

    def test_missing_key_no_match(self, executor) -> None:
        assert executor._filters_match({"file_type": "pdf"}, {"other": "data"}) is False

    def test_multiple_filters_all_must_match(self, executor) -> None:
        filters = {"file_type": "pdf", "size": "large"}
        assert executor._filters_match(filters, {"file_type": "pdf", "size": "large"}) is True
        assert executor._filters_match(filters, {"file_type": "pdf", "size": "small"}) is False


# ============================================================================
# _filter_has_unknown_keys — silent-fail detection
# ============================================================================


class TestFilterHasUnknownKeys:
    """Tests for ``TriggerExecutor._filter_has_unknown_keys``.

    A filter key absent from the event payload can never match — the trigger
    is structurally guaranteed never to fire on these events. This is
    distinct from a value mismatch (the key is present, the value differs),
    which is normal filtering. The helper reports the silent-fail set so
    the caller can log a warning operators will actually see.
    """

    @pytest.fixture
    def executor(self):
        return TriggerExecutor(
            trigger_service=MagicMock(),
            workflow_service=MagicMock(),
            tool_service=MagicMock(),
            llm_service=MagicMock(),
            graph_repository=MagicMock(),
            search_repository=MagicMock(),
            database_name="test_db",
            execute_workflow_fn=MagicMock(),
        )

    def test_returns_empty_when_all_keys_present(self, executor) -> None:
        unknown = executor._filter_has_unknown_keys(
            {"entity_type": "node"}, {"entity_type": "edge", "entity_id": "n1"}
        )
        assert unknown == set()

    def test_returns_keys_absent_from_event_payload(self, executor) -> None:
        unknown = executor._filter_has_unknown_keys(
            {"file_type": "pdf"}, {"entity_type": "node", "entity_id": "n1"}
        )
        assert unknown == {"file_type"}

    def test_returns_all_keys_when_none_match(self, executor) -> None:
        unknown = executor._filter_has_unknown_keys({"foo": 1, "bar": 2}, {"baz": 3})
        assert unknown == {"foo", "bar"}

    def test_empty_filters_yield_empty(self, executor) -> None:
        assert executor._filter_has_unknown_keys({}, {"any": "data"}) == set()


# ============================================================================
# _handle_event — silent-fail surfacing via WARNING log
# ============================================================================


class TestHandleEventLogsSilentFailure:
    """Integration: when a trigger's filter mentions a key absent from the
    event payload, ``_handle_event`` must emit a WARNING the operator can see.

    Distinct from value-mismatch (a normal filter that just doesn't apply
    to this event), which stays at DEBUG. The WARNING surfaces the
    silent-fail vector identified in the 2026-05-07 contract-drift audit:
    a misconfigured trigger that's structurally guaranteed to never fire.
    """

    @pytest.fixture
    def executor(self):
        return TriggerExecutor(
            trigger_service=MagicMock(),
            workflow_service=MagicMock(),
            tool_service=MagicMock(),
            llm_service=MagicMock(),
            graph_repository=MagicMock(),
            search_repository=MagicMock(),
            database_name="test_db",
            execute_workflow_fn=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_warning_when_filter_key_absent_from_event(self, executor) -> None:
        """Operator-visible warning: filter mentions a key the event never carries.

        We patch the module-level structlog logger directly because the
        codebase's structlog configuration uses ``WriteLoggerFactory`` (emits
        straight to stderr) rather than routing through stdlib ``logging``,
        so ``caplog`` does not see these records.
        """
        executor.trigger_service.list_triggers = MagicMock(
            return_value=[
                {
                    "id": "trg_1",
                    "name": "Mistuned trigger",
                    "filters": {"file_type": "pdf"},  # not in node.create payload
                    "workflow_id": "wf_1",
                },
            ]
        )

        with patch(
            "chaoscypher_core.services.workflows.triggers.engine.executor.logger"
        ) as mock_logger:
            await executor._handle_event(
                {
                    "source": "node.create",
                    "data": {"entity_type": "node", "entity_id": "n1"},
                }
            )

            mock_logger.warning.assert_called_once()
            event_name = mock_logger.warning.call_args.args[0]
            assert event_name == "trigger_filter_unknown_keys"
            kwargs = mock_logger.warning.call_args.kwargs
            assert kwargs["unknown_keys"] == ["file_type"]
            assert kwargs["event_source"] == "node.create"
            assert kwargs["event_payload_keys"] == ["entity_id", "entity_type"]
            assert kwargs["trigger_id"] == "trg_1"
            assert "hint" in kwargs

    @pytest.mark.asyncio
    async def test_no_warning_when_filter_key_present_but_value_differs(self, executor) -> None:
        """Value mismatch is normal filtering — must log DEBUG, not WARNING."""
        executor.trigger_service.list_triggers = MagicMock(
            return_value=[
                {
                    "id": "trg_2",
                    "name": "Edge-only trigger",
                    "filters": {"entity_type": "edge"},  # key exists; value differs
                    "workflow_id": "wf_1",
                },
            ]
        )

        with patch(
            "chaoscypher_core.services.workflows.triggers.engine.executor.logger"
        ) as mock_logger:
            await executor._handle_event(
                {
                    "source": "node.create",
                    "data": {"entity_type": "node", "entity_id": "n1"},
                }
            )

            # No silent-fail warning on a value-mismatch filter.
            absent_warnings = [
                c
                for c in mock_logger.warning.call_args_list
                if c.args and c.args[0] == "trigger_filter_unknown_keys"
            ]
            assert not absent_warnings, (
                "Value-mismatch filtering must not emit the silent-fail warning; "
                "got: " + str(absent_warnings)
            )

            # The DEBUG path should still fire (existing behavior preserved).
            debug_calls = [
                c
                for c in mock_logger.debug.call_args_list
                if c.args and c.args[0] == "trigger_filters_not_matched"
            ]
            assert debug_calls, (
                "Value-mismatch filtering must still log 'trigger_filters_not_matched' "
                "at DEBUG (existing behavior). Got debug calls: "
                f"{mock_logger.debug.call_args_list}"
            )


# ============================================================================
# publish_event_sync
# ============================================================================


class TestPublishEventSync:
    """Tests for TriggerExecutor.publish_event_sync."""

    @pytest.fixture
    def executor(self):
        """Create a minimal TriggerExecutor."""
        return TriggerExecutor(
            trigger_service=MagicMock(),
            workflow_service=MagicMock(),
            tool_service=MagicMock(),
            llm_service=MagicMock(),
            graph_repository=MagicMock(),
            search_repository=MagicMock(),
            database_name="test_db",
            execute_workflow_fn=MagicMock(),
        )

    def test_puts_event_on_queue(self, executor) -> None:
        executor.publish_event_sync("node.create", {"node_id": "n1"})
        assert executor.event_queue.qsize() == 1
        event = executor.event_queue.get_nowait()
        assert event["source"] == "node.create"
        assert event["data"]["node_id"] == "n1"
        assert "timestamp" in event

    def test_serializes_datetime_in_data(self, executor) -> None:
        dt = datetime(2026, 3, 23, tzinfo=UTC)
        executor.publish_event_sync("test", {"created_at": dt})
        event = executor.event_queue.get_nowait()
        assert isinstance(event["data"]["created_at"], str)

    def test_handles_multiple_events(self, executor) -> None:
        executor.publish_event_sync("event.a", {"a": 1})
        executor.publish_event_sync("event.b", {"b": 2})
        assert executor.event_queue.qsize() == 2
