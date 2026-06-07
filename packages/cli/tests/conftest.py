# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pytest Configuration and Fixtures.

Provides shared fixtures for CLI tests including:
- Mock contexts and adapters
- Sample files for source_processing
- Temporary directories
- Click CLI runner
- Lens testing fixtures

Example:
    def test_source_processing_service(cli_context, sample_pdf):
        service = CLISourceProcessingService(cli_context)
        file_id = service.upload_file(sample_pdf)
        assert file_id.startswith("if_")

    def test_command(cli_runner, mock_context):
        result = cli_runner.invoke(my_command, ["--help"])
        assert result.exit_code == 0
"""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Point CHAOSCYPHER_DATA_DIR at tmp_path and reset app_config caches.

    app_config.get_settings is lru_cached AND backed by a module global;
    get_config_manager is lru_cached too. Without this reset, the first
    test to touch settings.yaml pins its tmp_path for the whole session.
    Yields tmp_path (the data dir that holds settings.yaml).
    """
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    # Hermetic config dir too: without this, tests read (and one historic
    # save_config-based test WROTE) the dev machine's real cli.yaml.
    monkeypatch.setenv("CHAOSCYPHER_CONFIG_DIR", str(tmp_path / "config"))
    for var in (
        "CHAOSCYPHER_LLM_PROVIDER",
        "CHAOSCYPHER_DATABASE",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    from chaoscypher_core import app_config

    def _reset() -> None:
        app_config.get_config_manager.cache_clear()
        app_config.get_settings.cache_clear()
        app_config._settings = None

    _reset()
    yield tmp_path
    _reset()


@pytest.fixture
def temp_dir() -> Generator[Path]:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_text_file(temp_dir: Path) -> Path:
    """Create a sample text file for testing."""
    file_path = temp_dir / "sample.txt"
    file_path.write_text(
        "This is a sample document for testing the source_processing pipeline.\n\n"
        "It contains multiple paragraphs of text that can be chunked and processed.\n\n"
        "The ChaosCypher system will extract entities and relationships from this text.\n\n"
        "For example, it might identify 'ChaosCypher' as a software system entity.\n\n"
        "This paragraph adds more content to ensure we have enough text for chunking.\n"
    )
    return file_path


@pytest.fixture
def sample_markdown_file(temp_dir: Path) -> Path:
    """Create a sample markdown file for testing."""
    file_path = temp_dir / "sample.md"
    file_path.write_text(
        "# Sample Document\n\n"
        "This is a **markdown** document for testing.\n\n"
        "## Section 1\n\n"
        "Some content in section 1 about knowledge graphs and entity extraction.\n\n"
        "## Section 2\n\n"
        "More content about the ChaosCypher platform and its capabilities.\n"
    )
    return file_path


@pytest.fixture
def mock_storage_adapter() -> MagicMock:
    """Create a mock storage adapter for testing."""
    adapter = MagicMock()

    # Track created files/chunks
    adapter._files: dict[str, dict[str, Any]] = {}
    adapter._chunks: dict[str, list[dict[str, Any]]] = {}

    def upload_source(
        source_id: str,
        database_name: str,
        filename: str,
        file_content: bytes,
        staging_dir: str,
        extraction_depth: str = "full",
        forced_domain: str | None = None,
        content_hash: str | None = None,
        # W1 (2026-05-07): upload-row settings that the real adapter
        # persists; the mock just round-trips them so callers can assert.
        auto_analyze: bool = True,
        enable_normalization: bool | None = None,
        enable_vision: bool = True,
        content_filtering: bool = True,
        filtering_mode: str = "balanced",
        # Other upload kwargs the real adapter accepts that some tests
        # don't exercise directly. Captured so this fake doesn't drift.
        **_extra_kwargs: Any,
    ) -> dict[str, Any]:
        suffix = Path(filename).suffix.lstrip(".").lower() if "." in filename else None
        staged_path = (
            f"{staging_dir}/{source_id}.{suffix}" if suffix else f"{staging_dir}/{source_id}"
        )
        record = {
            "id": source_id,
            "database_name": database_name,
            "filename": filename,
            "filepath": staged_path,
            "staged_path": staged_path,
            "file_size": len(file_content),
            "file_type": suffix,
            "status": "uploaded",
            "extraction_depth": extraction_depth,
            "forced_domain": forced_domain,
            "content_hash": content_hash,
            # Round-trip upload-row settings.
            "auto_analyze": auto_analyze,
            "enable_normalization": enable_normalization,
            "enable_vision": enable_vision,
            "content_filtering": content_filtering,
            "filtering_mode": filtering_mode,
        }
        adapter._files[source_id] = record
        # Write the actual file
        Path(staging_dir).mkdir(parents=True, exist_ok=True)
        Path(staged_path).write_bytes(file_content)
        return record

    def get_file(file_id: str, database_name: str) -> dict[str, Any] | None:
        return adapter._files.get(file_id)

    def update_file(file_id: str, database_name: str, updates: dict[str, Any]) -> None:
        if file_id in adapter._files:
            adapter._files[file_id].update(updates)

    def start_indexing(file_id: str) -> None:
        if file_id in adapter._files:
            adapter._files[file_id]["status"] = "indexing"

    def complete_indexing(
        source_id: str,
        chunks_count: int = 0,
        embedding_model: str = "none",
        embedding_dimensions: int = 0,
    ) -> None:
        if source_id in adapter._files:
            adapter._files[source_id]["status"] = "indexed"
            adapter._files[source_id]["chunks_count"] = chunks_count
            adapter._files[source_id]["embedding_model"] = embedding_model
            adapter._files[source_id]["embedding_dimensions"] = embedding_dimensions

    def fail_indexing(file_id: str, error: str) -> None:
        if file_id in adapter._files:
            adapter._files[file_id]["status"] = "failed"
            adapter._files[file_id]["error"] = error

    def create_chunks_batch(
        file_id: str, database_name: str, chunks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        adapter._chunks[file_id] = chunks
        return chunks

    def get_chunks(file_id: str, database_name: str | None = None) -> list[dict[str, Any]]:
        return adapter._chunks.get(file_id, [])

    def get_hierarchical_groups(
        source_id: str, database_name: str | None = None
    ) -> list[dict[str, Any]]:
        chunks = adapter._chunks.get(source_id, [])
        # Group by group_index into dicts with combined_content
        groups: dict[int, list[dict[str, Any]]] = {}
        for chunk in chunks:
            idx = chunk.get("group_index", 0)
            if idx not in groups:
                groups[idx] = []
            groups[idx].append(chunk)
        return [
            {
                "id": f"group_{i}",
                "group_index": i,
                "combined_content": " ".join(c.get("content", "") for c in group_chunks),
                "small_chunk_ids": [c.get("id", "") for c in group_chunks],
            }
            for i, group_chunks in sorted(groups.items())
        ]

    async def store_chunks_and_groups(
        small_chunks: list[dict[str, Any]],
        hierarchical_groups: list[dict[str, Any]],
        batch_size: int = 500,
    ) -> None:
        # Store chunks keyed by source_id from first chunk
        if small_chunks:
            source_id = small_chunks[0].get("source_id", "unknown")
            adapter._chunks[source_id] = small_chunks

    adapter.upload_source = upload_source
    adapter.get_file = get_file
    adapter.update_file = update_file
    adapter.start_indexing = start_indexing
    adapter.complete_indexing = complete_indexing
    adapter.fail_indexing = fail_indexing
    adapter.create_chunks_batch = create_chunks_batch
    adapter.get_chunks = get_chunks
    adapter.get_hierarchical_groups = get_hierarchical_groups
    adapter.store_chunks_and_groups = store_chunks_and_groups

    # Stub other methods
    adapter.start_extraction = MagicMock()
    adapter.complete_extraction = MagicMock()
    adapter.fail_extraction = MagicMock()
    adapter.start_commit = MagicMock()
    adapter.complete_commit = MagicMock()
    adapter.fail_commit = MagicMock()
    adapter.update_step_progress = MagicMock()

    return adapter


@pytest.fixture
def mock_cli_context(temp_dir: Path, mock_storage_adapter: MagicMock) -> MagicMock:
    """Create a mock CLI context for testing."""
    ctx = MagicMock()
    ctx.database_name = "test"
    ctx.database_dir = temp_dir / "databases" / "test"
    ctx.database_dir.mkdir(parents=True, exist_ok=True)
    ctx.storage_adapter = mock_storage_adapter
    ctx.has_llm = False
    ctx.llm_provider = None

    # Mock graph repository and services
    ctx.graph_repository = MagicMock()
    ctx.node_service = MagicMock()
    ctx.edge_service = MagicMock()
    ctx.search_repository = MagicMock()

    # Mock settings with chunking config
    settings = MagicMock()
    settings.chunking = MagicMock()
    settings.chunking.small_chunk_size = 600
    settings.chunking.small_chunk_overlap = 100
    settings.chunking.group_size = 4
    settings.chunking.group_overlap = 1

    # Batching config needed by embedding batch operations
    settings.batching = MagicMock()
    settings.batching.embedding_api_batch_size = 64

    # Paths config needed by LoaderRegistry for plugin discovery
    settings.paths = MagicMock()
    settings.paths.data_dir = str(temp_dir / "data")

    ctx.settings = settings

    return ctx


@pytest.fixture
def mock_cli_context_with_llm(mock_cli_context: MagicMock) -> MagicMock:
    """Create a mock CLI context with LLM provider."""
    ctx = mock_cli_context
    ctx.has_llm = True

    # Mock LLM provider
    llm = MagicMock()
    llm.settings = MagicMock()
    llm.settings.llm = MagicMock()
    llm.settings.llm.ollama_embedding_model = "snowflake-arctic-embed2"

    # Mock async methods
    from chaoscypher_core.models import BatchEmbedResult, LLMChatResponse

    async def mock_chat(messages: list[dict[str, str]]) -> LLMChatResponse:
        return LLMChatResponse(
            content='{"entities": [{"name": "Test", "type": "Thing"}], "relationships": []}',
            provider="mock",
        )

    async def mock_batch_embed(texts: list[str], batch_size: int = 50) -> BatchEmbedResult:
        return BatchEmbedResult(
            embeddings=[[0.1, 0.2, 0.3] for _ in texts],
            total=len(texts),
            failed=0,
            provider="mock",
        )

    llm.chat = mock_chat
    llm.batch_embed = mock_batch_embed

    ctx.llm_provider = llm
    return ctx


# ============================================================================
# Click CLI Testing Fixtures
# ============================================================================


@pytest.fixture
def cli_runner():
    """Create a Click CLI runner for testing commands."""
    from click.testing import CliRunner

    return CliRunner(mix_stderr=False)


@pytest.fixture
def mock_get_context(mock_cli_context: MagicMock):
    """Patch get_context to return mock context."""
    with patch("chaoscypher_cli.context.get_context", return_value=mock_cli_context):
        yield mock_cli_context


@pytest.fixture
def mock_get_context_with_llm(mock_cli_context_with_llm: MagicMock):
    """Patch get_context to return mock context with LLM."""
    with patch("chaoscypher_cli.context.get_context", return_value=mock_cli_context_with_llm):
        yield mock_cli_context_with_llm


# ============================================================================
# Lens Testing Fixtures
# ============================================================================


@pytest.fixture
def sample_lens() -> dict[str, Any]:
    """Create a sample lens for testing."""
    return {
        "id": "lens_test_12345678",
        "name": "test-lens",
        "description": "A test lens for unit testing",
        "templates": ["Person", "Organization"],
        "status": "ready",
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
        "rules": [
            {
                "id": "rule_1",
                "name": "Extract persons",
                "entity_type": "Person",
                "extraction_prompt": "Find all person names",
            }
        ],
        "patterns": [
            {
                "name": "Person pattern",
                "entity_type": "Person",
                "indicators": ["Dr.", "Mr.", "Ms."],
            }
        ],
        "examples": [
            {
                "entity_type": "Person",
                "name": "John Smith",
                "properties": {"role": "CEO"},
            }
        ],
    }


@pytest.fixture
def mock_storage_adapter_with_lenses(
    mock_storage_adapter: MagicMock, sample_lens: dict[str, Any]
) -> MagicMock:
    """Create a mock storage adapter with lens support."""
    adapter = mock_storage_adapter
    adapter._lenses: dict[str, dict[str, Any]] = {sample_lens["id"]: sample_lens}

    def create_lens(lens_data: dict[str, Any]) -> dict[str, Any]:
        adapter._lenses[lens_data["id"]] = lens_data
        return lens_data

    def get_lens(lens_id: str, database_name: str) -> dict[str, Any] | None:
        return adapter._lenses.get(lens_id)

    def list_lenses(
        database_name: str,
        status: str | None = None,
        name: str | None = None,
    ) -> list[dict[str, Any]]:
        lenses = list(adapter._lenses.values())
        if status is not None:
            lenses = [lens for lens in lenses if lens.get("status") == status]
        if name is not None:
            lenses = [lens for lens in lenses if lens.get("name") == name]
        return lenses

    def update_lens(lens_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        if lens_id in adapter._lenses:
            adapter._lenses[lens_id].update(updates)
            return adapter._lenses[lens_id]
        return None

    def delete_lens(lens_id: str, database_name: str) -> bool:
        if lens_id in adapter._lenses:
            del adapter._lenses[lens_id]
            return True
        return False

    adapter.create_lens = create_lens
    adapter.get_lens = get_lens
    adapter.list_lenses = list_lenses
    adapter.update_lens = update_lens
    adapter.delete_lens = delete_lens

    return adapter


@pytest.fixture
def mock_lens_context(temp_dir: Path, mock_storage_adapter_with_lenses: MagicMock) -> MagicMock:
    """Create a mock CLI context for lens testing."""
    ctx = MagicMock()
    ctx.database_name = "test"
    ctx.database_dir = temp_dir / "databases" / "test"
    ctx.database_dir.mkdir(parents=True, exist_ok=True)
    ctx.storage_adapter = mock_storage_adapter_with_lenses
    ctx.has_llm = False
    ctx.llm_provider = None

    # Mock graph repository and services
    ctx.graph_repository = MagicMock()
    ctx.graph_repository._save = MagicMock()
    ctx.node_service = MagicMock()
    ctx.node_service.create_node = MagicMock()
    ctx.node_service.get_node = MagicMock(return_value=None)
    ctx.edge_service = MagicMock()
    ctx.edge_service.create_edge = MagicMock()

    return ctx


@pytest.fixture
def mock_lens_context_with_llm(mock_lens_context: MagicMock) -> MagicMock:
    """Create a mock CLI context for lens testing with LLM."""
    ctx = mock_lens_context
    ctx.has_llm = True

    # Mock LLM provider
    llm = MagicMock()

    from chaoscypher_core.models import LLMChatResponse

    async def mock_chat(messages: list[dict[str, str]]) -> LLMChatResponse:
        # Return appropriate response based on prompt content
        content = messages[0]["content"] if messages else ""

        if "patterns" in content.lower():
            return LLMChatResponse(
                content='{"patterns": [{"name": "Test", "entity_type": "Thing", "indicators": ["test"]}]}',
                provider="mock",
            )
        if "rules" in content.lower():
            return LLMChatResponse(
                content='{"rules": [{"id": "r1", "name": "Test rule", "entity_type": "Thing"}]}',
                provider="mock",
            )
        if "examples" in content.lower():
            return LLMChatResponse(
                content='{"examples": [{"name": "Test Example", "entity_type": "Thing"}]}',
                provider="mock",
            )
        return LLMChatResponse(
            content='{"entities": [{"name": "Test", "type": "Thing"}], "relationships": []}',
            provider="mock",
        )

    llm.chat = mock_chat
    ctx.llm_provider = llm

    return ctx


# ============================================================================
# Hub/API Testing Fixtures
# ============================================================================


@pytest.fixture
def mock_hub_api_client() -> MagicMock:
    """Create a mock Hub API client for testing."""
    client = MagicMock()

    # Mock authentication
    client.is_authenticated = True
    client.username = "test-user"
    client.token = "test-token-12345"

    async def mock_login(username: str, password: str) -> dict[str, Any]:
        return {"token": "test-token-12345", "username": username}

    async def mock_search(query: str, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {"name": "user/package1", "description": "Test package 1"},
            {"name": "user/package2", "description": "Test package 2"},
        ]

    async def mock_download(package: str, version: str | None = None) -> Path:
        return Path("/tmp/test-package.ccx")

    client.login = mock_login
    client.search = mock_search
    client.download = mock_download

    return client
