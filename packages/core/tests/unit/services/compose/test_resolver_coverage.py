# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""Unit tests for services/compose/resolver.py (PackageResolver).

Covers spec resolution for local archives (.ccx), local directories, hub
packages (cache-hit + download), the BFS dependency-resolution loop in
``resolve_all``, cache-key generation, manifest loading error branches, and
the ``ResolverError`` payload.

The resolver now reads + validates packages via ``ccx-format``:
- ``.ccx`` files / downloaded bytes are validated with
  ``ccx.open_package(...).validate()`` and the CCX 3.0 ``manifest.json`` drives
  ``ResolvedPackage`` construction, then the generic ``extract_archive``
  unpacks the bytes to the cache dir for downstream composition.
- A local directory is resolved by reading its CCX 3.0 ``manifest.json``.

Real CCX 3.0 packages are built with ``ccx.PackageBuilder`` for fixtures.
``extract_archive`` is mocked where the test only cares about manifest reading
(the real extraction of a valid package is exercised elsewhere).

Local ``PackageSpec`` objects are built explicitly with ``is_local=True``
because ``PackageSpec.parse`` only treats ``./`` ``../`` ``/`` prefixes as
local (a Windows ``C:\\`` absolute path would be misclassified as a hub spec).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import ccx
import pytest

from chaoscypher_core.services.compose import resolver as resolver_mod
from chaoscypher_core.services.compose.models import PackageSpec
from chaoscypher_core.services.compose.resolver import PackageResolver, ResolverError
from chaoscypher_core.services.lexicon import LexiconClientError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local_spec(path: Path) -> PackageSpec:
    """Build a local PackageSpec without going through parse() prefix rules."""
    return PackageSpec(
        raw=str(path),
        name=path.stem,
        version=None,
        is_local=True,
        local_path=path,
    )


def _ccx_bytes(
    name: str = "pkg",
    version: str = "1.0.0",
    dependencies: dict[str, str] | None = None,
) -> bytes:
    """Build a minimal, valid CCX 3.0 package via the reference writer."""
    builder = ccx.PackageBuilder(
        name=name,
        package_version=version,
        license="CC-BY-4.0",
        dependencies=dependencies or None,
    )
    builder.add_graph("ccx", "knowledge", {"@graph": []}, role="default")
    data: bytes = builder.build()
    return data


def _manifest_dict(
    name: str = "pkg",
    version: str = "1.0.0",
    dependencies: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the CCX 3.0 manifest.json dict the resolver reads from a dir."""
    manifest: dict[str, Any] = {
        "ccx_version": "3.0",
        "name": name,
        "package_version": version,
    }
    if dependencies:
        manifest["dependencies"] = dependencies
    return manifest


def _write_manifest(
    target_dir: Path,
    name: str = "pkg",
    version: str = "1.0.0",
    dependencies: dict[str, str] | None = None,
) -> None:
    """Write a CCX 3.0 manifest.json into ``target_dir``."""
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "manifest.json").write_text(
        json.dumps(_manifest_dict(name, version, dependencies)),
        encoding="utf-8",
    )


def _fake_lexicon_client(info_version: str = "1.0.0", archive: bytes | None = None) -> MagicMock:
    """Build a LexiconClient stub with async get_package_info/download."""
    client = MagicMock()
    client.get_package_info = AsyncMock(return_value=SimpleNamespace(version=info_version))
    client.download = AsyncMock(return_value=archive if archive is not None else _ccx_bytes())
    return client


def _patch_lexicon(client: MagicMock) -> Any:
    """Patch LexiconClient so `async with LexiconClient(...)` yields `client`."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=cm)
    return patch.object(resolver_mod, "LexiconClient", factory)


# ---------------------------------------------------------------------------
# ResolverError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolverError:
    def test_carries_package_in_details(self) -> None:
        err = ResolverError("boom", package="pkg-x", details={"a": 1})
        assert err.code == "RESOLVER_ERROR"
        assert err.package == "pkg-x"
        assert err.details["package"] == "pkg-x"
        assert err.details["a"] == 1

    def test_without_package(self) -> None:
        err = ResolverError("boom")
        assert err.package is None
        assert err.details == {}


# ---------------------------------------------------------------------------
# _get_cache_key
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCacheKey:
    def test_local_spec_keyed_by_path(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(tmp_path / "pkg")
        key = resolver._get_cache_key(spec)
        assert key.startswith("local:")

    def test_versioned_hub_spec(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("med-ontology:2.1.0")
        assert resolver._get_cache_key(spec) == "med-ontology:2.1.0"

    def test_unversioned_hub_spec(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("med-ontology")
        assert resolver._get_cache_key(spec) == "med-ontology"

    def test_default_cache_dir(self) -> None:
        resolver = PackageResolver()
        assert resolver.cache_dir.parts[-2:] == (".chaoscypher", "cache")


# ---------------------------------------------------------------------------
# _resolve_local — error branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveLocalErrors:
    @pytest.mark.asyncio
    async def test_missing_local_path(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(tmp_path / "x")
        spec.local_path = None
        with pytest.raises(ResolverError, match="Local path not specified"):
            await resolver._resolve_local(spec)

    @pytest.mark.asyncio
    async def test_nonexistent_path(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(tmp_path / "nope")
        with pytest.raises(ResolverError, match="Package not found"):
            await resolver._resolve_local(spec)

    @pytest.mark.asyncio
    async def test_invalid_path_kind(self, tmp_path: Path) -> None:
        # An existing file that is neither .ccx nor a directory.
        bad = tmp_path / "data.txt"
        bad.write_text("hi")
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(bad)
        with pytest.raises(ResolverError, match="Invalid package path"):
            await resolver._resolve_local(spec)


# ---------------------------------------------------------------------------
# _resolve_archive (.ccx)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveArchive:
    @pytest.mark.asyncio
    async def test_validates_and_loads_manifest(self, tmp_path: Path) -> None:
        archive = tmp_path / "thing.ccx"
        archive.write_bytes(
            _ccx_bytes(name="thing", version="2.0.0", dependencies={"dep-a": "1.0"})
        )
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(archive)

        with patch.object(resolver_mod, "extract_archive") as extract:
            resolved = await resolver._resolve_local(spec)

        # ccx-format validated the bytes; the manifest drove construction; the
        # generic extract_archive still unpacked to the cache dir.
        extract.assert_called_once()
        assert resolved.name == "thing"
        assert resolved.version == "2.0.0"
        assert resolved.dependencies == ["dep-a"]
        # extract target is cache/extracted/<stem>
        assert resolved.path.parent.name == "extracted"

    @pytest.mark.asyncio
    async def test_invalid_ccx_raises(self, tmp_path: Path) -> None:
        archive = tmp_path / "thing.ccx"
        archive.write_bytes(b"not a real zip")
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(archive)

        with pytest.raises(ResolverError, match="Invalid .ccx package"):
            await resolver._resolve_local(spec)

    @pytest.mark.asyncio
    async def test_extract_failure_wrapped(self, tmp_path: Path) -> None:
        archive = tmp_path / "thing.ccx"
        archive.write_bytes(_ccx_bytes(name="thing"))
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(archive)

        with patch.object(resolver_mod, "extract_archive", side_effect=RuntimeError("corrupt")):
            with pytest.raises(ResolverError, match="Failed to extract archive"):
                await resolver._resolve_local(spec)

    @pytest.mark.asyncio
    async def test_ccx_suffix_case_insensitive(self, tmp_path: Path) -> None:
        archive = tmp_path / "thing.CCX"
        archive.write_bytes(_ccx_bytes(name="thing", version="1.0.0"))
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(archive)

        with patch.object(resolver_mod, "extract_archive"):
            resolved = await resolver._resolve_local(spec)
        assert resolved.name == "thing"


# ---------------------------------------------------------------------------
# _resolve_directory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveDirectory:
    @pytest.mark.asyncio
    async def test_valid_directory(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "mypkg"
        _write_manifest(pkg_dir, name="mypkg", version="3.1.0")
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(pkg_dir)

        resolved = await resolver._resolve_local(spec)

        assert resolved.name == "mypkg"
        assert resolved.version == "3.1.0"
        assert resolved.path == pkg_dir
        assert resolved.dependencies == []

    @pytest.mark.asyncio
    async def test_directory_with_dependencies(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "mypkg"
        _write_manifest(pkg_dir, name="mypkg", version="1.0.0", dependencies={"dep-a": "1.0"})
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(pkg_dir)

        resolved = await resolver._resolve_local(spec)

        assert resolved.dependencies == ["dep-a"]

    @pytest.mark.asyncio
    async def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "broken"
        pkg_dir.mkdir()
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(pkg_dir)

        with pytest.raises(ResolverError, match="Missing manifest.json"):
            await resolver._resolve_local(spec)


# ---------------------------------------------------------------------------
# _resolve_hub
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveHub:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_download(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("hubpkg:1.0.0")

        # Pre-seed the cache version dir with a CCX 3.0 manifest.json.
        version_dir = resolver.cache_dir / "packages" / "hubpkg" / "1.0.0"
        _write_manifest(version_dir, name="hubpkg", version="1.0.0", dependencies={"d": "1"})

        client = _fake_lexicon_client(info_version="1.0.0")
        resolved = await resolver._resolve_hub(spec, client)

        client.get_package_info.assert_awaited_once()
        client.download.assert_not_awaited()
        assert resolved.name == "hubpkg"
        assert resolved.version == "1.0.0"
        assert resolved.dependencies == ["d"]

    @pytest.mark.asyncio
    async def test_download_validates_and_extracts(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("hubpkg")  # no version -> "latest"

        archive = _ccx_bytes(name="hubpkg", version="9.9.9")
        client = _fake_lexicon_client(info_version="9.9.9", archive=archive)
        with patch.object(resolver_mod, "extract_archive") as extract:
            resolved = await resolver._resolve_hub(spec, client)

        client.download.assert_awaited_once_with("hubpkg", "9.9.9")
        extract.assert_called_once()
        assert resolved.name == "hubpkg"
        assert resolved.version == "9.9.9"
        # version_dir is cache/packages/hubpkg/9.9.9
        assert resolved.path.name == "9.9.9"

    @pytest.mark.asyncio
    async def test_download_invalid_ccx_raises(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("hubpkg:1.0.0")

        client = _fake_lexicon_client(info_version="1.0.0", archive=b"garbage")
        with pytest.raises(ResolverError, match="Invalid .ccx package"):
            await resolver._resolve_hub(spec, client)

    @pytest.mark.asyncio
    async def test_lexicon_error_wrapped(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("hubpkg:1.0.0")

        client = MagicMock()
        client.get_package_info = AsyncMock(
            side_effect=LexiconClientError(status_code=404, message="not found", details={"x": 1})
        )
        with pytest.raises(ResolverError) as exc_info:
            await resolver._resolve_hub(spec, client)

        err = exc_info.value
        assert "Hub error" in err.message
        assert err.details["status_code"] == 404


# ---------------------------------------------------------------------------
# _load_manifest — error branches (real method, real files)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadManifest:
    def test_missing_manifest(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        with pytest.raises(ResolverError, match="Missing manifest.json"):
            resolver._load_manifest(tmp_path, "pkg-ref")

    def test_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text("{ not json ")
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        with pytest.raises(ResolverError, match="Invalid manifest JSON"):
            resolver._load_manifest(tmp_path, "pkg-ref")

    def test_success_returns_manifest_view(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, name="x", version="4.5.6", dependencies={"d": "1"})
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        out = resolver._load_manifest(tmp_path, "pkg-ref")
        assert out.name == "x"
        assert out.package_version == "4.5.6"
        assert out.dependencies == {"d": "1"}
        assert out.to_dict()["name"] == "x"


# ---------------------------------------------------------------------------
# resolve_all — BFS orchestration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveAll:
    @pytest.mark.asyncio
    async def test_resolves_single_hub_package(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("alpha:1.0.0")

        client = _fake_lexicon_client(
            info_version="1.0.0", archive=_ccx_bytes(name="alpha", version="1.0.0")
        )
        with (
            _patch_lexicon(client),
            patch.object(resolver_mod, "extract_archive"),
        ):
            result = await resolver.resolve_all([spec])

        assert [p.name for p in result] == ["alpha"]

    @pytest.mark.asyncio
    async def test_dependencies_resolved_in_bfs_order(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        root_spec = PackageSpec.parse("root:1.0.0")

        # root depends on "child"; child has no deps.
        archives = {
            "root": _ccx_bytes(name="root", version="1.0.0", dependencies={"child": "1"}),
            "child": _ccx_bytes(name="child", version="1.0.0"),
        }

        client = MagicMock()
        client.get_package_info = AsyncMock(
            side_effect=lambda name, version: SimpleNamespace(version="1.0.0")
        )
        client.download = AsyncMock(side_effect=lambda name, version: archives[name])

        with (
            _patch_lexicon(client),
            patch.object(resolver_mod, "extract_archive"),
        ):
            result = await resolver.resolve_all([root_spec], include_dependencies=True)

        names = [p.name for p in result]
        assert names == ["root", "child"]

    @pytest.mark.asyncio
    async def test_include_dependencies_false_skips_deps(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("root:1.0.0")

        client = _fake_lexicon_client(
            info_version="1.0.0",
            archive=_ccx_bytes(name="root", version="1.0.0", dependencies={"child": "1"}),
        )
        with (
            _patch_lexicon(client),
            patch.object(resolver_mod, "extract_archive"),
        ):
            result = await resolver.resolve_all([spec], include_dependencies=False)

        assert [p.name for p in result] == ["root"]

    @pytest.mark.asyncio
    async def test_duplicate_specs_resolved_once(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec_a = PackageSpec.parse("dup:1.0.0")
        spec_b = PackageSpec.parse("dup:1.0.0")

        client = _fake_lexicon_client(
            info_version="1.0.0", archive=_ccx_bytes(name="dup", version="1.0.0")
        )
        with (
            _patch_lexicon(client),
            patch.object(resolver_mod, "extract_archive"),
        ):
            result = await resolver.resolve_all([spec_a, spec_b])

        # Second spec is skipped via the _resolved cache check.
        assert [p.name for p in result] == ["dup"]
