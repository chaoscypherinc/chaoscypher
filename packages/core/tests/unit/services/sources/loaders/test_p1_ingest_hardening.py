# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for three P1 ingest-path findings (hunt queue, 2026-08-04).

Each test is written to fail if its fix is reverted:

* RST loader ran docutils with ``file_insertion_enabled`` / ``raw_enabled`` at
  their ``True`` defaults, so an uploaded ``.rst`` could read local files and
  hang the worker thread.
* ``ArchiveLoader`` re-entered itself through ``GenericHandler`` with no depth
  cap, so one upload authorised unbounded nested extraction.
"""

from __future__ import annotations

import builtins
import tarfile
import zipfile
from pathlib import Path

import pytest

from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.services.sources.loaders.archive_loader import ArchiveLoader
from chaoscypher_core.services.sources.loaders.rst_loader import RSTLoader
from chaoscypher_core.settings import EngineSettings


class TestRSTLoaderUntrustedContent:
    """The RST loader must not let docutils touch the filesystem."""

    def test_include_directive_never_opens_the_referenced_file(self, tmp_path: Path) -> None:
        """``.. include::`` must not cause docutils to open the target.

        Asserting "the secret is absent from the output" would be vacuous:
        the loader returns the *raw* source unconditionally, so that holds
        whether or not the fix is present (verified by mutation -- such a test
        passes with the fix reverted). The falsifiable property is whether the
        file is opened at all, so spy on ``open`` for the duration of the load.
        """
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP_SECRET_VALUE", encoding="utf-8")

        rst = tmp_path / "evil.rst"
        rst.write_text(
            "Title\n=====\n\n.. include:: " + secret.as_posix() + "\n",
            encoding="utf-8",
        )

        opened: list[str] = []
        real_open = builtins.open

        def _spy_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        builtins.open = _spy_open  # type: ignore[assignment]
        try:
            RSTLoader(EngineSettings()).load_document(str(rst))
        finally:
            builtins.open = real_open  # type: ignore[assignment]

        assert not any(secret.name in p for p in opened), (
            f"docutils opened the included file -- file_insertion_enabled is "
            f"back on. Opened: {opened}"
        )

    def test_raw_file_directive_never_opens_the_referenced_file(self, tmp_path: Path) -> None:
        """``.. raw:: html`` with ``:file:`` is the other half of the pair."""
        secret = tmp_path / "raw_secret.txt"
        secret.write_text("RAW_SECRET_VALUE", encoding="utf-8")

        rst = tmp_path / "evil_raw.rst"
        rst.write_text(
            "Title\n=====\n\n.. raw:: html\n   :file: " + secret.as_posix() + "\n",
            encoding="utf-8",
        )

        opened: list[str] = []
        real_open = builtins.open

        def _spy_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        builtins.open = _spy_open  # type: ignore[assignment]
        try:
            RSTLoader(EngineSettings()).load_document(str(rst))
        finally:
            builtins.open = real_open  # type: ignore[assignment]

        assert not any(secret.name in p for p in opened), (
            f"docutils opened the raw-included file -- raw_enabled is back on. Opened: {opened}"
        )

    def test_settings_overrides_pin_both_flags_off(self, tmp_path: Path) -> None:
        """Pin the two settings explicitly, so a refactor cannot drop them.

        The behavioural tests above pass for the wrong reason if the null
        writer simply discards everything, so assert the call itself.
        """
        import chaoscypher_core.services.sources.loaders.rst_loader as rst_mod

        captured: dict[str, object] = {}
        original = rst_mod.publish_string

        def _spy(*args: object, **kwargs: object) -> object:
            captured.update(kwargs.get("settings_overrides") or {})
            return original(*args, **kwargs)

        rst_mod.publish_string = _spy  # type: ignore[assignment]
        try:
            rst = tmp_path / "plain.rst"
            rst.write_text("Title\n=====\n\nBody text.\n", encoding="utf-8")
            RSTLoader(EngineSettings()).load_document(str(rst))
        finally:
            rst_mod.publish_string = original  # type: ignore[assignment]

        assert captured.get("file_insertion_enabled") is False
        assert captured.get("raw_enabled") is False


class TestArchiveNestingDepth:
    """One upload must not authorise unbounded nested extraction."""

    @staticmethod
    def _zip_containing(path: Path, inner: Path) -> Path:
        with zipfile.ZipFile(path, "w") as zf:
            zf.write(inner, arcname=inner.name)
        return path

    def test_nesting_beyond_the_cap_is_refused(self, tmp_path: Path) -> None:
        """At the cap, the loader refuses *before* extracting anything."""
        from chaoscypher_core.services.sources.loaders import archive_loader as mod

        leaf = tmp_path / "leaf.txt"
        leaf.write_text("hello", encoding="utf-8")
        archive = self._zip_containing(tmp_path / "outer.zip", leaf)

        settings = EngineSettings()
        settings.archive.max_nesting_depth = 2
        loader = ArchiveLoader(settings=settings)

        # Simulate already being two levels deep, as GenericHandler's re-entry
        # would be.
        token = mod._archive_depth.set(2)
        try:
            with pytest.raises(OperationError, match="nesting depth"):
                loader.load_document(str(archive))
        finally:
            mod._archive_depth.reset(token)

    def test_depth_is_restored_after_a_failed_load(self, tmp_path: Path) -> None:
        """A failure must not leave the depth inflated for later archives.

        Without the ``finally``-based reset, one broken nested archive would
        permanently shrink the remaining budget on that context.
        """
        from chaoscypher_core.services.sources.loaders import archive_loader as mod

        broken = tmp_path / "broken.zip"
        broken.write_bytes(b"not a real zip")

        loader = ArchiveLoader(settings=EngineSettings())
        before = mod._archive_depth.get()

        with pytest.raises(Exception):  # noqa: B017 - any extraction failure
            loader.load_document(str(broken))

        assert mod._archive_depth.get() == before, "nesting depth leaked after a failed load"

    def test_top_level_load_is_unaffected(self, tmp_path: Path) -> None:
        """The guard must not break ordinary single-level archives."""
        leaf = tmp_path / "doc.md"
        leaf.write_text("# Title\n\nBody.\n", encoding="utf-8")
        archive = self._zip_containing(tmp_path / "plain.zip", leaf)

        from chaoscypher_core.services.sources.loaders import archive_loader as mod

        loader = ArchiveLoader(settings=EngineSettings())
        docs = loader.load_document(str(archive))

        assert docs, "a normal one-level archive should still load"
        assert mod._archive_depth.get() == 0, "depth not restored after success"

    def test_tar_gz_is_also_gated(self, tmp_path: Path) -> None:
        """The cap covers every supported archive suffix, not just .zip."""
        from chaoscypher_core.services.sources.loaders import archive_loader as mod

        leaf = tmp_path / "leaf.txt"
        leaf.write_text("hello", encoding="utf-8")
        archive = tmp_path / "outer.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(leaf, arcname=leaf.name)

        settings = EngineSettings()
        settings.archive.max_nesting_depth = 1
        loader = ArchiveLoader(settings=settings)

        token = mod._archive_depth.set(1)
        try:
            with pytest.raises(OperationError, match="nesting depth"):
                loader.load_document(str(archive))
        finally:
            mod._archive_depth.reset(token)
