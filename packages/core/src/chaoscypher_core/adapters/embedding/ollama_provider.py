# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""OllamaEmbeddingProvider — GPU-accelerated embedding via Ollama HTTP API.

Generates embeddings through Ollama's /api/embed endpoint, supporting both
single and batch embedding with automatic MRL truncation. Implements
EmbeddingProviderProtocol for use with the embedding abstraction layer.
"""

from __future__ import annotations

import time

import httpx
import structlog

from chaoscypher_core.adapters.embedding._retry import request_with_retry
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.models import BatchEmbedResult, EmbedResult
from chaoscypher_core.ports.embedding import EmbeddingHealthStatus


logger = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 30.0


class OllamaEmbeddingProvider:
    """Ollama GPU-accelerated embedding provider.

    Generates embeddings via Ollama's /api/embed endpoint with automatic
    retry, MRL truncation, and batch support.

    Implements ``EmbeddingProviderProtocol`` for integration with the
    embedding abstraction layer.

    Args:
        model_name: Ollama model tag (e.g. "qwen3-embedding:0.6b").
        vector_dimensions: Target output dimensions (MRL truncation).
        base_url: Ollama server base URL. Required — pass explicitly
            from ``settings.embedding`` (the factory already does this
            via ``_resolve_ollama_base_url``).

    Example:
        provider = OllamaEmbeddingProvider(
            model_name="qwen3-embedding:0.6b",
            vector_dimensions=1024,
            base_url="http://localhost:11434",
        )
        result = await provider.embed("quantum entanglement")
        print(len(result.embedding))  # 1024

    """

    def __init__(
        self,
        model_name: str,
        vector_dimensions: int,
        base_url: str,
    ) -> None:
        """Initialize Ollama embedding provider configuration.

        Args:
            model_name: Ollama model tag (e.g. "qwen3-embedding:0.6b").
            vector_dimensions: Target vector dimensions for MRL truncation.
            base_url: Ollama server base URL. Required parameter — the
                previous silent ``"http://localhost:11434"`` default masked
                configuration mistakes in production. Callers should pass
                from ``get_settings().embedding`` (or an equivalent
                resolved URL) so operator overrides are honoured.

        Raises:
            TypeError: If ``base_url`` is omitted (no longer a parameter
                with a default value).

        """
        self.model_name = model_name
        self.vector_dimensions = vector_dimensions
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier."""
        return "ollama"

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, creating it on first use."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request_embeddings(self, input_data: str | list[str]) -> list[list[float]]:
        """Send an embedding request to Ollama with retry logic.

        Retries up to ``_MAX_RETRIES`` times with exponential backoff on
        transient failures (5xx status codes and connection errors).
        Timeout scales with batch size to prevent false timeouts on large
        batches.

        Args:
            input_data: Single text string or list of text strings.

        Returns:
            List of raw embedding vectors from Ollama.

        Raises:
            LLMError: If all retries are exhausted or a non-retryable error occurs.

        """
        payload = {"model": self.model_name, "input": input_data}
        url = f"{self.base_url}/api/embed"

        # Scale timeout with batch size: base 30s + 0.1s per text
        batch_count = len(input_data) if isinstance(input_data, list) else 1
        timeout = _DEFAULT_TIMEOUT + batch_count * 0.1

        client = self._get_client()
        response = await request_with_retry(
            request_fn=lambda: client.post(url, json=payload, timeout=timeout),
            provider="ollama",
        )

        if response.status_code != 200:
            msg = f"Ollama embedding failed ({response.status_code}): {response.text}"
            raise LLMError(msg)

        data = response.json()
        return data["embeddings"]  # type: ignore[no-any-return]

    def _truncate(self, vector: list[float]) -> list[float]:
        """Truncate a vector to the configured dimensions (MRL).

        Args:
            vector: Raw embedding vector from Ollama.

        Returns:
            Vector truncated to ``vector_dimensions``.

        """
        return vector[: self.vector_dimensions]

    async def embed(self, text: str) -> EmbedResult:
        """Embed a single text string.

        Generates an embedding vector via Ollama's /api/embed endpoint,
        truncated to ``vector_dimensions`` via MRL.

        Args:
            text: Text to embed.

        Returns:
            EmbedResult with truncated embedding vector.

        Raises:
            LLMError: If the embedding request fails.

        """
        raw_embeddings = await self._request_embeddings(text)
        embedding = self._truncate(raw_embeddings[0])

        return EmbedResult(
            embedding=embedding,
            provider="ollama",
        )

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        """Embed multiple texts with automatic chunking.

        Sends texts to Ollama's /api/embed endpoint in chunks of
        ``batch_size``. All vectors are truncated to ``vector_dimensions``.

        Args:
            texts: List of text strings to embed.
            batch_size: Number of texts per API call.

        Returns:
            BatchEmbedResult with truncated embedding vectors.

        Raises:
            LLMError: If the batch embedding request fails.

        """
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            raw_embeddings = await self._request_embeddings(chunk)
            all_embeddings.extend(self._truncate(vec) for vec in raw_embeddings)

        return BatchEmbedResult(
            embeddings=all_embeddings,
            total=len(all_embeddings),
            failed=0,
            provider="ollama",
        )

    async def check_health(self) -> EmbeddingHealthStatus:
        """Check Ollama embedding provider health with a cheap probe.

        Hits ``GET {base_url}/api/tags`` and verifies the configured model
        is listed among installed models. Deliberately does NOT run a
        real ``embed()`` round-trip: under concurrent load the embedding
        model can be evicted from VRAM by a chat/extraction model, and
        a functional embed probe then has to wait on a model swap, which
        routinely exceeds the health-check timeout. A tag probe shares
        no Ollama capacity with inference, so it stays fast and accurate
        under load.

        Failure signals still covered:
        - Server unreachable → ConnectError → unhealthy
        - Server 5xx on /api/tags → unhealthy
        - Configured model not installed → unhealthy with explicit message
        - Slow /api/tags response (Ollama overloaded) → captured via
          ``response_time_ms`` so upstream dashboards can still trend

        Intentionally not covered (deferred to the first real embed):
        - Model loaded but weights corrupt / inference broken. That
          failure has a clearer surface at the real call site.
        """
        url = f"{self.base_url}/api/tags"
        timeout = httpx.Timeout(5.0, connect=2.0)
        try:
            t0 = time.perf_counter()
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=timeout)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)

            if response.status_code != 200:
                return EmbeddingHealthStatus(
                    healthy=False,
                    provider="ollama",
                    model=self.model_name,
                    message=f"Ollama /api/tags returned {response.status_code}",
                    response_time_ms=elapsed_ms,
                )

            installed_names = {m.get("name") for m in response.json().get("models", [])}
            if self.model_name not in installed_names:
                return EmbeddingHealthStatus(
                    healthy=False,
                    provider="ollama",
                    model=self.model_name,
                    message=f"Embedding model {self.model_name!r} not installed in Ollama",
                    response_time_ms=elapsed_ms,
                )

            return EmbeddingHealthStatus(
                healthy=True,
                provider="ollama",
                model=self.model_name,
                dimensions=self.vector_dimensions,
                response_time_ms=elapsed_ms,
            )
        except httpx.ConnectError:
            return EmbeddingHealthStatus(
                healthy=False,
                provider="ollama",
                model=self.model_name,
                message=f"Not reachable at {self.base_url}",
            )
        except httpx.TimeoutException:
            return EmbeddingHealthStatus(
                healthy=False,
                provider="ollama",
                model=self.model_name,
                message=f"Timed out at {self.base_url}",
            )
