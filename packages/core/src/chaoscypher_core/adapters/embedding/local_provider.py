# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LocalEmbeddingProvider — local CPU embedding via sentence-transformers.

Runs embedding models locally on CPU, independent of any LLM provider.
Implements EmbeddingProviderProtocol for use with the embedding abstraction layer.
Supports lazy model loading, MRL truncation, and async thread offload.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


# Must be set before tqdm is first imported by any dependency
os.environ.setdefault("TQDM_DISABLE", "1")

import structlog

from chaoscypher_core.models import BatchEmbedResult, EmbedResult
from chaoscypher_core.ports.embedding import EmbeddingHealthStatus


logger = structlog.get_logger(__name__)


def _suppress_third_party_noise() -> None:
    """Silence noisy third-party loggers and progress bars.

    Suppresses HuggingFace unauthenticated warnings, sentence-transformers
    info logs, tqdm progress bars from transformers weight loading, and
    sentence-transformers batch encoding progress bars.
    """
    # Silence third-party loggers that bypass structlog
    for name in (
        "sentence_transformers",
        "huggingface_hub",
        "transformers",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)

    # Disable all tqdm progress bars (Loading weights, Batches)
    os.environ["TQDM_DISABLE"] = "1"
    # Disable HuggingFace progress bars
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    # Prevent tokenizer parallelism fork warning
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    # Set transformers library verbosity to errors only
    try:
        import transformers

        transformers.logging.set_verbosity_error()
    except ImportError:
        pass


class LocalEmbeddingProvider:
    """Local CPU embedding provider using sentence-transformers.

    Loads a HuggingFace model lazily on first use and runs all encoding
    on a background thread to keep the async event loop free.

    Implements ``EmbeddingProviderProtocol`` for integration with the
    embedding abstraction layer.

    Args:
        model_name: HuggingFace model ID (e.g. "Snowflake/snowflake-arctic-embed-l-v2.0").
        vector_dimensions: Target output dimensions (MRL truncation).
        cache_dir: Directory for downloaded model files.

    Example:
        provider = LocalEmbeddingProvider(
            model_name="Qwen/Qwen3-Embedding-0.6B",
            vector_dimensions=1024,
            cache_dir=Path("/data/models/embeddings"),
        )
        result = await provider.embed("quantum entanglement")
        print(len(result.embedding))  # 1024

    """

    def __init__(self, model_name: str, vector_dimensions: int, cache_dir: Path) -> None:
        """Initialize local embedding provider configuration.

        Args:
            model_name: HuggingFace model ID.
            vector_dimensions: Target vector dimensions for MRL truncation.
            cache_dir: Directory to cache downloaded model files.

        """
        self.model_name = model_name
        self.vector_dimensions = vector_dimensions
        self.cache_dir = cache_dir
        self._model: Any = None
        self._model_loaded: bool = False

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier."""
        return "local"

    @classmethod
    def from_settings(cls, settings: EngineSettings) -> LocalEmbeddingProvider:
        """Create a LocalEmbeddingProvider from EngineSettings.

        Args:
            settings: EngineSettings instance with embedding.model,
                search.vector_dimensions, and paths.data_dir.

        Returns:
            Configured LocalEmbeddingProvider instance.

        """
        return cls(
            model_name=settings.embedding.model,
            vector_dimensions=settings.search.vector_dimensions,
            cache_dir=Path(settings.paths.data_dir) / "models" / "embeddings",
        )

    def _ensure_model(self) -> Any:
        """Load model if not already loaded (lazy initialization).

        Returns:
            Loaded SentenceTransformer model instance.

        """
        if not self._model_loaded:
            from sentence_transformers import SentenceTransformer

            _suppress_third_party_noise()
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            t0 = time.perf_counter()
            # Redirect stderr to suppress tqdm "Loading weights" bar from
            # safetensors — it passes disable=False explicitly, ignoring
            # TQDM_DISABLE.  Real errors still raise exceptions.
            with contextlib.redirect_stderr(io.StringIO()):
                self._model = SentenceTransformer(
                    self.model_name,
                    cache_folder=str(self.cache_dir),
                )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self._model_loaded = True
            logger.info(
                "embedding_model_loaded",
                model=self.model_name,
                load_time_ms=elapsed_ms,
                cache_dir=str(self.cache_dir),
            )
        return self._model

    async def embed(self, text: str) -> EmbedResult:
        """Embed a single text string.

        Generates an embedding vector using the configured model,
        truncated to ``vector_dimensions`` via MRL.

        Args:
            text: Text to embed.

        Returns:
            EmbedResult with truncated embedding vector.

        """
        _start = time.monotonic()

        def _encode() -> list[float]:
            """Run the blocking sentence-transformers encode in a worker thread."""
            model = self._ensure_model()
            vector = model.encode(text, show_progress_bar=False)
            return [float(v) for v in vector[: self.vector_dimensions]]

        embedding = await asyncio.to_thread(_encode)

        logger.info(
            "embedding_completed",
            dimensions=len(embedding),
            duration_ms=int((time.monotonic() - _start) * 1000),
        )

        return EmbedResult(
            embedding=embedding,
            provider="local",
        )

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        """Embed multiple texts in one call.

        Uses sentence-transformers internal batching for efficiency.
        All vectors are truncated to ``vector_dimensions``.

        Args:
            texts: List of text strings to embed.
            batch_size: Internal batch size for sentence-transformers.

        Returns:
            BatchEmbedResult with truncated embedding vectors.

        """

        def _encode_batch() -> list[list[float]]:
            """Run the blocking sentence-transformers batch encode in a worker thread."""
            model = self._ensure_model()
            vectors = model.encode(texts, batch_size=batch_size, show_progress_bar=False)
            return [[float(v) for v in vec[: self.vector_dimensions]] for vec in vectors]

        embeddings = await asyncio.to_thread(_encode_batch)

        return BatchEmbedResult(
            embeddings=embeddings,
            total=len(embeddings),
            failed=0,
            provider="local",
        )

    async def download_model(self, model_name: str) -> dict[str, Any]:
        """Download and validate a model from HuggingFace.

        Used by the settings save endpoint for pull-on-save behavior.
        This is a local-only method, not part of EmbeddingProviderProtocol.

        Args:
            model_name: HuggingFace model ID to download.

        Returns:
            Dict with model info (name, dimensions, size).

        Raises:
            ValueError: If model cannot be loaded or is invalid.

        """

        def _download() -> dict[str, Any]:
            """Fetch the model into the cache and return its metadata."""
            from sentence_transformers import SentenceTransformer

            _suppress_third_party_noise()
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            t0 = time.perf_counter()
            with contextlib.redirect_stderr(io.StringIO()):
                model = SentenceTransformer(
                    model_name,
                    cache_folder=str(self.cache_dir),
                )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)

            # Get native dimensions by encoding a test string
            test_vec = model.encode("test", show_progress_bar=False)
            native_dims = len(test_vec)

            return {
                "model_name": model_name,
                "native_dimensions": native_dims,
                "download_time_ms": elapsed_ms,
            }

        try:
            info = await asyncio.to_thread(_download)
            logger.info(
                "embedding_model_downloaded",
                model=model_name,
                native_dimensions=info["native_dimensions"],
                download_time_ms=info["download_time_ms"],
            )
            return info
        except Exception as e:
            logger.exception(
                "embedding_model_download_failed",
                model=model_name,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            msg = f"Failed to download model '{model_name}': {e}"
            raise ValueError(msg) from e

    async def check_health(self) -> EmbeddingHealthStatus:
        """Check local embedding provider health with a cheap probe.

        Fast paths:
        1. If the model is already loaded in memory → healthy.
        2. Otherwise check whether the HuggingFace cache directory on
           disk contains a ``models--<org>--<name>`` folder for this
           model. Presence of the folder means the model is downloaded
           and ready to lazy-load on first embed; absence means a
           download would be required and we can't know ahead of time
           whether network reachability / disk space will cooperate.

        Deliberately does NOT run a real ``embed()`` round-trip: the
        thread-pool encoder slot is the same one production workloads
        use, so a live probe under concurrent embedding load blocks
        behind real work and times out exactly when it's most costly
        to do so.
        """
        t0 = time.perf_counter()

        if self._model_loaded:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return EmbeddingHealthStatus(
                healthy=True,
                provider="local",
                model=self.model_name,
                dimensions=self.vector_dimensions,
                response_time_ms=elapsed_ms,
            )

        # HuggingFace layout: <cache_dir>/models--<org>--<name>/...
        # The hub library normalizes the model id by replacing '/' with
        # '--' and prefixing ``models--``. Match by checking any dir
        # under cache_dir whose name corresponds to self.model_name.
        expected_stem = "models--" + self.model_name.replace("/", "--")
        exists_on_disk = False
        if self.cache_dir.exists():
            try:
                exists_on_disk = any(
                    p.is_dir() and p.name == expected_stem for p in self.cache_dir.iterdir()
                )
            except OSError as e:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                return EmbeddingHealthStatus(
                    healthy=False,
                    provider="local",
                    model=self.model_name,
                    message=f"Embedding cache unreadable: {e}",
                    response_time_ms=elapsed_ms,
                )

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if not exists_on_disk:
            return EmbeddingHealthStatus(
                healthy=False,
                provider="local",
                model=self.model_name,
                message=(
                    f"Embedding model {self.model_name!r} not downloaded to "
                    f"{self.cache_dir}; will fetch on first use."
                ),
                response_time_ms=elapsed_ms,
            )

        return EmbeddingHealthStatus(
            healthy=True,
            provider="local",
            model=self.model_name,
            dimensions=self.vector_dimensions,
            response_time_ms=elapsed_ms,
        )
