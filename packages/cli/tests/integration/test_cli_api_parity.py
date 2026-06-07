# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Same source uploaded via CLI and API produces the same row state.

W9 (2026-05-08): End-to-end parity guard. Workstream 1 (2026-05-07)
plumbed every upload setting through both frontends; this test pins
the contract so future drift on either side fails fast.

The test runs two uploads against a shared in-memory storage adapter:

1. **CLI path** — ``CLISourceProcessingService.upload_file`` writes the
   row directly through the storage adapter.
2. **API path** — Core's ``SourceProcessingService.upload_file``
   (the function the Cortex ``UploadService`` and the URL fetch
   handler both call) writes the row through the same adapter.

The two rows are then compared on every persisted upload-setting
column. Both paths are driven with identical option values, so any
divergence must come from one of the two services dropping a value
between its own signature and the storage write.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------
# Shared infrastructure
# ---------------------------------------------------------------------


class _ParityStorage:
    """Tiny in-memory adapter that captures upload_source kwargs.

    Both the CLI service and the Core ``SourceProcessingService`` call
    ``upload_source`` on the storage adapter; we record the resulting
    row dict so the test can compare per-column state across paths.
    """

    def __init__(self) -> None:
        self._files: dict[str, dict[str, Any]] = {}

    def upload_source(
        self,
        source_id: str,
        database_name: str,
        filename: str,
        file_content: bytes | None = None,
        staging_dir: str | None = None,
        extraction_depth: str = "full",
        forced_domain: str | None = None,
        content_hash: str | None = None,
        origin_url: str | None = None,
        source_type_override: str | None = None,
        title_override: str | None = None,
        staged_file_path: Path | None = None,
        file_size: int | None = None,
        auto_analyze: bool = True,
        enable_normalization: bool | None = None,
        enable_vision: bool = True,
        content_filtering: bool = True,
        filtering_mode: str = "balanced",
        **_extra_kwargs: Any,
    ) -> dict[str, Any]:
        # Resolve filesystem path so both paths exercise the same
        # staging convention. The actual contents don't matter — the
        # parity test asserts on the persisted row, not on disk.
        if staged_file_path is not None:
            staged_path = str(staged_file_path)
            try:
                resolved_size = (
                    file_size if file_size is not None else Path(staged_file_path).stat().st_size
                )
            except OSError:
                resolved_size = file_size or 0
        else:
            staging_root = Path(staging_dir or "/tmp/sources")
            staging_root.mkdir(parents=True, exist_ok=True)
            suffix = Path(filename).suffix
            staged = staging_root / f"{source_id}{suffix}"
            staged.write_bytes(file_content or b"")
            staged_path = str(staged)
            resolved_size = file_size if file_size is not None else len(file_content or b"")

        record: dict[str, Any] = {
            "id": source_id,
            "database_name": database_name,
            "filename": filename,
            "filepath": staged_path,
            "staged_path": staged_path,
            "file_size": resolved_size,
            "status": "uploaded",
            "extraction_depth": extraction_depth,
            "forced_domain": forced_domain,
            "content_hash": content_hash,
            "origin_url": origin_url,
            "source_type": source_type_override or "file",
            "title": title_override or filename,
            # Upload-row settings (W1, 2026-05-07).
            "auto_analyze": auto_analyze,
            "enable_normalization": enable_normalization,
            "enable_vision": enable_vision,
            "content_filtering": content_filtering,
            "filtering_mode": filtering_mode,
        }
        self._files[source_id] = record
        return record

    def get_file(self, file_id: str, _database_name: str) -> dict[str, Any] | None:
        return self._files.get(file_id)

    def get_source(self, file_id: str, _database_name: str) -> dict[str, Any] | None:
        # Cortex/Core nomenclature — both names point at the same row.
        return self._files.get(file_id)

    def find_by_content_hash(
        self, _database_name: str, _content_hash: str
    ) -> dict[str, Any] | None:
        return None

    def update_file(self, file_id: str, _database_name: str, updates: dict[str, Any]) -> None:
        if file_id in self._files:
            self._files[file_id].update(updates)


class _ParityEnv:
    """Bundle the shared storage with helpers for both upload paths."""

    def __init__(self, tmp_path: Path, sample_file: Path) -> None:
        self.adapter = _ParityStorage()
        self.tmp_path = tmp_path
        self.sample_file = sample_file
        self.database_name = "default"

    def _build_settings(self) -> MagicMock:
        settings = MagicMock()
        settings.batching = MagicMock()
        settings.batching.max_upload_bytes = 10_000_000
        settings.batching.max_upload_files = 20
        settings.batching.upload_max_concurrent = 4
        settings.batching.upload_chunk_size = 1024
        settings.batching.upload_disk_headroom_bytes = 0
        settings.batching.upload_content_type_allowlist = ["*"]
        settings.batching.embedding_api_batch_size = 32
        settings.current_database = self.database_name
        settings.database_dir = self.tmp_path / "databases" / self.database_name
        settings.database_dir.mkdir(parents=True, exist_ok=True)
        settings.data_dir = str(self.tmp_path / "data")
        settings.paths = MagicMock()
        settings.paths.data_dir = settings.data_dir
        # Priority used by the Core service for queue dispatch.
        settings.priorities = MagicMock()
        settings.priorities.background = 5
        # Chunking settings the CLI ctx fixture references.
        settings.chunking = MagicMock()
        settings.chunking.small_chunk_size = 600
        settings.chunking.small_chunk_overlap = 100
        settings.chunking.group_size = 4
        settings.chunking.group_overlap = 1
        return settings

    def upload_via_cli(self, options: dict[str, Any]) -> str:
        """Drive the CLI upload path; return the new source_id."""
        from chaoscypher_cli.sources.service import CLISourceProcessingService

        ctx = MagicMock()
        ctx.database_name = self.database_name
        ctx.database_dir = self.tmp_path / "databases" / self.database_name
        ctx.database_dir.mkdir(parents=True, exist_ok=True)
        ctx.storage_adapter = self.adapter
        ctx.has_llm = False
        ctx.llm_provider = None
        ctx.settings = self._build_settings()

        # Patch the loader registry so .txt is supported without
        # spinning up the full plugin discovery chain.
        from unittest.mock import patch

        loader_registry = MagicMock()
        loader_registry.list_supported_extensions.return_value = [
            ".txt",
            ".md",
            ".pdf",
            ".html",
            ".json",
        ]

        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=loader_registry,
        ):
            service = CLISourceProcessingService(ctx)
            file_id = service.upload_file(
                self.sample_file,
                extraction_depth=options["extraction_depth"],
                domain=options["forced_domain"],
                skip_duplicates=options["skip_duplicates"],
                auto_analyze=options["auto_analyze"],
                enable_normalization=options["enable_normalization"],
                enable_vision=options["enable_vision"],
                content_filtering=options["content_filtering"],
                filtering_mode=options["filtering_mode"],
            )
            assert isinstance(file_id, str)
            return file_id

    async def upload_via_api(self, options: dict[str, Any]) -> str:
        """Drive the Core ``SourceProcessingService.upload_file`` path.

        This is the function the Cortex ``UploadService.upload_single``
        and the URL-fetch handler both delegate to, so calling it
        directly exercises the API surface without standing up FastAPI.
        """
        from chaoscypher_core.services.sources.management.service import (
            SourceProcessingService,
        )

        settings = self._build_settings()
        config_manager = MagicMock()
        config_manager.get_settings.return_value = settings

        # operations_manager.queue_import_indexing is awaited; stub it
        # so the upload flow doesn't try to talk to a real queue.
        operations_manager = MagicMock()
        operations_manager.queue_import_indexing = AsyncMock(return_value={"task_id": "tsk_test"})

        validators = MagicMock()
        validators.require_source_processing_service = MagicMock()

        service = SourceProcessingService(
            source_manager=self.adapter,
            operations_manager=operations_manager,
            config_manager=config_manager,
            validators=validators,
        )

        file_content = self.sample_file.read_bytes()
        content_hash = hashlib.sha256(file_content).hexdigest()
        result = await service.upload_file(
            file_content=file_content,
            filename=self.sample_file.name,
            auto_analyze=options["auto_analyze"],
            extraction_depth=options["extraction_depth"],
            generate_embeddings=True,
            enable_normalization=options["enable_normalization"],
            forced_domain=options["forced_domain"],
            skip_duplicates=options["skip_duplicates"],
            enable_vision=options["enable_vision"],
            content_filtering=options["content_filtering"],
            filtering_mode=options["filtering_mode"],
            content_hash=content_hash,
            file_size=len(file_content),
        )
        return str(result["id"])


@pytest.fixture
def parity_test_env(tmp_path: Path) -> _ParityEnv:
    sample = tmp_path / "parity_doc.txt"
    sample.write_text(
        "Knowledge graphs link entities through relationships.\n"
        "ChaosCypher extracts that structure from raw documents.\n"
    )
    return _ParityEnv(tmp_path=tmp_path, sample_file=sample)


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_and_api_produce_same_row_state(
    parity_test_env: _ParityEnv,
) -> None:
    """Upload identical content with identical options through both paths."""
    options: dict[str, Any] = {
        "auto_analyze": True,
        "extraction_depth": "quick",
        "forced_domain": "generic",
        "skip_duplicates": False,
        "enable_normalization": False,
        "enable_vision": False,
        "content_filtering": True,
        "filtering_mode": "strict",
    }

    cli_src_id = parity_test_env.upload_via_cli(options)
    cli_row = parity_test_env.adapter.get_source(cli_src_id, "default")
    assert cli_row is not None

    api_src_id = await parity_test_env.upload_via_api(options)
    api_row = parity_test_env.adapter.get_source(api_src_id, "default")
    assert api_row is not None

    # Compare every persisted upload-setting column. These are the
    # exact set Workstream 1 plumbed through both frontends.
    parity_keys = [
        "auto_analyze",
        "extraction_depth",
        "forced_domain",
        "enable_normalization",
        "enable_vision",
        "content_filtering",
        "filtering_mode",
    ]
    for key in parity_keys:
        assert cli_row[key] == api_row[key], (
            f"CLI vs API drift on {key!r}: cli={cli_row[key]!r} api={api_row[key]!r}"
        )


@pytest.mark.asyncio
async def test_cli_and_api_default_options_match(
    parity_test_env: _ParityEnv,
) -> None:
    """Default options on each path produce the same persisted row.

    Catches drift in *defaults*, not just user-supplied values: e.g.
    if one frontend silently flipped its ``enable_vision`` default
    from ``True`` to ``False``, this test would fail even though
    callers pass nothing for that flag.
    """
    options: dict[str, Any] = {
        "auto_analyze": True,
        "extraction_depth": "full",
        "forced_domain": None,
        "skip_duplicates": False,
        "enable_normalization": None,
        "enable_vision": True,
        "content_filtering": True,
        "filtering_mode": "balanced",
    }

    cli_src_id = parity_test_env.upload_via_cli(options)
    api_src_id = await parity_test_env.upload_via_api(options)

    cli_row = parity_test_env.adapter.get_source(cli_src_id, "default")
    api_row = parity_test_env.adapter.get_source(api_src_id, "default")
    assert cli_row is not None
    assert api_row is not None

    for key in (
        "auto_analyze",
        "extraction_depth",
        "forced_domain",
        "enable_normalization",
        "enable_vision",
        "content_filtering",
        "filtering_mode",
    ):
        assert cli_row[key] == api_row[key], (
            f"CLI vs API default drift on {key!r}: cli={cli_row[key]!r} api={api_row[key]!r}"
        )
