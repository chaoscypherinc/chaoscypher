# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI E2E test fixtures.

Provides a temp data directory and Click CliRunner for testing
CLI commands against real (but temporary) databases.

Note: ChaosCypher CLI uses LazyGroup which checks sys.argv to decide
whether to load real commands vs stubs. We patch sys.argv in the
invoke helper to make LazyGroup load the real commands.
"""

import hashlib
import math
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from click.testing import CliRunner


if TYPE_CHECKING:
    from collections.abc import Iterator

    from chaoscypher_core.models import BatchEmbedResult, EmbedResult
    from chaoscypher_core.ports.embedding import EmbeddingHealthStatus
    from chaoscypher_core.settings import EngineSettings


def pytest_collection_modifyitems(config, items):
    """Auto-apply cli marker to all tests in this directory (collection time)."""
    for item in items:
        if "/cli/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.cli)


@pytest.fixture(autouse=True)
def reset_cli_singletons() -> None:
    """Reset process-wide CLI / settings singletons between tests.

    Without this, the cached ``_context_instance`` in
    ``chaoscypher_cli.context`` and the ``lru_cache`` on
    ``get_settings`` / ``get_config_manager`` keep pointing at the
    first test's ``tmp_path``. Subsequent tests then operate on a
    stale Engine + DB and surface as either silent-stale-reads or
    UNIQUE / FK violations when fixture data overlaps.
    """
    from chaoscypher_cli.context import reset_context
    from chaoscypher_core import app_config

    reset_context()
    app_config.get_settings.cache_clear()
    app_config.get_config_manager.cache_clear()
    # cache_clear alone is not enough: get_settings is also backed by the
    # module-global ``_settings``, which would survive the lru reset and
    # keep serving the first test's data_dir.
    app_config._settings = None


class _DeterministicEmbeddingProvider:
    """Hermetic stand-in for the configured embedding provider.

    Satisfies ``EmbeddingProviderProtocol`` structurally with vectors
    derived from ``sha256(text)``, so identical input always yields an
    identical vector -- within a run, across runs, and across machines
    (unlike ``hash()``, which is salted per process).

    Exists because the real ``local`` provider needs Qwen3-Embedding-0.6B
    weights: the cloud routine sandbox has no HF cache,
    ``allow_model_download`` defaults False and ``huggingface.co`` is
    403-blocked there, so ``e2e-cli`` failed for five nights (#435/#437);
    on a dev machine the same test silently *downloaded* the model and
    spent ~50s doing it. Either way the tier's result depended on ambient
    model state rather than on the code under test.

    Semantic quality is irrelevant to what this tier asserts:
    ``SearchRepository.vector_search`` applies no similarity threshold,
    so any well-formed vector of the configured width exercises the real
    path -- embed -> ``vec_search_chunks`` insert -> retrieval.
    """

    model_name = "e2e-deterministic-embed"

    def __init__(self, dimensions: int) -> None:
        """Build a provider emitting ``dimensions``-wide unit vectors."""
        self.dimensions = dimensions

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier (matches the protocol)."""
        return "e2e-deterministic"

    def _vector(self, text: str) -> list[float]:
        """Derive a stable unit vector of the configured width from text."""
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [
            (digest[i % len(digest)] ^ (i * 31 % 256)) / 255.0 - 0.5 for i in range(self.dimensions)
        ]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]

    async def embed(self, text: str) -> EmbedResult:
        """Embed a single text deterministically."""
        from chaoscypher_core.models import EmbedResult, TokenUsage

        tokens = max(1, len(text) // 4)
        return EmbedResult(
            embedding=self._vector(text),
            provider=self.provider_type,
            usage=TokenUsage(input_tokens=tokens, output_tokens=0, total_tokens=tokens),
        )

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        """Embed a batch deterministically (all-or-nothing, per the port)."""
        from chaoscypher_core.models import BatchEmbedResult

        return BatchEmbedResult(
            embeddings=[self._vector(t) for t in texts],
            total=len(texts),
            provider=self.provider_type,
        )

    async def check_health(self) -> EmbeddingHealthStatus:
        """Report healthy -- this provider has nothing to be unavailable."""
        from chaoscypher_core.ports.embedding import EmbeddingHealthStatus

        return EmbeddingHealthStatus(
            healthy=True,
            provider=self.provider_type,
            model=self.model_name,
            dimensions=self.dimensions,
            message="deterministic e2e provider",
        )


@pytest.fixture(autouse=True)
def hermetic_embeddings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Route every embedding request through the deterministic provider.

    Patches the factory at each module that binds the name, because call
    sites differ: most import it lazily inside a function (the commit
    service, ``chaoscypher_cli.context``) and so resolve the package
    attribute at call time, while ``repo_factories.embedding_factory``
    binds it at import. Patching only one of them leaves the other on the
    real model -- which is how this tier came to depend on ambient model
    state in the first place.

    The singleton in ``embedding_factory`` is invalidated on both sides of
    the test: entering, so a provider cached by an earlier test cannot
    serve this one, and leaving, so this fake cannot escape the tier.
    """
    from chaoscypher_core import adapters as adapters_pkg
    from chaoscypher_core.adapters import embedding as embedding_pkg
    from chaoscypher_core.adapters.embedding import factory as embedding_factory_mod
    from chaoscypher_core.repo_factories import embedding_factory

    def _create(settings: EngineSettings) -> _DeterministicEmbeddingProvider:
        return _DeterministicEmbeddingProvider(settings.search.vector_dimensions)

    for module in (adapters_pkg, embedding_pkg, embedding_factory_mod, embedding_factory):
        monkeypatch.setattr(module, "create_embedding_provider", _create)

    embedding_factory.invalidate_embedding_service()
    yield
    embedding_factory.invalidate_embedding_service()


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a Click CLI runner."""
    return CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory for CLI tests."""
    data = tmp_path / "data"
    data.mkdir()
    return data


@pytest.fixture
def cli_env(data_dir: Path) -> dict[str, str]:
    """Environment variables for CLI commands pointing to temp data dir.

    A minimal settings.yaml marks the install as configured so the
    first-run setup gate stays out of the way of non-interactive tests.
    (Engine config lives in data_dir/settings.yaml since the 2026-06
    config unification; previously these tests passed only because the
    gate saw the developer's real cli.yaml outside the temp dir.)
    """
    (data_dir / "settings.yaml").write_text("setup_completed: true\n", encoding="utf-8")
    config_dir = data_dir.parent / "config"
    config_dir.mkdir(exist_ok=True)
    return {
        "CHAOSCYPHER_DATA_DIR": str(data_dir),
        # Hermetic config dir: without this, tests read the dev machine's
        # real cli.yaml (and historically one wrote it — test debris there
        # masked real failures for months).
        "CHAOSCYPHER_CONFIG_DIR": str(config_dir),
        "LOG_LEVEL": "WARNING",
    }


def invoke_cli(runner: CliRunner, args: list[str], **kwargs):
    """Invoke CLI with sys.argv patched for LazyGroup compatibility.

    LazyGroup checks sys.argv to decide whether to load real commands
    vs stubs. CliRunner doesn't set sys.argv, so we patch it.
    """
    from chaoscypher_cli.__main__ import main

    fake_argv = ["chaoscypher", *args]
    with patch.object(sys, "argv", fake_argv):
        return runner.invoke(main, args, **kwargs)


@pytest.fixture
def run_cli(cli_runner: CliRunner):
    """Fixture that returns invoke_cli bound to the runner."""

    def _run(args: list[str], **kwargs):
        return invoke_cli(cli_runner, args, **kwargs)

    return _run


@pytest.fixture
def sample_txt(sample_data_dir: str) -> Path:
    """Path to sample.txt test file."""
    return Path(sample_data_dir) / "sample.txt"


@pytest.fixture
def sample_pdf(sample_data_dir: str) -> Path:
    """Path to sample.pdf test file."""
    return Path(sample_data_dir) / "sample.pdf"


@pytest.fixture
def seed_ccx(e2e_fixtures_dir: str) -> Path:
    """Path to seed.ccx fixture file."""
    return Path(e2e_fixtures_dir) / "seed.ccx"
