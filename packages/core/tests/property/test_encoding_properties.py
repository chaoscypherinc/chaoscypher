# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Property-based tests for ``chaoscypher_core.utils.encoding.detect_encoding``.

The helper takes a ``Path`` (not raw bytes), tries a fixed cascade
(UTF-8-BOM → UTF-8 → cp1252 → charset-normalizer → chardet → Latin-1 →
UTF-8 with replacement), and returns ``(encoding_used, decoded_text,
replacement_chars_count)``.

Invariants pinned:

1. Empty input does not raise; returns a 3-tuple.
2. Every non-empty byte string yields a non-empty (encoding_label,
   decoded_text, replacement_count) tuple where the label is a non-empty
   string and the count is a non-negative int.
3. Round-trip property: text encoded as UTF-8 round-trips to itself.
4. Round-trip property: bytes that are valid UTF-8 are labelled
   ``"utf-8"`` and the decode matches Python's strict UTF-8 decode.
5. A UTF-8-BOM prefix is stripped and labelled ``"utf-8-bom"``.
6. Latin-1-only bytes (containing one of the cp1252-undefined bytes
   0x81/0x8D/0x8F/0x90/0x9D) fall through to ``"latin-1-fallback"`` and
   decode losslessly (every byte maps to a codepoint).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from chaoscypher_core.utils.encoding import detect_encoding


# --- strategies ------------------------------------------------------------

# Arbitrary unicode text the cascade should round-trip via UTF-8.
_text_strategy = st.text(min_size=0, max_size=2000)

# Arbitrary bytes — exercise the full cascade including bytes that are not
# valid UTF-8.
_bytes_strategy = st.binary(min_size=0, max_size=2000)

# The five cp1252-undefined byte values. Bytes containing one of these
# force the cascade past cp1252 strict.
_CP1252_UNDEFINED = (0x81, 0x8D, 0x8F, 0x90, 0x9D)


def _write_bytes(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --- invariant 1: empty input -----------------------------------------------


def test_detect_encoding_empty_file_does_not_raise(tmp_path: Path) -> None:
    """Empty input returns a 3-tuple without raising."""
    path = _write_bytes(tmp_path, "empty.txt", b"")
    encoding, text, count = detect_encoding(path)
    assert isinstance(encoding, str)
    assert isinstance(text, str)
    assert isinstance(count, int)
    assert text == ""
    assert count == 0


# --- invariant 2: arbitrary bytes return well-formed tuple -------------------


@given(data=_bytes_strategy)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_detect_encoding_returns_well_formed_tuple(tmp_path: Path, data: bytes) -> None:
    """Every input yields ``(non-empty-label, decoded-str, non-negative-int)``."""
    path = _write_bytes(tmp_path, "arb.bin", data)
    encoding, text, count = detect_encoding(path)
    assert isinstance(encoding, str)
    assert encoding != ""
    assert isinstance(text, str)
    assert isinstance(count, int)
    assert count >= 0
    # The replacement-count invariant: only the explicit utf-8-replace
    # path may report > 0 replacements.
    if count > 0:
        assert encoding == "utf-8-replace"


# --- invariant 3 & 4: UTF-8 round-trip --------------------------------------


@given(text=_text_strategy)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_detect_encoding_utf8_round_trip(tmp_path: Path, text: str) -> None:
    """UTF-8-encoded text decodes back to the same string."""
    raw = text.encode("utf-8")
    path = _write_bytes(tmp_path, "utf8.txt", raw)
    encoding, decoded, count = detect_encoding(path)
    # When the text is empty the cascade hits the empty-bytes path; both
    # branches still need to round-trip cleanly.
    assert decoded == text
    assert count == 0
    # Empty input cannot be relabelled — it satisfies UTF-8 strict.
    assert encoding in {"utf-8", "utf-8-bom"} or text == ""


# --- invariant 5: UTF-8-BOM is stripped -------------------------------------


@given(text=_text_strategy)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_detect_encoding_strips_utf8_bom(tmp_path: Path, text: str) -> None:
    """A UTF-8 BOM is stripped from the decoded output and labelled ``utf-8-bom``."""
    raw = b"\xef\xbb\xbf" + text.encode("utf-8")
    path = _write_bytes(tmp_path, "utf8bom.txt", raw)
    encoding, decoded, count = detect_encoding(path)
    assert decoded == text
    assert count == 0
    assert encoding == "utf-8-bom"


# --- invariant 6: Latin-1 fallback for cp1252-undefined bytes ----------------


@given(
    forced=st.sampled_from(_CP1252_UNDEFINED),
    payload=st.binary(min_size=0, max_size=200),
)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_detect_encoding_latin1_fallback_decodes_losslessly(
    tmp_path: Path, forced: int, payload: bytes
) -> None:
    """Bytes containing a cp1252-undefined byte fall through to a strict
    decode (charset-normalizer / chardet / latin-1) and never lose data.

    The exact label varies (charset-normalizer may relabel as cp1251 etc.,
    chardet may tiebreak, latin-1-fallback may fire) — what we pin is that
    the result is *not* the lossy ``utf-8-replace`` path.
    """
    raw = bytes([forced]) + payload
    path = _write_bytes(tmp_path, "latin1.bin", raw)
    encoding, decoded, count = detect_encoding(path)
    # Latin-1 decode of the raw input is the "ground truth" for the
    # lossless reading of those bytes; the cascade should match it byte-
    # for-byte unless charset-normalizer relabelled to a different codec
    # that also decodes strictly.
    latin1 = raw.decode("latin-1")
    if encoding == "latin-1-fallback":
        assert decoded == latin1
    # No matter which strict path won, the lossy utf-8-replace must NOT
    # have fired for input that latin-1 handles cleanly.
    assert encoding != "utf-8-replace"
    assert count == 0


# --- behavioural pin: a few sanity examples we want to lock in -------------


@pytest.mark.parametrize(
    ("payload", "expected_label_prefix"),
    [
        (b"hello world", "utf-8"),
        (b"\xef\xbb\xbfhello", "utf-8-bom"),
        # cp1252 smart-quote sequence ("smart" “ ”).
        (b"\x93hello\x94", "cp1252"),
    ],
)
def test_detect_encoding_known_examples(
    tmp_path: Path, payload: bytes, expected_label_prefix: str
) -> None:
    """Lock in the canonical labels for the three most common inputs."""
    path = _write_bytes(tmp_path, "known.bin", payload)
    encoding, _, _ = detect_encoding(path)
    assert encoding.startswith(expected_label_prefix)
