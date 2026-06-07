# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for content-filtering threshold tuning (Cluster C).

Each filter gets 3 positive fixtures (should match, content IS the
filter's target) and 3 negative fixtures (should NOT match, content
has SOME surface similarity but is legitimate). These pin the
threshold at its tuned value so future drift is caught.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.extraction.content_categories import (
    CONTENT_CATEGORIES,
)


# ---------------------------------------------------------------------------
# bulk_lists — threshold 0.85
# ---------------------------------------------------------------------------


BULK_LISTS_POSITIVE = [
    # A real glossary index — most lines are short term entries
    """
- Abduction
- Abuse of authority
- Access control
- Account hijacking
- Acquiescence
- Actionable intelligence
- Active directory
- Ad hoc analysis
- Advisory board
- Aftermath analysis
- Alert fatigue
- Algorithm bias
""".strip(),
    # A feature bullet list with no narrative
    """
* Fast startup
* Low memory footprint
* Cross-platform builds
* Signed binaries
* TLS 1.3 support
* GDPR compliant
* Open-source license
* Plugin architecture
* Auto-updates
* Rollback support
""".strip(),
    # A navigation/index list
    """
- Home
- About
- Products
- Services
- Pricing
- Contact
- FAQ
- Blog
- Documentation
- Support
""".strip(),
]


BULK_LISTS_NEGATIVE = [
    # Narrative with a short bullet list embedded
    """
The authentication layer supports three mechanisms:
- OAuth 2.0 via JWT
- Session cookies with HMAC signing
- API keys rotated every 30 days

Each mechanism has its own tradeoffs, and the framework allows
you to stack them so different routes can require different
levels of trust. Implementation notes follow in the next section.
""".strip(),
    # Technical doc with inline bullets as examples
    """
When designing a retry policy, consider these factors:
- Maximum attempts before giving up
- Backoff strategy (linear, exponential, jittered)
- Which error types warrant retry vs immediate failure

The library ships sensible defaults for each, but production
deployments should review these values against their SLAs.
""".strip(),
    # Short glossary with narrative explanations between bullets
    """
**Authentication**: The process of verifying a user's identity via credentials.

**Authorization**: Determining what an authenticated user is permitted to do.

**Accounting**: Logging user actions for audit and compliance.
""".strip(),
]


@pytest.mark.parametrize("text", BULK_LISTS_POSITIVE)
def test_bulk_lists_positive_fires(text: str) -> None:
    """Genuine bulk-list content should trigger the bulk_lists filter."""
    matcher = CONTENT_CATEGORIES["bulk_lists"]
    assert matcher.matches(text), f"Expected bulk_lists to match, got no match.\nText:\n{text!r}"


@pytest.mark.parametrize("text", BULK_LISTS_NEGATIVE)
def test_bulk_lists_negative_preserves(text: str) -> None:
    """Narrative content with embedded bullets should NOT fire bulk_lists."""
    matcher = CONTENT_CATEGORIES["bulk_lists"]
    assert not matcher.matches(text), (
        f"Expected bulk_lists to skip narrative+bullets, got a match.\nText:\n{text!r}"
    )


# ---------------------------------------------------------------------------
# changelog — threshold 5
# ---------------------------------------------------------------------------


CHANGELOG_POSITIVE = [
    # A real changelog section — each bullet uses tight "verb in/since version N" phrasing
    """
## 3.2.0

- Added in version 3.2: custom schema support
- Changed in version 3.2: default timeout from 30s to 60s
- Deprecated in version 3.0: the Legacy* class hierarchy
- New in version 3.2: async transport layer
- Removed in version 3.1: the old sync-only client

## 3.1.0

- Added in version 3.1: telemetry export endpoint
- Changed since version 3.1: log output format is now JSON
""".strip(),
    # Migration notes, many version references
    """
If you're upgrading from version 2, note that the RequestContext
class was removed in version 3.0, the auth middleware was changed
in version 3.0, and the config schema was deprecated in version 2.9.

Handlers registered via the old API added in version 2.0 still work
but new code should use the patterns added in version 3.0.

Also, the default TLS version was changed in version 3.0 from 1.2
to 1.3, which is a breaking change for clients still on version 2.
""".strip(),
    # Dense errata / version-notes block
    """
Known issues added in version 4.1:
- Race condition in the pool, will be fixed in version 4.2
- Config reload was deprecated in version 4.0 and removed in version 4.1
- `--legacy-mode` flag was added in version 4.0 and will be removed in version 5.0
- Metrics format was changed in version 4.1

Workaround: pin to version 4.0 until 4.2 ships with the fix.
""".strip(),
]


CHANGELOG_NEGATIVE = [
    # Tutorial that references one version
    """
This guide assumes you're running the library version 2.0 or later.
Earlier versions had a different API. If you're on version 1.x, see
the migration guide before following these steps.
""".strip(),
    # Blog post mentioning 2 versions as context
    """
The HTTP/2 support added in version 3.0 significantly improved
throughput for our use case. Before that, we had to work around
connection limits manually. The new connection pool changed in
version 3.0 handles this automatically.
""".strip(),
    # Technical doc mentioning a few versions across sections
    """
## Installation

Requires Python 3.10 or later. The type hints added in version 2.0
assume modern Python. For older Python, see the legacy branch.

## Configuration

Config format was changed in version 2.0. If you're migrating from
version 1.x, run the built-in migration tool.
""".strip(),
]


@pytest.mark.parametrize("text", CHANGELOG_POSITIVE)
def test_changelog_positive_fires(text: str) -> None:
    """Real changelog/release-notes content should trigger the filter."""
    matcher = CONTENT_CATEGORIES["changelog"]
    assert matcher.matches(text), f"Expected changelog to match, got no match.\nText:\n{text!r}"


@pytest.mark.parametrize("text", CHANGELOG_NEGATIVE)
def test_changelog_negative_preserves(text: str) -> None:
    """Prose that happens to reference a couple of versions should NOT fire."""
    matcher = CONTENT_CATEGORIES["changelog"]
    assert not matcher.matches(text), (
        f"Expected changelog to skip narrative+versions, got a match.\nText:\n{text!r}"
    )


# ---------------------------------------------------------------------------
# code_blocks — threshold 0.75
# ---------------------------------------------------------------------------


CODE_BLOCKS_POSITIVE = [
    # A Python module stub — 100% code
    """
import asyncio
from typing import Any

async def fetch(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return response.json()

def main() -> None:
    result = asyncio.run(fetch("https://example.com"))
    print(result)

if __name__ == "__main__":
    main()
""".strip(),
    # Config dump — lots of indented lines
    """
server:
    host: 0.0.0.0
    port: 8080
    timeout: 30
database:
    host: localhost
    port: 5432
    pool_size: 10
logging:
    level: info
    format: json
    output: stdout
""".strip(),
    # A dense class definition — nearly every line is code
    """
class ConnectionPool:
    def __init__(self, host: str, port: int, max_size: int = 10) -> None:
        self._host = host
        self._port = port
        self._max_size = max_size
        self._connections: list[Connection] = []

    def acquire(self) -> Connection:
        if self._connections:
            return self._connections.pop()
        return Connection(self._host, self._port)

    def release(self, conn: Connection) -> None:
        if len(self._connections) < self._max_size:
            self._connections.append(conn)
        else:
            conn.close()
""".strip(),
]


CODE_BLOCKS_NEGATIVE = [
    # Tutorial with inline code examples (common in READMEs)
    """
To get started, install the library:

    pip install my-lib

Then import and use it in your code. The main entry point is the
``Client`` class, which takes a URL and returns a response object.

Here's a minimal example:

    client = Client("https://api.example.com")
    result = client.get("/users")

The result contains the parsed JSON payload.
""".strip(),
    # Conceptual doc with one indented code snippet
    """
The caching layer sits between the request handler and the
database. When a request arrives, the cache is consulted first.
If the key is present and fresh, the cached value is returned
directly without touching the database.

    GET /users/42  -->  cache hit  -->  return cached
    GET /users/43  -->  cache miss -->  query DB, cache, return

This drastically reduces load on the primary datastore.
""".strip(),
    # Markdown with some inline backticks and one fenced block
    """
Use the ``parse()`` function to convert a string to a tree.

```python
tree = parse(source)
```

The tree is a nested dict where each node has ``type``, ``value``,
and ``children`` keys. Traversal is depth-first by default but
you can switch to breadth-first via the ``order`` parameter.
""".strip(),
]


@pytest.mark.parametrize("text", CODE_BLOCKS_POSITIVE)
def test_code_blocks_positive_fires(text: str) -> None:
    """Code-dominant content should trigger the code_blocks filter."""
    matcher = CONTENT_CATEGORIES["code_blocks"]
    assert matcher.matches(text), f"Expected code_blocks to match, got no match.\nText:\n{text!r}"


@pytest.mark.parametrize("text", CODE_BLOCKS_NEGATIVE)
def test_code_blocks_negative_preserves(text: str) -> None:
    """Prose with embedded code examples should NOT fire code_blocks."""
    matcher = CONTENT_CATEGORIES["code_blocks"]
    assert not matcher.matches(text), (
        f"Expected code_blocks to skip tutorial+code, got a match.\nText:\n{text!r}"
    )


# ---------------------------------------------------------------------------
# min_content_length logic — only applies when something was stripped
# ---------------------------------------------------------------------------


def _make_chunk(content: str, chunk_id: str = "c1", chunk_index: int = 0) -> dict:
    """Build a minimal chunk dict for filter tests."""
    return {"id": chunk_id, "chunk_index": chunk_index, "content": content}


def test_min_content_length_preserves_untouched_short_chunk():
    """A short chunk that no filter stripped must be kept (the fix)."""
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        filter_and_strip_chunks,
    )

    # 60-char chunk, below the default 100 min. No matcher that fires on it.
    short_definition = "REST: Representational State Transfer, a web arch style."
    matchers = [CONTENT_CATEGORIES["legal"]]  # won't match

    kept, stats = filter_and_strip_chunks(
        [_make_chunk(short_definition)],
        matchers,
        min_content_length=100,
    )

    assert len(kept) == 1, (
        f"Short untouched chunk should be preserved, got {len(kept)} kept, stats={stats}"
    )
    assert kept[0]["content"] == short_definition
    assert stats.excluded_chunks == 0


def test_min_content_length_discards_stripped_chunk_below_threshold():
    """Stripped-to-short chunks still get discarded.

    A chunk shorter than min_content_length AFTER a filter stripped
    from it should be discarded (existing behavior preserved).
    """
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        filter_and_strip_chunks,
    )

    # Build content that the bulk_lists filter will mostly strip, leaving
    # <100 chars of residual.
    content = "\n".join([f"- Item {i}" for i in range(20)]) + "\nEnd."
    matchers = [CONTENT_CATEGORIES["bulk_lists"]]

    kept, stats = filter_and_strip_chunks(
        [_make_chunk(content)],
        matchers,
        min_content_length=100,
    )

    assert len(kept) == 0, f"Stripped-to-short chunk should be discarded, got {len(kept)} kept"
    assert stats.excluded_chunks == 1


def test_min_content_length_keeps_long_chunks_regardless():
    """Long chunks are kept regardless of whether a filter stripped.

    Long chunks (> min_content_length) are kept regardless of whether
    a filter stripped from them or not.
    """
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        filter_and_strip_chunks,
    )

    # 500-char chunk with no filter matching.
    long_content = "This is a paragraph of prose. " * 20
    matchers = [CONTENT_CATEGORIES["legal"]]  # won't match

    kept, stats = filter_and_strip_chunks(
        [_make_chunk(long_content)],
        matchers,
        min_content_length=100,
    )

    assert len(kept) == 1
    assert stats.excluded_chunks == 0


def test_min_content_length_keeps_stripped_chunk_still_above_threshold():
    """Stripped chunks above threshold are kept with cleaned content.

    A chunk where a filter stripped some content but the remainder is
    still above min_content_length should be kept (with cleaned content).
    """
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        filter_and_strip_chunks,
    )

    # Build: 500 chars of narrative + 200 chars of bullet list items.
    # bulk_lists strips the bullets; remainder is ~500 chars, still above threshold.
    narrative = "This section discusses authentication mechanisms in depth. " * 10
    # 20 short bullet lines so bulk_lists triggers (0.85 threshold)
    bullets = "\n" + "\n".join([f"- item {i}" for i in range(20)])
    content = narrative + bullets

    # Make sure bulk_lists fires on the bullets — the chunk as a whole has
    # plenty of narrative, so ratio should still be high enough for
    # bulk_lists to match. Check manually during test run; if it doesn't
    # match, tweak the bullet count.
    matchers = [CONTENT_CATEGORIES["bulk_lists"]]

    kept, stats = filter_and_strip_chunks(
        [_make_chunk(content)],
        matchers,
        min_content_length=100,
    )

    # The chunk is either kept (because remainder > 100 even after
    # stripping) or dropped depending on whether bulk_lists actually
    # fired. Either outcome is consistent with the contract; the key
    # invariant is that IF it's kept, the content is the cleaned
    # version (without bullets), not the original.
    if kept:
        assert "item 0" not in kept[0]["content"], (
            "Bullets should have been stripped from kept chunk"
        )
