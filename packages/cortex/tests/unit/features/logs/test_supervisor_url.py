# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Supervisord XML-RPC credentials must be URL-quoted.

The RPC URL was built with raw f-string interpolation — a ``@`` or ``:``
in the password corrupted the authority component (everything after the
``@`` was parsed as the host).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from chaoscypher_cortex.features.logs.service import LogService


if TYPE_CHECKING:
    from pathlib import Path


def test_supervisor_credentials_are_url_quoted(tmp_path: Path) -> None:
    """A password containing '@' and ':' produces a valid, quoted URL."""
    socket_file = tmp_path / "supervisor.sock"
    socket_file.touch()

    service = LogService(
        log_dir=str(tmp_path),
        supervisor_socket=str(socket_file),
        supervisor_username="supervisor",
        supervisor_password="p@ss:word",
    )

    proxy = MagicMock()
    proxy.supervisor.getAllProcessInfo.return_value = []

    with patch("xmlrpc.client.ServerProxy", return_value=proxy) as mock_proxy_cls:
        response = service.get_service_status()

    assert response.available is True
    url = mock_proxy_cls.call_args[0][0]
    assert url == "http://supervisor:p%40ss%3Aword@localhost/RPC2"
