# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage-focused unit tests for LogService.

Targets the supervisord ``get_service_status`` XML-RPC path (mocked
proxy + socket) and the ``_read_file_lines`` tail/OSError handling that
the existing ``test_service.py`` does not exercise.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from chaoscypher_cortex.features.logs.service import LogService


_SERVICE_MODULE = "chaoscypher_cortex.features.logs.service"


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
