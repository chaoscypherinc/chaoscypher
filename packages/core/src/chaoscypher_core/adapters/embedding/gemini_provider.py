# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GeminiEmbeddingProvider — cloud embedding via Google Gemini API.

Generates embeddings through Google's Gemini embedding endpoints, supporting
both single and batch embedding with optional output dimensionality control.
Implements EmbeddingProviderProtocol for use with the embedding abstraction layer.
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
_GEMINI_BATCH_LIMIT = 100


class GeminiEmbeddingProvider:
    """Google Gemini cloud embedding provider.

    Generates embeddings via Gemini's embedContent and batchEmbedContents
    endpoints with automatic retry and rate-limit handling.

    Implements ``EmbeddingProviderProtocol`` for integration with the
    embedding abstraction layer.

    Args:
        model_name: Gemini model identifier (e.g. "gemini-embedding-001").
        vector_dimensions: Target output dimensions (sent as ``outputDimensionality``).
        api_key: Google API key.
        api_base: Optional custom API base URL (defaults to Google).

    Example:
        provider = GeminiEmbeddingProvider(
            model_name="gemini-embedding-001",
            vector_dimensions=1024,
            api_key="AIza...",
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
        """Initialize Gemini embedding provider configuration.

        Args:
            model_name: Gemini model identifier (e.g. "gemini-embedding-001").
            vector_dimensions: Target vector dimensions for output dimensionality.
            api_key: Google API key for authentication.
            api_base: Optional custom API base URL.

        """
        self.model_name = model_name
        self.vector_dimensions = vector_dimensions
        self.api_key = api_key
        self.api_base = (api_base or "https://generativelanguage.googleapis.com").rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier."""
        return "gemini"

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

    def _model_path(self) -> str:
        """Build the full model path for Gemini API requests.

        Returns:
            Model path string (e.g. "models/gemini-embedding-001").

        """
        if self.model_name.startswith("models/"):
            return self.model_name
        return f"models/{self.model_name}"

    async def _request_with_retry(self, url: str, payload: dict, headers: dict) -> dict:
        """Send an HTTP POST request with retry logic.

        Args:
            url: Full request URL.
            payload: JSON request body.
            headers: HTTP headers.

        Returns:
            Parsed JSON response dictionary.

        Raises:
            LLMError: If retries are exhausted or a non-retryable error occurs.
        """
        client = self._get_client()
        response = await request_with_retry(
            request_fn=lambda: client.post(url, json=payload, headers=headers),
            provider="gemini",
            auth_error_codes=(401, 403),
        )

        if response.status_code != 200:
            msg = f"Gemini embedding failed ({response.status_code}): {response.text}"
            raise LLMError(msg)

        return response.json()  # type: ignore[no-any-return]

    async def embed(self, text: str) -> EmbedResult:
        """Embed a single text string.

        Generates an embedding vector via Gemini's embedContent endpoint
        with the configured ``outputDimensionality`` parameter.

        Args:
            text: Text to embed.

        Returns:
            EmbedResult with embedding vector.

        Raises:
            LLMError: If the embedding request fails.

        """
        model_path = self._model_path()
        url = f"{self.api_base}/v1beta/{model_path}:embedContent"
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload: dict = {
            "model": model_path,
            "content": {"parts": [{"text": text}]},
            "outputDimensionality": self.vector_dimensions,
        }

        data = await self._request_with_retry(url, payload, headers)
        embedding = data["embedding"]["values"]

        return EmbedResult(
            embedding=embedding,
            provider="gemini",
        )

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        """Embed multiple texts with automatic chunking.

        Sends texts to Gemini's batchEmbedContents endpoint in chunks.
        Gemini's batch limit is 100 per request; ``batch_size`` is capped
        accordingly.

        Args:
            texts: List of text strings to embed.
            batch_size: Number of texts per API call (capped at 100).

        Returns:
            BatchEmbedResult with embedding vectors.

        Raises:
            LLMError: If the batch embedding request fails.

        """
        effective_batch_size = min(batch_size, _GEMINI_BATCH_LIMIT)
        model_path = self._model_path()
        url = f"{self.api_base}/v1beta/{model_path}:batchEmbedContents"
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), effective_batch_size):
            chunk = texts[i : i + effective_batch_size]
            requests = [
                {
                    "model": model_path,
                    "content": {"parts": [{"text": text}]},
                    "outputDimensionality": self.vector_dimensions,
                }
                for text in chunk
            ]
            payload: dict = {"requests": requests}

            data = await self._request_with_retry(url, payload, headers)
            all_embeddings.extend(emb["values"] for emb in data["embeddings"])

        return BatchEmbedResult(
            embeddings=all_embeddings,
            total=len(all_embeddings),
            failed=0,
            provider="gemini",
        )

    async def check_health(self) -> EmbeddingHealthStatus:
        """Check Gemini embedding provider health with a cheap probe.

        Hits ``GET {api_base}/v1beta/models?key=…`` and verifies the
        configured model appears in the response. Deliberately does NOT
        run a real ``embed()`` round-trip: live probes burn Google's
        quota, consume billable tokens, and stack up under load when
        upstream is slow. A model-listing call shares none of that.

        Gemini returns model ids as ``models/<name>``; we match the
        configured ``model_name`` against either the full id or its
        trailing segment so both naming conventions work.
        """
        url = f"{self.api_base}/v1beta/models"
        params = {"key": self.api_key}
        timeout = httpx.Timeout(5.0, connect=2.0)
        try:
            t0 = time.perf_counter()
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=timeout)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)

            if response.status_code in (401, 403):
                return EmbeddingHealthStatus(
                    healthy=False,
                    provider="gemini",
                    model=self.model_name,
                    message=f"Gemini API key rejected ({response.status_code})",
                    response_time_ms=elapsed_ms,
                )
            if response.status_code != 200:
                return EmbeddingHealthStatus(
                    healthy=False,
                    provider="gemini",
                    model=self.model_name,
                    message=f"Gemini /v1beta/models returned {response.status_code}",
                    response_time_ms=elapsed_ms,
                )

            available: set[str] = set()
            for m in response.json().get("models", []):
                full_id = m.get("name", "")
                available.add(full_id)
                tail = full_id.rsplit("/", 1)[-1] if "/" in full_id else full_id
                available.add(tail)
            if self.model_name not in available:
                return EmbeddingHealthStatus(
                    healthy=False,
                    provider="gemini",
                    model=self.model_name,
                    message=(
                        f"Embedding model {self.model_name!r} not available for this Gemini API key"
                    ),
                    response_time_ms=elapsed_ms,
                )

            return EmbeddingHealthStatus(
                healthy=True,
                provider="gemini",
                model=self.model_name,
                dimensions=self.vector_dimensions,
                response_time_ms=elapsed_ms,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            return EmbeddingHealthStatus(
                healthy=False,
                provider="gemini",
                model=self.model_name,
                message=f"Connection failed: {e}",
            )
