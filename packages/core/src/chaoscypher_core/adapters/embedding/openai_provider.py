# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""OpenAIEmbeddingProvider — cloud embedding via OpenAI Embeddings API.

Generates embeddings through OpenAI's /v1/embeddings endpoint, supporting both
single and batch embedding with optional MRL dimension truncation. Implements
EmbeddingProviderProtocol for use with the embedding abstraction layer.
"""

from __future__ import annotations

import time

import httpx
import structlog

from chaoscypher_core.adapters.embedding._retry import request_with_retry
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.models import BatchEmbedResult, EmbedResult, TokenUsage
from chaoscypher_core.ports.embedding import EmbeddingHealthStatus


logger = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 30.0


class OpenAIEmbeddingProvider:
    """OpenAI cloud embedding provider.

    Generates embeddings via OpenAI's /v1/embeddings endpoint with automatic
    retry, rate-limit handling, and batch support.

    Implements ``EmbeddingProviderProtocol`` for integration with the
    embedding abstraction layer.

    Args:
        model_name: OpenAI model identifier (e.g. "text-embedding-3-large").
        vector_dimensions: Target output dimensions (sent as ``dimensions`` param).
        api_key: OpenAI API key.
        api_base: Optional custom API base URL (defaults to OpenAI).

    Example:
        provider = OpenAIEmbeddingProvider(
            model_name="text-embedding-3-large",
            vector_dimensions=1024,
            api_key="sk-...",
        )
        result = await provider.embed("quantum entanglement")
        print(len(result.embedding))  # 1024

    """

    def __init__(
        self,
        model_name: str,
        vector_dimensions: int,
        api_key: str,
        api_base: str | None = None,
    ) -> None:
        """Initialize OpenAI embedding provider configuration.

        Args:
            model_name: OpenAI model identifier (e.g. "text-embedding-3-large").
            vector_dimensions: Target vector dimensions for MRL truncation.
            api_key: OpenAI API key for authentication.
            api_base: Optional custom API base URL.

        """
        self.model_name = model_name
        self.vector_dimensions = vector_dimensions
        self.api_key = api_key
        self.api_base = (api_base or "https://api.openai.com").rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier."""
        return "openai"

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, creating it on first use."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request_embeddings(
        self, texts: list[str]
    ) -> tuple[list[list[float]], TokenUsage | None]:
        """Send an embedding request to OpenAI with retry logic.

        Retries up to ``_MAX_RETRIES`` times with exponential backoff on
        rate-limit (429) and server errors (5xx). Respects ``Retry-After``
        header when present.

        Args:
            texts: List of text strings to embed.

        Returns:
            Tuple of (embedding vectors sorted by index, optional token usage).

        Raises:
            LLMError: If all retries are exhausted or a non-retryable error occurs.

        """
        payload: dict = {
            "model": self.model_name,
            "input": texts,
            "dimensions": self.vector_dimensions,
        }
        url = f"{self.api_base}/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        client = self._get_client()
        response = await request_with_retry(
            request_fn=lambda: client.post(url, json=payload, headers=headers),
            provider="openai",
            auth_error_codes=(401, 403),
        )

        if response.status_code != 200:
            msg = f"OpenAI embedding failed ({response.status_code}): {response.text}"
            raise LLMError(msg)

        data = response.json()

        # Sort by index to preserve input order
        sorted_data = sorted(data["data"], key=lambda d: d["index"])
        embeddings = [item["embedding"] for item in sorted_data]

        usage: TokenUsage | None = None
        if "usage" in data:
            usage_data = data["usage"]
            usage = TokenUsage(
                input_tokens=usage_data.get("prompt_tokens", 0),
                output_tokens=0,
                total_tokens=usage_data.get("total_tokens", 0),
            )

        return embeddings, usage

    async def embed(self, text: str) -> EmbedResult:
        """Embed a single text string.

        Generates an embedding vector via OpenAI's /v1/embeddings endpoint
        with the configured ``dimensions`` parameter.

        Args:
            text: Text to embed.

        Returns:
            EmbedResult with embedding vector and optional token usage.

        Raises:
            LLMError: If the embedding request fails.

        """
        embeddings, usage = await self._request_embeddings([text])

        return EmbedResult(
            embedding=embeddings[0],
            provider="openai",
            usage=usage,
        )

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        """Embed multiple texts with automatic chunking.

        Sends texts to OpenAI's /v1/embeddings endpoint in chunks of
        ``batch_size``. OpenAI supports up to 2048 inputs per request.

        Args:
            texts: List of text strings to embed.
            batch_size: Number of texts per API call.

        Returns:
            BatchEmbedResult with embedding vectors.

        Raises:
            LLMError: If the batch embedding request fails.

        """
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            embeddings, _ = await self._request_embeddings(chunk)
            all_embeddings.extend(embeddings)

        return BatchEmbedResult(
            embeddings=all_embeddings,
            total=len(all_embeddings),
            failed=0,
            provider="openai",
        )

    async def check_health(self) -> EmbeddingHealthStatus:
        """Check OpenAI embedding provider health with a cheap probe.

        Hits ``GET {api_base}/v1/models`` and verifies the configured
        model appears in the response. Deliberately does NOT run a real
        ``embed()`` round-trip: live probes burn rate-limit budget,
        consume billable tokens, and stack up under load when upstream
        is slow. A model-listing call shares none of that.

        Failure signals still covered: unreachable, bad/missing API key
        (401), server 5xx, and model-not-available. The "model loaded
        but inference broken" edge case is deferred to the first real
        embed call where the error has a clearer surface.
        """
        url = f"{self.api_base}/v1/models"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = httpx.Timeout(5.0, connect=2.0)
        try:
            t0 = time.perf_counter()
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=timeout)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)

            if response.status_code == 401:
                return EmbeddingHealthStatus(
                    healthy=False,
                    provider="openai",
                    model=self.model_name,
                    message="OpenAI API key rejected (401)",
                    response_time_ms=elapsed_ms,
                )
            if response.status_code != 200:
                return EmbeddingHealthStatus(
                    healthy=False,
                    provider="openai",
                    model=self.model_name,
                    message=f"OpenAI /v1/models returned {response.status_code}",
                    response_time_ms=elapsed_ms,
                )

            available = {m.get("id") for m in response.json().get("data", [])}
            if self.model_name not in available:
                return EmbeddingHealthStatus(
                    healthy=False,
                    provider="openai",
                    model=self.model_name,
                    message=(
                        f"Embedding model {self.model_name!r} not available for this OpenAI API key"
                    ),
                    response_time_ms=elapsed_ms,
                )

            return EmbeddingHealthStatus(
                healthy=True,
                provider="openai",
                model=self.model_name,
                dimensions=self.vector_dimensions,
                response_time_ms=elapsed_ms,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            return EmbeddingHealthStatus(
                healthy=False,
                provider="openai",
                model=self.model_name,
                message=f"Connection failed: {e}",
            )
