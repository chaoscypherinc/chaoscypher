# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Client-facing benchmark errors and their stable codes."""

from __future__ import annotations

from typing import Any


ERR_INVALID_ARGUMENT = "INVALID_ARGUMENT"
ERR_UNKNOWN_SUITE = "UNKNOWN_SUITE"
ERR_UNKNOWN_RUN = "UNKNOWN_RUN"
ERR_UNKNOWN_PROBE = "UNKNOWN_PROBE"
ERR_UNKNOWN_TASK = ERR_UNKNOWN_PROBE
"""A task id not in the run. Every task is a probe (the chat questions are chat probes)."""
ERR_UNKNOWN_REFERENCE = "UNKNOWN_REFERENCE"
ERR_OUT_OF_ORDER = "OUT_OF_ORDER"
ERR_TASK_FINAL = "TASK_FINAL"
"""A stage (or the whole task) was already answered; a run scores the first answer."""
ERR_RUN_FINISHED = "RUN_FINISHED"
"""The run was finished; it takes no more answers."""
ERR_EMPTY_OUTPUT = "EMPTY_OUTPUT"
ERR_INCOMPLETE_RUN = "INCOMPLETE_RUN"
ERR_INTERNAL = "INTERNAL_ERROR"


class BenchmarkError(Exception):
    """A client-facing benchmark error with a stable ``error_code``."""

    def __init__(self, code: str, message: str) -> None:
        """Store the error code alongside the message."""
        super().__init__(message)
        self.code = code


def error_payload(code: str, message: str) -> dict[str, Any]:
    """Build the JSON error payload every tool returns on failure."""
    return {"success": False, "error_code": code, "error": message}


__all__ = [
    "ERR_EMPTY_OUTPUT",
    "ERR_INCOMPLETE_RUN",
    "ERR_INTERNAL",
    "ERR_INVALID_ARGUMENT",
    "ERR_OUT_OF_ORDER",
    "ERR_RUN_FINISHED",
    "ERR_TASK_FINAL",
    "ERR_UNKNOWN_PROBE",
    "ERR_UNKNOWN_REFERENCE",
    "ERR_UNKNOWN_RUN",
    "ERR_UNKNOWN_SUITE",
    "ERR_UNKNOWN_TASK",
    "BenchmarkError",
    "error_payload",
]
