# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for LocalEmbeddingProvider download + on-disk health paths.

Complements ``test_local_provider.py`` (embed / batch / loaded-model health)
with:

* ``download_model`` success — returns native dimensions and timing.
* ``download_model`` failure — wraps the underlying error in ValueError.
* ``check_health`` when the model is NOT loaded:
  - cache folder present on disk → healthy.
  - cache folder absent → unhealthy with a "not downloaded" message.
  - ``iterdir`` raising OSError → unhealthy with an "unreadable" message.

``SentenceTransformer`` and ``asyncio.to_thread`` are patched at the source
path so no real model download or thread offload occurs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch


if TYPE_CHECKING:
    from pathlib import Path

import pytest

from chaoscypher_core.adapters.embedding.local_provider import LocalEmbeddingProvider
from chaoscypher_core.ports.embedding import EmbeddingHealthStatus


@pytest.fixture
def provider(tmp_path: Path) -> LocalEmbeddingProvider:
    """LocalEmbeddingProvider pointed at an isolated tmp cache dir."""
    return LocalEmbeddingProvider(
        model_name="acme/test-model",
        vector_dimensions=4,
        cache_dir=tmp_path / "models",
    )


# ---------------------------------------------------------------------------
# download_model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_model_success_returns_metadata(
    provider: LocalEmbeddingProvider,
) -> None:
    """A successful download reports the native dims and the model name."""
    fake_model = MagicMock()
    # encode("test") returns a 6-dim vector → native_dimensions == 6.
    fake_model.encode.return_value = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]

    with patch(
        "sentence_transformers.SentenceTransformer",
        return_value=fake_model,
    ):
        info = await provider.download_model("acme/test-model")

    assert info["model_name"] == "acme/test-model"
    assert info["native_dimensions"] == 6
    assert "download_time_ms" in info


@pytest.mark.asyncio
async def test_download_model_failure_wrapped_in_value_error(
    provider: LocalEmbeddingProvider,
) -> None:
    """A loader exception is logged and re-raised as ValueError."""
    with patch(
        "sentence_transformers.SentenceTransformer",
        side_effect=RuntimeError("model not found on hub"),
    ):
        with pytest.raises(ValueError, match="Failed to download model"):
            await provider.download_model("acme/bogus-model")


# ---------------------------------------------------------------------------
# check_health (model not loaded → disk inspection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_health_folder_present_is_healthy(
    provider: LocalEmbeddingProvider,
) -> None:
    """A matching ``models--<org>--<name>`` folder on disk reports healthy."""
    assert not provider._model_loaded
    provider.cache_dir.mkdir(parents=True, exist_ok=True)
    # HuggingFace cache layout: models--acme--test-model
    (provider.cache_dir / "models--acme--test-model").mkdir()

    result = await provider.check_health()

    assert isinstance(result, EmbeddingHealthStatus)
    assert result.healthy is True
    assert result.provider == "local"
    assert result.dimensions == 4


@pytest.mark.asyncio
async def test_check_health_folder_absent_is_unhealthy(
    provider: LocalEmbeddingProvider,
) -> None:
    """No matching cache folder reports unhealthy with a 'not downloaded' note."""
    assert not provider._model_loaded
    provider.cache_dir.mkdir(parents=True, exist_ok=True)
    # Unrelated folder present, but not the expected model stem.
    (provider.cache_dir / "models--other--model").mkdir()

    result = await provider.check_health()

    assert result.healthy is False
    assert result.message is not None
    assert "not downloaded" in result.message


@pytest.mark.asyncio
async def test_check_health_iterdir_oserror_is_unhealthy(
    provider: LocalEmbeddingProvider,
) -> None:
    """An OSError while scanning the cache dir reports unhealthy gracefully."""
    assert not provider._model_loaded
    provider.cache_dir.mkdir(parents=True, exist_ok=True)

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise OSError("permission denied")

    with patch.object(type(provider.cache_dir), "iterdir", _boom):
        result = await provider.check_health()

    assert result.healthy is False
    assert result.message is not None
    assert "unreadable" in result.message
