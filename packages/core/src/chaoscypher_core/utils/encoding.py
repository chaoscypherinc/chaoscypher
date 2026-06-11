# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared encoding-detection helper for text-shaped loaders.

Tries UTF-8 first (the default and what most modern files use), then
cp1252 (Windows Notepad / Excel exports — by far the most common
non-UTF-8 case for Western text), and finally falls through to Latin-1
(which always succeeds). For files that fail strict cp1252 (the five
undefined positions 0x81 / 0x8D / 0x8F / 0x90 / 0x9D, or genuinely
non-Western files) charset-normalizer is consulted as a tie-breaker.
Strips a UTF-8 BOM if present. Records which encoding was used so the
source's ``loader_encoding_used`` quality counter can surface it.

Replaces the assorted hardcoded ``encoding="utf-8", errors="replace"``
/ ``errors="ignore"`` calls previously sprinkled across loaders.
Strict decoding before any replacement so cp1252 / Latin-1 files keep
their characters intact instead of turning every special character
into U+FFFD or silently truncating.

Why cp1252 strict before charset-normalizer: charset-normalizer's
best guess can be overconfident on short snippets and pick obscure
code pages (e.g. cp1125 Cyrillic, utf_16_be on a 13-byte sample) for
files that are plain Western European cp1252. Trying cp1252 strict
first gives deterministic behaviour for the overwhelmingly common
case without needing heuristics on top of a heuristic library.

Three behaviours help short legacy-encoded files (cp1251 Cyrillic,
Shift-JIS Japanese, GB18030 Chinese):

1. The charset-normalizer threshold is configurable via
   ``LoaderSettings.encoding_chardet_min_input_size`` (default 32).
   Short files get statistical detection instead of falling all the
   way to Latin-1.

2. When charset-normalizer's top result has ``coherence`` below
   ``LoaderSettings.encoding_chardet_confidence_threshold`` (default
   0.7), chardet is consulted as a second-opinion detector.  The
   higher-confidence result wins.

3. The ``encoding_used`` label is granular:
   ``charset-normalizer-cp1251``, ``chardet-shift_jis``,
   ``latin-1-fallback``, ``utf-8-replace`` — so the data-quality tab
   can surface exactly which step fired.

``LOADER_REPLACEMENT_CHARS_COUNT`` still counts U+FFFD insertions
correctly; the strict-decode paths all return 0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.settings import LoaderSettings

logger = structlog.get_logger(__name__)


def detect_encoding(  # noqa: C901, PLR0911, PLR0912 - tiered detector: each branch handles a distinct encoding fallback layer (UTF-8/cp1252/charset-normalizer/chardet/latin-1)
    path: Path, *, settings: LoaderSettings | None = None
) -> tuple[str, str, int]:
    """Return ``(encoding_used, decoded_text, replacement_chars_count)`` for a text file.

    Strategy (each step strict — no replacement until the very end):

    1. Strip a UTF-8 BOM if present and try UTF-8.
    2. Try strict UTF-8 on the raw bytes.
    3. Try strict cp1252 (covers Windows-Western files, the most common
       non-UTF-8 case).
    4. Ask ``charset-normalizer`` for an opinion (handles genuinely
       unusual encodings).  When its confidence is below the configured
       threshold, ``chardet`` is consulted as a second-opinion tiebreaker
       and the higher-confidence result wins.
    5. Fall through to Latin-1, which always succeeds (no byte values
       are undefined).
    6. Last resort: UTF-8 with ``errors="replace"`` and a warning log.

    The ``encoding_used`` label uses these forms so the data-quality
    counter can distinguish strict matches from fallbacks:

    - ``"utf-8"`` — strict UTF-8 succeeded.
    - ``"utf-8-bom"`` — file started with a UTF-8 BOM (stripped).
    - ``"cp1252"`` — strict cp1252 (preferred Western fallback).
    - ``"charset-normalizer-<name>"`` — charset-normalizer's best guess
      (e.g. ``"charset-normalizer-cp1251"``).
    - ``"chardet-<name>"`` — chardet's tiebreaker result (e.g.
      ``"chardet-shift_jis"``).  Only emitted when charset-normalizer's
      confidence was below the configured threshold.
    - ``"latin-1-fallback"`` — strict decode succeeded via the final
      Latin-1 fallback.
    - ``"utf-8-replace"`` — every strict candidate failed; UTF-8 with
      replacement was used and a warning was logged.

    ``replacement_chars_count`` is 0 for all strict-decode paths.  It is
    non-zero only for ``"utf-8-replace"``, where it counts the U+FFFD
    (``�``) characters inserted for each undecodeable byte.  Callers
    should surface this via ``LOADER_REPLACEMENT_CHARS_COUNT`` so operators
    can tell whether a file decoded cleanly or absorbed silent damage.

    Parameters
    ----------
    path:
        Path to the file to decode.
    settings:
        Optional :class:`~chaoscypher_core.settings.LoaderSettings` used
        to tune the charset-normalizer min-input-size threshold and the
        chardet confidence threshold.  When *None* a default-constructed
        instance is used (i.e. all defaults apply).
    """
    if settings is None:
        from chaoscypher_core.settings import LoaderSettings

        settings = LoaderSettings()

    # Pre-flight size guard: refuse to materialise a multi-GB file into RAM.
    # Mirrors ``check_loader_file_size`` (which takes ``EngineSettings``) so
    # any caller that comes in directly with ``LoaderSettings`` (rst / html /
    # json loaders, web search adapter, …) is covered too. See
    # ``LoaderSettings.max_disk_bytes`` for the rationale and the trade-off
    # (default 500 MiB; set to None to disable in trusted local CLI flows).
    if settings.max_disk_bytes is not None:
        size = path.stat().st_size
        if size > settings.max_disk_bytes:
            from chaoscypher_core.exceptions import LoaderFileTooLargeError

            raise LoaderFileTooLargeError(
                filename=path.name,
                actual_bytes=size,
                max_bytes=settings.max_disk_bytes,
            )

    raw = path.read_bytes()

    # UTF-8 BOM: strip + decode strict.
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return ("utf-8-bom", raw[3:].decode("utf-8"), 0)
        except UnicodeDecodeError:
            # Mismatched BOM (rare); fall through to the rest of the chain.
            pass

    # Try strict UTF-8.
    try:
        return ("utf-8", raw.decode("utf-8"), 0)
    except UnicodeDecodeError:
        pass

    # Try strict cp1252. Five byte values (0x81, 0x8D, 0x8F, 0x90, 0x9D)
    # are undefined in cp1252; if the file uses one of those bytes the
    # decode raises and we fall through. For the overwhelmingly common
    # Western-Windows-export case (smart quotes, em-dash, accented
    # vowels) cp1252 wins deterministically.
    try:
        return ("cp1252", raw.decode("cp1252"), 0)
    except UnicodeDecodeError:
        pass

    # Ask charset-normalizer for non-trivial inputs only. The threshold is
    # now configurable (default 32, down from the previous hardcoded 256) so
    # short legacy-encoded files (cp1251 Cyrillic, Shift-JIS Japanese, …)
    # get statistical detection rather than falling all the way to Latin-1.
    # Imported lazily so the rest of core doesn't pay the import cost
    # when the helper isn't used.
    if len(raw) >= settings.encoding_chardet_min_input_size:
        try:
            from charset_normalizer import from_bytes
        except ImportError:  # pragma: no cover - dep is mandatory in core
            from_bytes = None  # type: ignore[assignment]

        if from_bytes is not None:
            matches = from_bytes(raw).best()
            if matches is not None:
                cn_encoding = (matches.encoding or "").lower()
                cn_coherence = float(matches.coherence)

                if cn_encoding and cn_encoding not in {"utf-8", "utf_8", "ascii"}:
                    # Skip relabelling as utf-8/ascii: those already failed
                    # strict above, so charset-normalizer is recommending a
                    # tolerant decode which we don't want.

                    if cn_coherence < settings.encoding_chardet_confidence_threshold:
                        # charset-normalizer is not confident — consult chardet
                        # as a second-opinion tiebreaker.  Use whichever
                        # detector returns higher confidence, but only if the
                        # chardet result strictly decodes.
                        try:
                            import chardet

                            chardet_result = chardet.detect(raw)
                            chardet_encoding = chardet_result.get("encoding")
                            chardet_confidence = float(chardet_result.get("confidence") or 0.0)
                            if chardet_encoding and chardet_confidence > cn_coherence:
                                try:
                                    text = raw.decode(chardet_encoding, errors="strict")
                                    chardet_label = chardet_encoding.lower().replace(" ", "_")
                                    return (f"chardet-{chardet_label}", text, 0)
                                except UnicodeDecodeError, LookupError:
                                    pass
                        except ImportError:  # pragma: no cover - chardet is transitively present
                            pass

                    # charset-normalizer result (either high-confidence, or
                    # chardet tiebreaker failed / lost).
                    try:
                        return (f"charset-normalizer-{cn_encoding}", raw.decode(cn_encoding), 0)
                    except UnicodeDecodeError, LookupError:
                        pass

    # Latin-1 always succeeds for any byte string (every byte 0x00-0xFF
    # maps to a Unicode codepoint). Use it as the last strict fallback.
    latin1_text: str | None
    try:
        latin1_text = raw.decode("latin-1")
    except UnicodeDecodeError:
        latin1_text = None  # pragma: no cover - Latin-1 cannot fail
    if latin1_text is not None:
        logger.info(
            "encoding_fallback_used",
            path=str(path),
            encoding="latin-1",
        )
        return ("latin-1-fallback", latin1_text, 0)

    # Truly last resort: UTF-8 with replacement. Latin-1 cannot fail in
    # CPython, so this path is defensive.
    replacement_text = raw.decode("utf-8", errors="replace")  # pragma: no cover
    replacement_chars_count = replacement_text.count("�")  # pragma: no cover
    logger.warning(  # pragma: no cover
        "encoding_replacement_used",
        path=str(path),
        replacement_chars_count=replacement_chars_count,
        message=(
            "Could not decode strictly with any candidate encoding; "
            "falling back to UTF-8 with replacement."
        ),
    )
    return ("utf-8-replace", replacement_text, replacement_chars_count)  # pragma: no cover


__all__ = ["detect_encoding"]
