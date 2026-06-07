# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""Unit tests for services/compose/resolver.py (PackageResolver).

Covers spec resolution for local archives (.ccx), local directories, hub
packages (cache-hit + download), the BFS dependency-resolution loop in
``resolve_all``, cache-key generation, manifest loading error branches, and
the ``ResolverError`` payload.

Collaborators are mocked at the resolver-module / instance level:
- ``LexiconClient`` (async context manager) — controls hub responses.
- ``extract_archive`` / ``validate_package_directory`` — control package
  loading without real archives.
- ``PackageResolver._load_manifest`` — patched per-instance to return a fake
  manifest (the real method requires a manifest.json on disk, which the mocked
  extract step never writes).

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


def _make_manifest(
    name: str = "pkg",
    version: str = "1.0.0",
    dependencies: dict[str, str] | None = None,
) -> MagicMock:
    """Build a fake PackageManifest-like object."""
    manifest = MagicMock()
    manifest.name = name
    manifest.package_version = version
    manifest.dependencies = dependencies or {}
    manifest.to_dict.return_value = {"name": name, "version": version}
    return manifest


def _fake_lexicon_client(info_version: str = "1.0.0", archive: bytes = b"ccx") -> MagicMock:
    """Build a LexiconClient stub with async get_package_info/download."""
    client = MagicMock()
    client.get_package_info = AsyncMock(return_value=SimpleNamespace(version=info_version))
    client.download = AsyncMock(return_value=archive)
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
    async def test_extracts_and_loads_manifest(self, tmp_path: Path) -> None:
        archive = tmp_path / "thing.ccx"
        archive.write_bytes(b"fake-archive")
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(archive)

        manifest = _make_manifest(name="thing", version="2.0.0", dependencies={"dep-a": "1.0"})
        with (
            patch.object(resolver_mod, "extract_archive") as extract,
            patch.object(resolver, "_load_manifest", return_value=manifest),
        ):
            resolved = await resolver._resolve_local(spec)

        extract.assert_called_once()
        assert resolved.name == "thing"
        assert resolved.version == "2.0.0"
        assert resolved.dependencies == ["dep-a"]
        # extract target is cache/extracted/<stem>
        assert resolved.path.parent.name == "extracted"

    @pytest.mark.asyncio
    async def test_extract_failure_wrapped(self, tmp_path: Path) -> None:
        archive = tmp_path / "thing.ccx"
        archive.write_bytes(b"fake")
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(archive)

        with patch.object(resolver_mod, "extract_archive", side_effect=RuntimeError("corrupt")):
            with pytest.raises(ResolverError, match="Failed to extract archive"):
                await resolver._resolve_local(spec)

    @pytest.mark.asyncio
    async def test_ccx_suffix_case_insensitive(self, tmp_path: Path) -> None:
        archive = tmp_path / "thing.CCX"
        archive.write_bytes(b"fake")
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(archive)

        manifest = _make_manifest(name="thing", version="1.0.0")
        with (
            patch.object(resolver_mod, "extract_archive"),
            patch.object(resolver, "_load_manifest", return_value=manifest),
        ):
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
        pkg_dir.mkdir()
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(pkg_dir)

        manifest = _make_manifest(name="mypkg", version="3.1.0")
        valid_result = SimpleNamespace(is_valid=True, errors=[], warnings=[])
        with (
            patch.object(resolver_mod, "validate_package_directory", return_value=valid_result),
            patch.object(resolver, "_load_manifest", return_value=manifest),
        ):
            resolved = await resolver._resolve_local(spec)

        assert resolved.name == "mypkg"
        assert resolved.version == "3.1.0"
        assert resolved.path == pkg_dir
        assert resolved.dependencies == []

    @pytest.mark.asyncio
    async def test_invalid_directory_raises_with_details(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "broken"
        pkg_dir.mkdir()
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = _local_spec(pkg_dir)

        invalid = SimpleNamespace(
            is_valid=False, errors=["no manifest", "no data"], warnings=["w1"]
        )
        with patch.object(resolver_mod, "validate_package_directory", return_value=invalid):
            with pytest.raises(ResolverError) as exc_info:
                await resolver._resolve_local(spec)

        err = exc_info.value
        assert "Invalid package structure" in err.message
        assert err.details["errors"] == ["no manifest", "no data"]
        assert err.details["warnings"] == ["w1"]


# ---------------------------------------------------------------------------
# _resolve_hub
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveHub:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_download(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("hubpkg:1.0.0")

        # Pre-seed the cache version dir with a manifest.json so cache-hit fires.
        version_dir = resolver.cache_dir / "packages" / "hubpkg" / "1.0.0"
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / "manifest.json").write_text("{}")

        client = _fake_lexicon_client(info_version="1.0.0")
        manifest = _make_manifest(name="hubpkg", version="1.0.0", dependencies={"d": "1"})
        with patch.object(resolver, "_load_manifest", return_value=manifest):
            resolved = await resolver._resolve_hub(spec, client)

        client.get_package_info.assert_awaited_once()
        client.download.assert_not_awaited()
        assert resolved.name == "hubpkg"
        assert resolved.version == "1.0.0"
        assert resolved.dependencies == ["d"]

    @pytest.mark.asyncio
    async def test_download_and_extract(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("hubpkg")  # no version -> "latest"

        client = _fake_lexicon_client(info_version="9.9.9", archive=b"ZIPDATA")
        manifest = _make_manifest(name="hubpkg", version="9.9.9")
        with (
            patch.object(resolver_mod, "extract_archive") as extract,
            patch.object(resolver, "_load_manifest", return_value=manifest),
        ):
            resolved = await resolver._resolve_hub(spec, client)

        client.download.assert_awaited_once_with("hubpkg", "9.9.9")
        extract.assert_called_once()
        assert resolved.version == "9.9.9"
        # version_dir is cache/packages/hubpkg/9.9.9
        assert resolved.path.name == "9.9.9"

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

    def test_from_json_other_error_wrapped(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text(json.dumps({"name": "x"}))
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        pm = MagicMock()
        pm.from_json.side_effect = ValueError("schema boom")
        with patch.object(resolver_mod, "PackageManifest", pm):
            with pytest.raises(ResolverError, match="Failed to load manifest"):
                resolver._load_manifest(tmp_path, "pkg-ref")

    def test_success_returns_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text(json.dumps({"name": "x"}))
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        manifest = _make_manifest()
        pm = MagicMock()
        pm.from_json.return_value = manifest
        with patch.object(resolver_mod, "PackageManifest", pm):
            out = resolver._load_manifest(tmp_path, "pkg-ref")
        assert out is manifest


# ---------------------------------------------------------------------------
# resolve_all — BFS orchestration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveAll:
    @pytest.mark.asyncio
    async def test_resolves_single_hub_package(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("alpha:1.0.0")

        client = _fake_lexicon_client(info_version="1.0.0")
        manifest = _make_manifest(name="alpha", version="1.0.0")
        with (
            _patch_lexicon(client),
            patch.object(resolver_mod, "extract_archive"),
            patch.object(resolver, "_load_manifest", return_value=manifest),
        ):
            result = await resolver.resolve_all([spec])

        assert [p.name for p in result] == ["alpha"]

    @pytest.mark.asyncio
    async def test_dependencies_resolved_in_bfs_order(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        root_spec = PackageSpec.parse("root:1.0.0")

        client = _fake_lexicon_client()

        # root depends on "child"; child has no deps.
        manifests = {
            "root": _make_manifest(name="root", version="1.0.0", dependencies={"child": "1"}),
            "child": _make_manifest(name="child", version="1.0.0"),
        }

        client.get_package_info = AsyncMock(
            side_effect=lambda name, version: SimpleNamespace(version="1.0.0")
        )

        def load_manifest_side_effect(path: Path, _ref: str) -> MagicMock:
            # path = cache/packages/<name>/<ver>
            name = path.parent.name
            return manifests[name]

        with (
            _patch_lexicon(client),
            patch.object(resolver_mod, "extract_archive"),
            patch.object(resolver, "_load_manifest", side_effect=load_manifest_side_effect),
        ):
            result = await resolver.resolve_all([root_spec], include_dependencies=True)

        names = [p.name for p in result]
        assert names == ["root", "child"]

    @pytest.mark.asyncio
    async def test_include_dependencies_false_skips_deps(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec = PackageSpec.parse("root:1.0.0")

        client = _fake_lexicon_client()
        manifest = _make_manifest(name="root", version="1.0.0", dependencies={"child": "1"})
        with (
            _patch_lexicon(client),
            patch.object(resolver_mod, "extract_archive"),
            patch.object(resolver, "_load_manifest", return_value=manifest),
        ):
            result = await resolver.resolve_all([spec], include_dependencies=False)

        assert [p.name for p in result] == ["root"]

    @pytest.mark.asyncio
    async def test_duplicate_specs_resolved_once(self, tmp_path: Path) -> None:
        resolver = PackageResolver(cache_dir=tmp_path / "cache")
        spec_a = PackageSpec.parse("dup:1.0.0")
        spec_b = PackageSpec.parse("dup:1.0.0")

        client = _fake_lexicon_client()
        manifest = _make_manifest(name="dup", version="1.0.0")
        with (
            _patch_lexicon(client),
            patch.object(resolver_mod, "extract_archive"),
            patch.object(resolver, "_load_manifest", return_value=manifest),
        ):
            result = await resolver.resolve_all([spec_a, spec_b])

        # Second spec is skipped via the _resolved cache check.
        assert [p.name for p in result] == ["dup"]
