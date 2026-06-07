# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""Regression test for the Windows console UTF-8 reconfigure hook.

Background: ``chaoscypher db migrate status`` used to crash on Windows
with ``UnicodeEncodeError: 'charmap' codec can't encode character
'\\u2192'`` whenever a migration description contained the ``→`` glyph
(every FK-add migration does — e.g. "Add FK
extraction_submissions.source_id → sources.id"). Rich's
``LegacyWindowsTerm.write_text`` writes through ``sys.stdout``, and
Python's default ``sys.stdout.encoding`` on a Windows console is
``cp1252``, which has no mapping for U+2192.

The fix at ``chaoscypher_cli.__main__._configure_console_encoding``
reconfigures ``sys.stdout`` and ``sys.stderr`` to UTF-8 with
``errors="replace"`` at import time, before any Rich ``Console()`` is
constructed. These tests pin that hook so a future cleanup can't
accidentally remove it (the same way it ships green on POSIX CI and
crashes in production on Windows).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def test_reconfigure_called_on_win32() -> None:
    """On Windows, the hook reconfigures stdout AND stderr to UTF-8 / replace."""
    import sys

    from chaoscypher_cli.__main__ import _configure_console_encoding

    stdout_mock = MagicMock()
    stderr_mock = MagicMock()
    original_platform = sys.platform
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    sys.platform = "win32"  # type: ignore[misc]
    sys.stdout = stdout_mock  # type: ignore[assignment]
    sys.stderr = stderr_mock  # type: ignore[assignment]
    try:
        _configure_console_encoding()
    finally:
        sys.platform = original_platform  # type: ignore[misc]
        sys.stdout = original_stdout
        sys.stderr = original_stderr

    stdout_mock.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
    stderr_mock.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


def test_reconfigure_noops_on_posix() -> None:
    """On Linux/macOS, the hook MUST NOT touch stdout/stderr.

    POSIX shells default to UTF-8 and reconfiguring them is unnecessary
    risk (subtly changes behaviour for pipes that bind a specific
    encoding). The hook is a Windows-only patch.
    """
    import sys

    from chaoscypher_cli.__main__ import _configure_console_encoding

    stdout_mock = MagicMock()
    stderr_mock = MagicMock()
    original_platform = sys.platform
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    sys.platform = "linux"  # type: ignore[misc]
    sys.stdout = stdout_mock  # type: ignore[assignment]
    sys.stderr = stderr_mock  # type: ignore[assignment]
    try:
        _configure_console_encoding()
    finally:
        sys.platform = original_platform  # type: ignore[misc]
        sys.stdout = original_stdout
        sys.stderr = original_stderr

    stdout_mock.reconfigure.assert_not_called()
    stderr_mock.reconfigure.assert_not_called()


def test_reconfigure_swallows_attribute_error_when_stream_lacks_method() -> None:
    """Pytest capture replaces sys.stdout with a non-``TextIOWrapper`` that
    has no ``.reconfigure`` method. The hook must swallow that case — we
    will not refuse to start the CLI over a missing convenience method.

    Equally important: a failure on stdout must not prevent the hook from
    attempting stderr (the per-stream try is inside the loop, not around
    the whole block).
    """
    import sys

    from chaoscypher_cli.__main__ import _configure_console_encoding

    stdout_no_reconfigure: Any = object()  # bare object — no .reconfigure
    stderr_mock = MagicMock()
    original_platform = sys.platform
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    sys.platform = "win32"  # type: ignore[misc]
    sys.stdout = stdout_no_reconfigure
    sys.stderr = stderr_mock  # type: ignore[assignment]
    try:
        # Must not raise.
        _configure_console_encoding()
    finally:
        sys.platform = original_platform  # type: ignore[misc]
        sys.stdout = original_stdout
        sys.stderr = original_stderr

    # stderr was still attempted despite stdout failing — the loop's
    # per-stream try keeps the second stream from being collateral damage.
    stderr_mock.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


def test_reconfigure_swallows_os_error_during_reconfigure() -> None:
    """If the stream's reconfigure raises OSError (e.g. underlying file
    descriptor is closed or bound to a non-text mode), the hook must
    keep startup going.
    """
    import sys

    from chaoscypher_cli.__main__ import _configure_console_encoding

    stdout_mock = MagicMock()
    stdout_mock.reconfigure.side_effect = OSError("fd not text-mode")
    stderr_mock = MagicMock()
    original_platform = sys.platform
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    sys.platform = "win32"  # type: ignore[misc]
    sys.stdout = stdout_mock  # type: ignore[assignment]
    sys.stderr = stderr_mock  # type: ignore[assignment]
    try:
        _configure_console_encoding()
    finally:
        sys.platform = original_platform  # type: ignore[misc]
        sys.stdout = original_stdout
        sys.stderr = original_stderr

    # stdout failure was swallowed; stderr still got attempted.
    stderr_mock.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
