# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage-focused unit tests for LogService.

Targets the supervisord ``get_service_status`` XML-RPC path (mocked
proxy + socket) and the ``_read_file_lines`` tail/OSError handling that
the existing ``test_service.py`` does not exercise.

Also pins the 2026-08-06 P1 performance contract: ``_read_file_lines``
must read only the file's tail. It used to stream the whole file through a
bounded deque, so the Logs tab's 3-second poll re-read every byte of a day's
un-rotated log to return 200 lines.
"""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from chaoscypher_cortex.features.logs.service import LogService


_SERVICE_MODULE = "chaoscypher_cortex.features.logs.service"


class _CountingHandle:
    """File-object proxy that tallies every byte/char handed to the caller.

    Counts reads, readlines and iteration alike, so it measures the I/O a
    reader actually consumes regardless of how it walks the file.
    """

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped
        self.consumed = 0

    def _tally(self, data: Any) -> Any:
        self.consumed += len(data)
        return data

    def read(self, *args: Any, **kwargs: Any) -> Any:
        return self._tally(self._wrapped.read(*args, **kwargs))

    def readline(self, *args: Any, **kwargs: Any) -> Any:
        return self._tally(self._wrapped.readline(*args, **kwargs))

    def __iter__(self) -> Any:
        for line in self._wrapped:
            yield self._tally(line)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def __enter__(self) -> _CountingHandle:
        self._wrapped.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        return self._wrapped.__exit__(*args)


# ---------------------------------------------------------------------------
# get_service_status — success path (mocked xmlrpc proxy + socket exists)
# ---------------------------------------------------------------------------


def test_get_service_status_returns_running_services(tmp_path: Path) -> None:
    """A reachable supervisord yields parsed ServiceStatus rows with uptime."""
    sock = tmp_path / "supervisor.sock"
    sock.write_text("")  # make socket_path.exists() True

    proxy = MagicMock()
    proxy.supervisor.getAllProcessInfo.return_value = [
        {
            "name": "cortex",
            "statename": "RUNNING",
            "pid": 1234,
            "start": 1_000_000,  # > 0 -> uptime + start_time computed
            "description": "pid 1234, uptime 0:10:00",
        }
    ]

    service = LogService(log_dir=str(tmp_path), supervisor_socket=str(sock))

    with (
        patch(f"{_SERVICE_MODULE}.xmlrpc.client.ServerProxy", return_value=proxy),
        patch(f"{_SERVICE_MODULE}._UnixStreamTransport"),
        patch(f"{_SERVICE_MODULE}.time.time", return_value=1_000_600),
    ):
        response = service.get_service_status()

    assert response.available is True
    assert len(response.services) == 1
    svc = response.services[0]
    assert svc.name == "cortex"
    assert svc.state == "RUNNING"
    assert svc.pid == 1234
    assert svc.uptime_seconds == 600
    assert svc.start_time is not None


def test_get_service_status_handles_stopped_service_without_start(tmp_path: Path) -> None:
    """A service with start==0 has no uptime/start_time and pid coerced to None."""
    sock = tmp_path / "supervisor.sock"
    sock.write_text("")

    proxy = MagicMock()
    proxy.supervisor.getAllProcessInfo.return_value = [
        {
            "name": "neuron",
            "statename": "STOPPED",
            "pid": 0,  # 0 -> None
            "start": 0,  # not > 0 -> no uptime
            "description": "",
        }
    ]

    service = LogService(log_dir=str(tmp_path), supervisor_socket=str(sock))

    with (
        patch(f"{_SERVICE_MODULE}.xmlrpc.client.ServerProxy", return_value=proxy),
        patch(f"{_SERVICE_MODULE}._UnixStreamTransport"),
    ):
        response = service.get_service_status()

    assert response.available is True
    svc = response.services[0]
    assert svc.pid is None
    assert svc.uptime_seconds is None
    assert svc.start_time is None


def test_get_service_status_uses_defaults_for_missing_fields(tmp_path: Path) -> None:
    """Missing keys fall back to the 'unknown'/'UNKNOWN' defaults."""
    sock = tmp_path / "supervisor.sock"
    sock.write_text("")

    proxy = MagicMock()
    proxy.supervisor.getAllProcessInfo.return_value = [{}]  # empty info dict

    service = LogService(log_dir=str(tmp_path), supervisor_socket=str(sock))

    with (
        patch(f"{_SERVICE_MODULE}.xmlrpc.client.ServerProxy", return_value=proxy),
        patch(f"{_SERVICE_MODULE}._UnixStreamTransport"),
    ):
        response = service.get_service_status()

    svc = response.services[0]
    assert svc.name == "unknown"
    assert svc.state == "UNKNOWN"


def test_get_service_status_returns_unavailable_on_proxy_error(tmp_path: Path) -> None:
    """An exception talking to supervisord yields available=False."""
    sock = tmp_path / "supervisor.sock"
    sock.write_text("")

    service = LogService(log_dir=str(tmp_path), supervisor_socket=str(sock))

    with (
        patch(
            f"{_SERVICE_MODULE}.xmlrpc.client.ServerProxy",
            side_effect=OSError("connection refused"),
        ),
        patch(f"{_SERVICE_MODULE}._UnixStreamTransport"),
    ):
        response = service.get_service_status()

    assert response.available is False
    assert response.services == []


# ---------------------------------------------------------------------------
# _read_file_lines — tail bound, empty-line skip, OSError handling
# ---------------------------------------------------------------------------


def test_read_file_lines_skips_blank_lines_and_strips(tmp_path: Path) -> None:
    """Blank lines are dropped and trailing whitespace is stripped."""
    log = tmp_path / "cortex.log"
    log.write_text("first  \n\n   \nsecond\n")

    service = LogService(log_dir=str(tmp_path))
    lines = service._read_file_lines(log)

    assert lines == ["first", "second"]


def test_read_file_lines_respects_max_lines_tail(tmp_path: Path) -> None:
    """Only the last ``max_lines`` non-empty lines are retained."""
    log = tmp_path / "cortex.log"
    log.write_text("\n".join(f"line{i}" for i in range(20)) + "\n")

    service = LogService(log_dir=str(tmp_path), max_log_lines=5)
    lines = service._read_file_lines(log)

    assert len(lines) == 5
    assert lines[0] == "line15"
    assert lines[-1] == "line19"


def test_read_file_lines_explicit_max_lines_override(tmp_path: Path) -> None:
    """An explicit max_lines argument overrides the construction default."""
    log = tmp_path / "cortex.log"
    log.write_text("\n".join(f"line{i}" for i in range(10)) + "\n")

    service = LogService(log_dir=str(tmp_path), max_log_lines=100)
    lines = service._read_file_lines(log, max_lines=3)

    assert lines == ["line7", "line8", "line9"]


def test_read_file_lines_returns_empty_on_oserror(tmp_path: Path) -> None:
    """An OSError while opening the file yields an empty list (logged)."""
    service = LogService(log_dir=str(tmp_path))
    missing = tmp_path / "does-not-exist.log"

    lines = service._read_file_lines(missing)

    assert lines == []


# ---------------------------------------------------------------------------
# _read_file_lines — bounded tail read (does not touch the whole file)
# ---------------------------------------------------------------------------


def _write_big_log(path: Path, line_count: int = 60_000) -> int:
    """Write a log far larger than any tail window; return its byte size."""
    path.write_text(
        "".join(
            f"2026-08-12T09:00:00 filler line {i:06d} padded out a bit\n" for i in range(line_count)
        )
    )
    return path.stat().st_size


def test_read_file_lines_reads_only_the_tail_of_a_large_file(tmp_path: Path) -> None:
    """A 10-line tail must not consume the whole file."""
    log = tmp_path / "cortex.log"
    size = _write_big_log(log)
    assert size > 1_000_000, "fixture must be large enough for the ratio to be meaningful"

    service = LogService(log_dir=str(tmp_path), max_log_lines=10)

    handles: list[_CountingHandle] = []
    real_open = builtins.open

    def _tracking_open(*args: Any, **kwargs: Any) -> Any:
        handle = _CountingHandle(real_open(*args, **kwargs))
        handles.append(handle)
        return handle

    with patch.object(builtins, "open", _tracking_open):
        lines = service._read_file_lines(log)

    consumed = sum(handle.consumed for handle in handles)
    assert consumed < size // 10, (
        f"read {consumed} of {size} bytes to return 10 lines — the tail read is "
        "still walking the whole file"
    )
    assert len(lines) == 10
    assert lines[-1] == "2026-08-12T09:00:00 filler line 059999 padded out a bit"
    assert lines[0] == "2026-08-12T09:00:00 filler line 059990 padded out a bit"


def test_read_file_lines_tail_matches_a_full_read(tmp_path: Path) -> None:
    """The bounded reader returns exactly the last N lines of a multi-block file."""
    log = tmp_path / "cortex.log"
    expected = [f"2026-08-12T09:00:00 line {i:05d}" for i in range(20_000)]
    log.write_text("\n".join(expected) + "\n")

    service = LogService(log_dir=str(tmp_path), max_log_lines=137)

    assert service._read_file_lines(log) == expected[-137:]


def test_read_file_lines_stitches_lines_across_block_boundaries(tmp_path: Path) -> None:
    """A window spanning many reverse-read blocks reassembles split lines correctly."""
    log = tmp_path / "cortex.log"
    expected = [f"2026-08-12T09:00:00 padded log line number {i:06d} tail" for i in range(30_000)]
    log.write_text("\n".join(expected) + "\n")
    # ~5,000 lines x ~55 bytes is several 64 KiB blocks, so the partial-line
    # carry between blocks is exercised rather than short-circuited.
    service = LogService(log_dir=str(tmp_path), max_log_lines=5_000)

    assert service._read_file_lines(log) == expected[-5_000:]


def test_read_file_lines_matches_full_read_at_tiny_block_sizes(tmp_path: Path) -> None:
    """Shrinking the block size to a few bytes must not change the result.

    Forces a block boundary inside almost every line, which is where a
    reverse-chunk reader loses or duplicates lines if the carry is wrong.
    """
    log = tmp_path / "cortex.log"
    expected = [f"line-{i:03d}" for i in range(200)]
    log.write_text("\n".join(expected) + "\n")

    for block_size in (1, 2, 3, 7, 13, 64):
        with patch(f"{_SERVICE_MODULE}._TAIL_BLOCK_SIZE", block_size):
            service = LogService(log_dir=str(tmp_path), max_log_lines=17)
            assert service._read_file_lines(log) == expected[-17:], (
                f"block size {block_size} changed the tail"
            )
            service_all = LogService(log_dir=str(tmp_path), max_log_lines=1_000)
            assert service_all._read_file_lines(log) == expected, (
                f"block size {block_size} changed the full read"
            )


def test_read_file_lines_handles_file_without_trailing_newline(tmp_path: Path) -> None:
    """A final line with no newline is still returned."""
    log = tmp_path / "cortex.log"
    log.write_text("alpha\nbeta\ngamma")

    service = LogService(log_dir=str(tmp_path), max_log_lines=2)

    assert service._read_file_lines(log) == ["beta", "gamma"]


def test_read_file_lines_returns_whole_file_when_shorter_than_window(tmp_path: Path) -> None:
    """A file with fewer lines than the window is returned whole."""
    log = tmp_path / "cortex.log"
    log.write_text("alpha\nbeta\n")

    service = LogService(log_dir=str(tmp_path), max_log_lines=500)

    assert service._read_file_lines(log) == ["alpha", "beta"]


def test_read_file_lines_skips_blank_lines_beyond_the_window(tmp_path: Path) -> None:
    """Blank lines never consume tail slots — the window counts real lines."""
    log = tmp_path / "cortex.log"
    log.write_text("alpha\n\n\nbeta\n\n\ngamma\n\n\n")

    service = LogService(log_dir=str(tmp_path), max_log_lines=2)

    assert service._read_file_lines(log) == ["beta", "gamma"]


def test_read_file_lines_returns_empty_for_empty_file(tmp_path: Path) -> None:
    """An empty file yields no lines rather than a spurious blank entry."""
    log = tmp_path / "cortex.log"
    log.write_text("")

    service = LogService(log_dir=str(tmp_path), max_log_lines=10)

    assert service._read_file_lines(log) == []


def test_read_file_lines_replaces_undecodable_bytes(tmp_path: Path) -> None:
    """``errors="replace"`` behaviour survives the switch to a binary tail read."""
    log = tmp_path / "cortex.log"
    log.write_bytes(b"good line\nbad \xff\xfe byte line\n")

    service = LogService(log_dir=str(tmp_path), max_log_lines=10)
    lines = service._read_file_lines(log)

    assert len(lines) == 2
    assert lines[0] == "good line"
    assert lines[1].startswith("bad ")


# ---------------------------------------------------------------------------
# get_all_logs — prefix-strip + re-tag, and tail truncation across services
# ---------------------------------------------------------------------------


def test_get_all_logs_strips_existing_prefix_before_retagging(tmp_path: Path) -> None:
    """An already-prefixed line is stripped then re-tagged once (no double prefix)."""
    log_dir = tmp_path
    # Line already carries the [cortex] prefix; it must not become [cortex] [cortex].
    (log_dir / "cortex.log").write_text("[cortex] 2026-04-03T14:00:01 startup done\n")

    service = LogService(log_dir=str(log_dir))
    response = service.get_all_logs(lines=10)

    assert response.service is None
    assert len(response.lines) == 1
    assert response.lines[0] == "[cortex] 2026-04-03T14:00:01 startup done"


def test_get_all_logs_tail_truncates_merged_output(tmp_path: Path) -> None:
    """The merged, timestamp-sorted output is tailed to the requested count."""
    log_dir = tmp_path
    (log_dir / "cortex.log").write_text(
        "\n".join(f"2026-04-03T14:00:0{i} cortex line {i}" for i in range(5)) + "\n"
    )
    (log_dir / "neuron.log").write_text(
        "\n".join(f"2026-04-03T14:00:1{i} neuron line {i}" for i in range(5)) + "\n"
    )

    service = LogService(log_dir=str(log_dir))
    response = service.get_all_logs(lines=3)

    # 10 total lines merged; only the last 3 (latest timestamps) returned.
    assert response.total_lines == 10
    assert len(response.lines) == 3
    assert all("neuron" in line for line in response.lines)


def test_get_all_logs_interleaves_four_services_by_timestamp(tmp_path: Path) -> None:
    """Four service tails merge into one timestamp-ordered stream."""
    log_dir = tmp_path
    # Each service logs one line per minute, offset so the merged order
    # round-robins cortex -> neuron -> nginx -> valkey.
    offsets = {"cortex": 0, "neuron": 15, "nginx": 30, "valkey": 45}
    for service, second in offsets.items():
        (log_dir / f"{service}.log").write_text(
            "\n".join(f"2026-08-12T09:0{minute}:{second:02d} {service} tick" for minute in range(3))
            + "\n"
        )

    service = LogService(log_dir=str(log_dir))
    response = service.get_all_logs(lines=12)

    assert response.total_lines == 12
    assert [line.split("]")[0].lstrip("[") for line in response.lines] == [
        "cortex",
        "neuron",
        "nginx",
        "valkey",
    ] * 3


def test_get_all_logs_tail_is_the_globally_latest_lines(tmp_path: Path) -> None:
    """The returned tail is the latest across services, not per-file tails."""
    log_dir = tmp_path
    # cortex holds the three newest lines; neuron's are all older.
    (log_dir / "cortex.log").write_text(
        "\n".join(f"2026-08-12T09:05:0{i} cortex late {i}" for i in range(3)) + "\n"
    )
    (log_dir / "neuron.log").write_text(
        "\n".join(f"2026-08-12T09:00:0{i} neuron early {i}" for i in range(3)) + "\n"
    )

    service = LogService(log_dir=str(log_dir))
    response = service.get_all_logs(lines=3)

    assert response.lines == [
        "[cortex] 2026-08-12T09:05:00 cortex late 0",
        "[cortex] 2026-08-12T09:05:01 cortex late 1",
        "[cortex] 2026-08-12T09:05:02 cortex late 2",
    ]
