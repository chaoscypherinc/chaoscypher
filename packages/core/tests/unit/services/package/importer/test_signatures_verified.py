# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The importer verifies a package's signatures before it writes anything (spec 2026-10-20, slice 7).

A package carrying a ``signatures`` entry is imported only when every signature verifies over
the manifest bytes it holds; a signed manifest changed after signing is refused with
``ValidationError``. A package with no ``signatures`` entry verifies nothing and imports.
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions


TITLE = b"signed pack"
TAMPERED = b"signed pacj"  # one byte changed


def _package(*, signed: bool) -> bytes:
    """A small valid package: one empty knowledge graph, signed under a throwaway key."""
    import ccx

    builder = ccx.PackageBuilder(
        name="signed-pack",
        package_version="1.0.0",
        license="CC-BY-4.0",
        base_iri="urn:ccx:test:",
        title=TITLE.decode(),
    )
    builder.add_graph("ccx", "knowledge", {"@graph": []}, role="default")
    if signed:
        private_key, _public = ccx.generate_ed25519_keypair()
        builder.sign(private_key)
    return builder.build()


def _flip_manifest_byte(data: bytes) -> bytes:
    """The same zip with one byte of ``manifest.json`` changed, every other entry as it was."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            body = src.read(info)
            if info.filename == "manifest.json":
                assert body.count(TITLE) == 1
                body = body.replace(TITLE, TAMPERED)
            dst.writestr(info, body)
    return out.getvalue()


def _importer() -> CcxImporter:
    return CcxImporter(graph_repository=MagicMock(), sources_repository=None)


async def test_a_manifest_changed_after_signing_is_refused() -> None:
    graph = MagicMock()
    importer = CcxImporter(graph_repository=graph, sources_repository=None)
    with pytest.raises(ValidationError, match="package signature failed") as raised:
        await importer.import_from_bytes(
            _flip_manifest_byte(_package(signed=True)), ImportOptions()
        )
    assert raised.value.details["verified"] is False
    assert raised.value.details["format"] == "ed25519"
    assert graph.mock_calls == []  # refused before anything was written


async def test_a_signed_package_imports() -> None:
    stats = await _importer().import_from_bytes(_package(signed=True), ImportOptions())
    assert not stats.errors


async def test_an_unsigned_package_imports() -> None:
    stats = await _importer().import_from_bytes(_package(signed=False), ImportOptions())
    assert not stats.errors
