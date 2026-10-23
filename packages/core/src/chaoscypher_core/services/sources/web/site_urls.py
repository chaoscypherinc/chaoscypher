# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""URL identity and scope for a site import.

Two spellings, kept apart on purpose:

- :func:`clean_url` is the address a page is fetched and recorded at:
  lowercased scheme and host, default port dropped, fragment dropped, the
  path and query exactly as found. A server that serves only ``/guide/`` (or
  only ``/guide``) is still fetched at the spelling it linked.
- :func:`url_key` is a page's identity for deduplication: the cleaned URL with
  the trailing slash folded (``/guide/`` and ``/guide`` are one page). The
  query is kept: ``?v=2`` is a different page.

:class:`SiteScope` is "the address the user gave": same scheme, same host and
port, and a path under the given path prefix. The crawl never leaves it.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _netloc(parts: SplitResult) -> str:
    """Lowercased ``host[:port]`` with the scheme's default port dropped."""
    host = (parts.hostname or "").lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    port = parts.port
    if port is None or port == _DEFAULT_PORTS.get(parts.scheme.lower()):
        return host
    return f"{host}:{port}"


def clean_url(url: str) -> str:
    """Return *url* as it is fetched and recorded: canonical origin, no fragment.

    Args:
        url: An absolute http(s) URL.

    Returns:
        The URL with a lowercased scheme and host, the default port dropped,
        the fragment dropped, an empty path written as ``/``, and the path
        and query otherwise untouched.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    return urlunsplit((scheme, _netloc(parts), parts.path or "/", parts.query, ""))


def url_key(url: str) -> str:
    """Return the identity two spellings of one page share (trailing slash folded).

    Args:
        url: An absolute http(s) URL.

    Returns:
        :func:`clean_url` of *url* with one trailing ``/`` removed from a
        non-root path.
    """
    parts = urlsplit(clean_url(url))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


#: The Markdown-twin spellings of a page (``/a.md``, ``/a/index.md`` ...), longest first.
_MARKDOWN_TWIN_SUFFIXES = ("/index.html.md", "/index.md", ".html.md", ".md")


def page_identity(url: str) -> str:
    """Return the page *url* names, whichever way the site spells it.

    :func:`url_key` with the Markdown twin folded: ``/a.md`` and
    ``/a/index.md`` (the spelling an llms.txt links) both name ``/a`` (the
    spelling a sitemap or a crawl finds). A site run matches its stored pages
    on it, so a change of discovery method finds the same pages again.

    Args:
        url: An absolute http(s) URL.

    Returns:
        The page's identity (a URL string).
    """
    parts = urlsplit(url_key(url))
    path = parts.path
    for suffix in _MARKDOWN_TWIN_SUFFIXES:
        if path.endswith(suffix):
            # ``/index.md`` at the root is the root, spelled as ``/`` is.
            path = path[: -len(suffix)].rstrip("/") or "/"
            break
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


#: Last path segments that name a file, so the scope is its directory:
#: ``/docs/index.html`` scopes to ``/docs``; ``/3.12`` (a version) does not.
FILE_SUFFIXES = (".html", ".htm", ".md", ".mdx", ".txt", ".php", ".aspx", ".xml", ".json")


@dataclass(frozen=True)
class SiteScope:
    """The part of the web a site import may touch.

    Attributes:
        scheme: ``http`` or ``https``.
        netloc: Lowercased ``host[:port]`` (default port dropped).
        path_prefix: The given path without its trailing slash (``""`` for
            the whole host). A URL is inside when its path equals the prefix
            or continues it after a ``/``: ``/docs`` holds ``/docs/a`` but not
            ``/docsearch``.
    """

    scheme: str
    netloc: str
    path_prefix: str

    @classmethod
    def from_url(cls, url: str) -> SiteScope:
        """Build the scope of the address *url* names.

        A last path segment with a known file suffix (:data:`FILE_SUFFIXES`:
        ``/docs/index.html``, ``/docs/guide.md``) names its directory: the
        scope is ``/docs``. Any other dot stays (``/3.12``, ``/en/5.0`` are
        versions, not files).

        Args:
            url: The address the user gave (or where it redirected to).

        Returns:
            The scope.
        """
        parts = urlsplit(clean_url(url))
        path = parts.path
        segments = path.split("/")
        if not path.endswith("/") and segments[-1].lower().endswith(FILE_SUFFIXES):
            path = "/".join(segments[:-1])
        return cls(scheme=parts.scheme, netloc=parts.netloc, path_prefix=path.rstrip("/"))

    @property
    def origin(self) -> str:
        """``scheme://host[:port]``, where robots.txt and the root llms.txt live."""
        return f"{self.scheme}://{self.netloc}"

    def same_origin(self, url: str) -> bool:
        """Whether *url* has this scope's scheme, host and port (any path)."""
        parts = urlsplit(clean_url(url))
        return parts.scheme == self.scheme and parts.netloc == self.netloc

    def contains(self, url: str) -> bool:
        """Whether *url* is inside the scope (same origin, path under the prefix)."""
        if not self.same_origin(url):
            return False
        path = urlsplit(clean_url(url)).path
        return path == self.path_prefix or path.startswith(f"{self.path_prefix}/")

    def document_url(self, name: str) -> str:
        """The URL of a well-known document (``llms.txt``, ``sitemap.xml``) at the scope's path."""
        return f"{self.origin}{self.path_prefix}/{name}"


__all__ = ["FILE_SUFFIXES", "SiteScope", "clean_url", "page_identity", "url_key"]
