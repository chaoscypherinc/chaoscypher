# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Log Service.

Reads log files from disk and queries supervisord for service status.
"""

import collections
import http.client
import re
import socket
import time
import xmlrpc.client
from pathlib import Path
from typing import Any, cast

import structlog

from chaoscypher_cortex.features.logs.models import (
    LogResponse,
    ServiceStatus,
    ServiceStatusResponse,
)


logger = structlog.get_logger(__name__)

# Matches ISO timestamps at the start of log lines
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")


class LogService:
    """Reads log files and queries supervisord for service status.

    Args:
        log_dir: Path to the directory containing service log files.
        supervisor_socket: Path to the supervisord Unix socket.
        supervisor_username: Username for supervisord HTTP Basic Auth.
        supervisor_password: Password for supervisord HTTP Basic Auth.
        known_services: Service names whose logs can be viewed.
        max_log_lines: Maximum lines to read from a single log file.
    """

    def __init__(
        self,
        log_dir: str,
        supervisor_socket: str = "/run/chaoscypher/supervisor.sock",
        supervisor_username: str = "supervisor",
        supervisor_password: str = "",
        known_services: tuple[str, ...] | list[str] = ("cortex", "neuron", "nginx", "valkey"),
        max_log_lines: int = 10000,
    ) -> None:
        """Initialize with log directory and supervisord socket paths.

        Args:
            log_dir: Path to the directory containing service log files.
            supervisor_socket: Path to the supervisord Unix domain socket.
            supervisor_username: Username for supervisord HTTP Basic Auth.
            supervisor_password: Password for supervisord HTTP Basic Auth.
            known_services: Service names whose logs can be viewed.
            max_log_lines: Maximum lines to read from a single log file.
        """
        self._log_dir = Path(log_dir)
        self._supervisor_socket = supervisor_socket
        self._supervisor_username = supervisor_username
        self._supervisor_password = supervisor_password
        self._known_services = tuple(known_services)
        self._max_log_lines = max_log_lines

    def get_logs(self, service: str, lines: int) -> LogResponse:
        """Read the last N lines from a service's log file.

        Args:
            service: Service name (cortex, neuron, nginx, valkey).
            lines: Number of lines to return from the tail.

        Returns:
            LogResponse with the requested log lines.
        """
        if service not in self._known_services:
            return LogResponse(service=service, lines=[], total_lines=0)

        log_file = self._log_dir / f"{service}.log"
        if not log_file.exists():
            return LogResponse(service=service, lines=[], total_lines=0)

        all_lines = self._read_file_lines(log_file)
        # Strip [service] prefix from lines (added by log-prefix for startup page)
        stripped = [self._strip_service_prefix(line, service) for line in all_lines]
        tail = stripped[-lines:] if len(stripped) > lines else stripped

        return LogResponse(
            service=service,
            lines=tail,
            total_lines=len(all_lines),
        )

    def get_all_logs(self, lines: int) -> LogResponse:
        """Read and merge logs from all services, sorted by timestamp.

        Args:
            lines: Total number of lines to return.

        Returns:
            LogResponse with interleaved log lines from all services.
        """
        tagged_lines: list[tuple[str, str]] = []

        for service in self._known_services:
            log_file = self._log_dir / f"{service}.log"
            if not log_file.exists():
                continue

            for line in self._read_file_lines(log_file):
                clean = self._strip_service_prefix(line, service)
                ts = self._extract_timestamp(clean)
                tagged_line = f"[{service}] {clean}" if clean else ""
                tagged_lines.append((ts, tagged_line))

        tagged_lines.sort(key=lambda x: x[0])

        sorted_lines = [line for _, line in tagged_lines if line]
        tail = sorted_lines[-lines:] if len(sorted_lines) > lines else sorted_lines

        return LogResponse(
            service=None,
            lines=tail,
            total_lines=len(sorted_lines),
        )

    def get_service_status(self) -> ServiceStatusResponse:
        """Query supervisord for service status information.

        Returns:
            ServiceStatusResponse with status of all managed services.
            Returns available=False if supervisord is unreachable.
        """
        socket_path = Path(self._supervisor_socket)
        if not socket_path.exists():
            return ServiceStatusResponse(available=False, services=[])

        try:
            url = f"http://{self._supervisor_username}:{self._supervisor_password}@localhost/RPC2"
            proxy = xmlrpc.client.ServerProxy(
                url,
                transport=_UnixStreamTransport(self._supervisor_socket),
            )
            all_info = cast("list[dict[str, Any]]", proxy.supervisor.getAllProcessInfo())
        except Exception:
            logger.debug("supervisord_unreachable", socket=self._supervisor_socket)
            return ServiceStatusResponse(available=False, services=[])

        services: list[ServiceStatus] = []
        for info in all_info:
            name = info.get("name", "unknown")
            state_name = info.get("statename", "UNKNOWN")
            pid = info.get("pid", 0) or None
            start = info.get("start", 0)

            uptime = None
            start_time = None
            if start > 0:
                uptime = int(time.time() - start)
                start_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start))

            services.append(
                ServiceStatus(
                    name=name,
                    state=state_name,
                    pid=pid,
                    uptime_seconds=uptime,
                    start_time=start_time,
                    description=info.get("description", ""),
                )
            )

        return ServiceStatusResponse(available=True, services=services)

    def _read_file_lines(self, path: Path, max_lines: int | None = None) -> list[str]:
        """Read a file and return the last non-empty lines (memory-bounded).

        Args:
            path: Path to the log file.
            max_lines: Maximum number of lines to keep (tail). Defaults to
                the ``max_log_lines`` value provided at construction.

        Returns:
            List of non-empty lines from the file (at most *max_lines*).
        """
        if max_lines is None:
            max_lines = self._max_log_lines
        try:
            with open(path, errors="replace") as f:
                return list(
                    collections.deque(
                        (line.rstrip() for line in f if line.strip()),
                        maxlen=max_lines,
                    )
                )
        except OSError:
            logger.warning("log_read_failed", path=str(path))
            return []

    def _strip_service_prefix(self, line: str, service: str) -> str:
        """Strip [service] prefix added by log-prefix from a log line.

        Args:
            line: Log line that may start with [service] prefix.
            service: Service name to strip.

        Returns:
            Line with prefix removed, or original line if no prefix.

        """
        prefix = f"[{service}] "
        if line.startswith(prefix):
            return line[len(prefix) :]
        return line

    def _extract_timestamp(self, line: str) -> str:
        """Extract ISO timestamp from start of line for sorting.

        Args:
            line: Log line to extract timestamp from.

        Returns:
            Timestamp string, or empty string if not found.
        """
        match = _TIMESTAMP_RE.match(line)
        return match.group(1) if match else ""


class _UnixConnection(http.client.HTTPConnection):
    """HTTP connection over a Unix domain socket."""

    def __init__(self, socket_path: str) -> None:
        """Initialize with socket path.

        Args:
            socket_path: Path to the Unix domain socket.
        """
        super().__init__("localhost")
        self._socket_path = socket_path

    def connect(self) -> None:
        """Connect to the Unix domain socket."""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._socket_path)


class _UnixStreamTransport(xmlrpc.client.Transport):
    """XML-RPC transport over a Unix domain socket."""

    def __init__(self, socket_path: str) -> None:
        """Initialize with socket path.

        Args:
            socket_path: Path to the Unix domain socket.
        """
        super().__init__()
        self._socket_path = socket_path

    def make_connection(self, host: str) -> _UnixConnection:  # type: ignore[override]
        """Create a Unix socket HTTP connection.

        Args:
            host: Host string (ignored, uses Unix socket).

        Returns:
            Unix socket HTTP connection.
        """
        return _UnixConnection(self._socket_path)
