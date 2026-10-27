# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Logs written while a memory is handled carry no exception text (spec 2026-10-24 D16).

A model's or a database's error can quote what it was given, and so the memory. Inside
``private_text()`` the log processor withholds a record's traceback and its error-text
fields at INFO and above; DEBUG keeps them for a person debugging on purpose.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import structlog

from chaoscypher_core.models import MemoryLinkState
from chaoscypher_core.utils.logging.privacy import (
    SAFE_KEYS,
    SAFE_SUFFIXES,
    WITHHELD,
    log_failure,
    private_text,
    withhold_private_text,
)


def _event(**fields: Any) -> dict[str, Any]:
    return {"event": "x_failed", **fields}


def test_inside_private_text_an_error_keeps_its_type_and_loses_its_text() -> None:
    error = ValueError("cannot parse: Pierre Bezukhov moved to Lyon.")
    with private_text():
        out = withhold_private_text(
            None,
            "error",
            _event(
                error_type="ValueError", error_message=str(error), error=str(error), exc_info=error
            ),
        )
    assert out == {
        "event": "x_failed",
        "error_type": "ValueError",
        "error_message": WITHHELD,
        "error": WITHHELD,
    }


def test_inside_private_text_debug_keeps_the_detail() -> None:
    error = ValueError("cannot parse: Pierre")
    with private_text():
        out = withhold_private_text(None, "debug", _event(error_message="Pierre", exc_info=error))
    assert out == _event(error_message="Pierre", exc_info=error)


def test_outside_private_text_nothing_is_withheld() -> None:
    error = ValueError("a document's error")
    out = withhold_private_text(None, "exception", _event(error_message=str(error), exc_info=True))
    assert out == _event(error_message=str(error), exc_info=True)


def test_log_failure_inside_private_text_logs_the_type_and_the_traceback_at_debug() -> None:
    logger = structlog.get_logger("test")
    error = ValueError("cannot parse: Pierre Bezukhov")
    with structlog.testing.capture_logs() as logs, private_text():
        log_failure(logger, "x_failed", error, stage="extraction")
    assert logs == [
        {
            "event": "x_failed",
            "error_type": "ValueError",
            "stage": "extraction",
            "log_level": "error",
        },
        {"event": "x_failed_detail", "exc_info": error, "log_level": "debug"},
    ]


def test_log_failure_outside_private_text_logs_the_message_and_the_traceback() -> None:
    logger = structlog.get_logger("test")
    error = ValueError("a document's error")
    with structlog.testing.capture_logs() as logs:
        log_failure(logger, "x_failed", error, level="warning")
    assert logs == [
        {
            "event": "x_failed",
            "error_type": "ValueError",
            "error_message": "a document's error",
            "exc_info": error,
            "log_level": "warning",
        }
    ]


def test_inside_private_text_only_allow_listed_keys_keep_their_text() -> None:
    """Above DEBUG every string value outside the allow-list is withheld, wherever it comes from."""
    with private_text():
        out = withhold_private_text(
            None,
            "warning",
            {
                "event": "relationship_line_rejected",
                "level": "warning",
                "timestamp": "2026-10-25T00:00:00Z",
                "logger": "chaoscypher_core.x",
                "filename": "line_parser.py",
                "func_name": "parse",
                "lineno": 427,
                "source_id": "a1b2",
                "node_ids": ["n1", "n2"],
                "entity_count": 3,
                "duration_ms": 12.5,
                "stage": "extraction",
                "error_type": "LLMError",
                "strict": True,
                "line": "E|Pierre Bezukhov|Person",
                "justification": "Pierre moved to Lyon",
                "entity_names": ["Pierre Bezukhov", "Lyon"],
                "preview": '{"name": "Pierre"}',
                "label": "Lyon",
                "fail_exception_message": "cannot read: Pierre",
                "details": {"name": "Pierre"},
                "sizes": [1, 2],
                "nothing": None,
                "_record": object,
                "_from_structlog": True,
            },
        )
    assert out == {
        "event": "relationship_line_rejected",
        "level": "warning",
        "timestamp": "2026-10-25T00:00:00Z",
        "logger": "chaoscypher_core.x",
        "filename": "line_parser.py",
        "func_name": "parse",
        "lineno": 427,
        "source_id": "a1b2",
        "node_ids": ["n1", "n2"],
        "entity_count": 3,
        "duration_ms": 12.5,
        "stage": "extraction",
        "error_type": "LLMError",
        "strict": True,
        "line": WITHHELD,
        "justification": WITHHELD,
        "entity_names": WITHHELD,
        "preview": WITHHELD,
        "label": WITHHELD,
        "fail_exception_message": WITHHELD,
        "details": WITHHELD,
        "sizes": [1, 2],
        "nothing": None,
        "_record": object,
        "_from_structlog": True,
    }


def test_an_enum_value_is_a_fixed_string_and_keeps_its_text() -> None:
    with private_text():
        out = withhold_private_text(None, "info", _event(reason=MemoryLinkState.NO_MODEL))
    assert out == _event(reason=MemoryLinkState.NO_MODEL)


def test_the_allow_list_is_pinned() -> None:
    """A key added to the allow-list is a decision to log its text: this test names each one."""
    assert {
        "event",
        "level",
        "log_level",
        "logger",
        "logger_name",
        "timestamp",
        "filename",
        "func_name",
        "lineno",
        "module",
        "pathname",
        "process",
        "process_name",
        "thread",
        "thread_name",
        "stage",
        "state",
        "status",
        "outcome",
        "error_type",
        "database_name",
        "operation",
        "queue",
        "provider",
        "model",
        "mode",
        "phase",
        "step",
        "attempt",
        "attempts",
    } == SAFE_KEYS
    assert SAFE_SUFFIXES == (
        "_id",
        "_ids",
        "_count",
        "_type",
        "_ms",
        "_seconds",
        "_length",
        "_len",
        "_size",
        "_bytes",
        "_number",
        "_index",
        "_stage",
        "_state",
        "_status",
    )


def test_a_stdlib_record_keeps_its_template_not_its_arguments() -> None:
    """``logging``'s own records carry the formatted message in ``event``: inside, the template."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.KeyValueRenderer(key_order=["event"]),
            foreign_pre_chain=[structlog.stdlib.add_log_level, withhold_private_text],
        )
    )
    logger = logging.getLogger("test.private_text.stdlib")
    logger.addHandler(handler)
    logger.propagate = False
    error = ValueError("cannot parse: Pierre Bezukhov")
    try:
        with private_text():
            try:
                raise error
            except ValueError as e:
                logger.exception("b %s", e)  # noqa: TRY401 - the message quoting the error is the case
        logger.warning("outside %s", "Anna")
    finally:
        logger.removeHandler(handler)
    inside, outside = stream.getvalue().splitlines()
    assert inside.startswith("event='test.private_text.stdlib: b %s'")
    assert "Pierre" not in inside and "Traceback" not in stream.getvalue().split(outside)[0]
    assert outside.startswith("event='outside Anna'")
