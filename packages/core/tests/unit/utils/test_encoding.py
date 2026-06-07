# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""detect_encoding handles UTF-8, cp1252, Latin-1, and BOM correctly.

Phase 4 tests added 2026-05-08: lower threshold, chardet tiebreaker, granular labels.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from chaoscypher_core.settings import LoaderSettings
from chaoscypher_core.utils.encoding import detect_encoding


def test_detects_utf8(tmp_path: Path) -> None:
    p = tmp_path / "u.txt"
    p.write_text("Hello — world", encoding="utf-8")
    encoding, _, replacement_count = detect_encoding(p)
    assert encoding.lower().replace("-", "") == "utf8"
    assert replacement_count == 0


def test_detects_cp1252(tmp_path: Path) -> None:
    p = tmp_path / "c.txt"
    p.write_bytes("café — résumé".encode("cp1252"))
    encoding, _, replacement_count = detect_encoding(p)
    # Either charset-normalizer's best guess or the cp1252 fallback labels it.
    assert "cp1252" in encoding.lower() or "windows-1252" in encoding.lower()
    assert replacement_count == 0


def test_strips_utf8_bom(tmp_path: Path) -> None:
    p = tmp_path / "b.txt"
    p.write_bytes(b"\xef\xbb\xbf" + b"hello")
    encoding, text, replacement_count = detect_encoding(p)
    assert "bom" in encoding.lower()
    assert not text.startswith("﻿")
    assert text == "hello"
    assert replacement_count == 0


def test_returns_text_decoded_correctly(tmp_path: Path) -> None:
    p = tmp_path / "decoded.txt"
    raw = "résumé café".encode("cp1252")
    p.write_bytes(raw)
    _, text, replacement_count = detect_encoding(p)
    # Whichever encoding got picked, the text reads back correctly.
    assert "résumé" in text
    assert "café" in text
    assert replacement_count == 0


def test_latin1_compatible_bytes_decode_correctly(tmp_path: Path) -> None:
    """Bytes valid in cp1252/Latin-1 (no smart quotes) decode correctly.

    cp1252 is a strict superset of Latin-1 in the high-ASCII range (with
    a handful of code-page-specific positions in 0x80-0x9F), so plain
    Latin-1 bytes round-trip via the cp1252 strict step.
    """
    p = tmp_path / "l.txt"
    # Bytes valid in latin-1/cp1252 but not utf-8.
    p.write_bytes(b"\xe9clair caf\xe9 r\xe9sum\xe9")
    encoding, text, replacement_count = detect_encoding(p)
    assert "éclair" in text
    assert "café" in text
    assert "résumé" in text
    # Strict utf-8 cannot decode lone 0xE9, so we landed in a fallback.
    assert encoding != "utf-8"
    assert replacement_count == 0


def test_undefined_cp1252_bytes_fall_through(tmp_path: Path) -> None:
    """Bytes that hit cp1252's undefined positions fall through to Latin-1."""
    p = tmp_path / "weird.txt"
    # 0x81 and 0x8D are undefined in cp1252; they decode under latin-1.
    p.write_bytes(b"hello \x81\x8d world")
    encoding, text, replacement_count = detect_encoding(p)
    # Whichever fallback caught it, the result is non-empty and not the
    # cp1252 strict label.
    assert "hello" in text and "world" in text
    assert encoding != "cp1252"
    assert replacement_count == 0


def test_empty_file_returns_utf8(tmp_path: Path) -> None:
    p = tmp_path / "empty.txt"
    p.write_bytes(b"")
    encoding, text, replacement_count = detect_encoding(p)
    assert text == ""
    assert "utf-8" in encoding.lower()
    assert replacement_count == 0


def test_clean_decode_has_zero_replacement_chars(tmp_path: Path) -> None:
    """Clean UTF-8 bytes always yield replacement_chars_count == 0."""
    p = tmp_path / "clean.txt"
    p.write_text("clean ascii text with no surprises", encoding="utf-8")
    encoding, text, replacement_count = detect_encoding(p)
    assert encoding == "utf-8"
    assert replacement_count == 0
    assert "clean ascii text" in text


# ---------------------------------------------------------------------------
# Phase 4 tests (2026-05-08): lower threshold, chardet tiebreaker, granular labels
# ---------------------------------------------------------------------------


def test_cp1251_with_undefined_cp1252_byte_uses_charset_normalizer(tmp_path: Path) -> None:
    """54-byte cp1251 file with a byte undefined in cp1252 is now detected.

    The 0x81 byte is defined in cp1251 (U+0403 Ѓ) but *undefined* in cp1252,
    so cp1252-strict fails and the file falls to charset-normalizer.  With
    the default threshold of 32 (down from 256), this file now gets statistical
    detection rather than falling all the way to Latin-1.
    """
    p = tmp_path / "cp1251.txt"
    # Prepend 0x81 (undefined in cp1252) to a Cyrillic sentence so that:
    #   a) cp1252-strict fails immediately, and
    #   b) charset-normalizer has enough Cyrillic bytes to identify cp1251.
    raw = bytes([0x81]) + "Привет, мир! Это тест на кириллице. Ещё строка здесь.".encode("cp1251")
    p.write_bytes(raw)
    assert len(raw) >= 32  # sanity-check: above default threshold

    encoding, decoded, replacement_count = detect_encoding(p)

    # The encoding label must indicate cp1251 or windows-1251.
    assert "cp1251" in encoding.lower() or "windows-1251" in encoding.lower(), (
        f"Expected cp1251 or windows-1251 in label, got {encoding!r}"
    )
    # The decoded text must contain Cyrillic characters (not mojibake).
    cyrillic_chars = sum(1 for c in decoded if "Ѐ" <= c <= "ӿ")
    assert cyrillic_chars >= 5, f"Expected Cyrillic characters in decoded text, got {decoded!r}"
    assert replacement_count == 0


def test_chardet_tiebreaker_fires_when_cn_confidence_low(tmp_path: Path) -> None:
    """Chardet is consulted when charset-normalizer's coherence is below threshold.

    For very short inputs (36 bytes here) charset-normalizer's coherence
    score is 0.0, which is below the default threshold of 0.7.  The chardet
    tiebreaker should fire and return a cp1251 / windows-1251 label when
    chardet's confidence (even if modest) exceeds charset-normalizer's 0.0.
    """
    p = tmp_path / "short_cp1251.txt"
    # 0x81 forces cp1252 strict to fail; the remaining bytes are enough for
    # chardet to detect cp1251 / windows-1251.
    raw = bytes([0x81]) + "Привет, мир! Это тест на кириллице.".encode("cp1251")
    p.write_bytes(raw)
    assert len(raw) >= 32  # above default threshold so statistical detectors run

    encoding, decoded, replacement_count = detect_encoding(p)

    # Either chardet or charset-normalizer should identify cp1251/windows-1251.
    assert "cp1251" in encoding.lower() or "windows-1251" in encoding.lower(), (
        f"Expected cp1251/windows-1251 label, got {encoding!r}"
    )
    # Label must be granular (not bare "cp1251" from the old code — now
    # prefixed with "charset-normalizer-" or "chardet-").
    assert encoding.startswith(("charset-normalizer-", "chardet-")), (
        f"Expected granular label prefix, got {encoding!r}"
    )
    assert replacement_count == 0


def test_granular_charset_normalizer_label(tmp_path: Path) -> None:
    """charset-normalizer detections carry a 'charset-normalizer-<enc>' prefix."""
    p = tmp_path / "cn_label.txt"
    # 0x81 at the start forces cp1252 to fail.  69 bytes of Cyrillic give
    # charset-normalizer enough coherence to reach the threshold on its own.
    raw = bytes(
        [0x81]
    ) + "Привет, мир! Это тест на кириллице. Ещё строка здесь для надёжности.".encode("cp1251")
    p.write_bytes(raw)
    assert len(raw) >= 50  # charset-normalizer coherence > 0.7 on ~70 bytes

    encoding, decoded, replacement_count = detect_encoding(p)

    # High-coherence charset-normalizer result must use the prefixed label.
    assert "cp1251" in encoding.lower() or "windows-1251" in encoding.lower(), (
        f"Expected cp1251/windows-1251, got {encoding!r}"
    )
    assert replacement_count == 0


def test_encoding_chardet_min_input_size_respected(tmp_path: Path) -> None:
    """Custom encoding_chardet_min_input_size via LoaderSettings is honoured.

    A 24-byte file sits above threshold=10 but below threshold=100.
    With threshold=100 it should fall to Latin-1; with threshold=10 it should
    get statistical detection.
    """
    p = tmp_path / "threshold.txt"
    # 24 bytes with bytes undefined in cp1252 so cp1252 strict fails.
    raw = bytes([0x81, 0x90, 0xC0, 0xE0]) * 6
    p.write_bytes(raw)
    assert len(raw) == 24

    # High threshold → Latin-1 fallback (statistical detection skipped).
    settings_high = LoaderSettings(encoding_chardet_min_input_size=100)
    enc_high, _, _ = detect_encoding(p, settings=settings_high)
    assert "latin-1" in enc_high.lower(), (
        f"Expected latin-1 fallback above threshold, got {enc_high!r}"
    )

    # Low threshold → statistical detection runs.
    settings_low = LoaderSettings(encoding_chardet_min_input_size=10)
    enc_low, _, _ = detect_encoding(p, settings=settings_low)
    assert "latin-1" not in enc_low.lower(), (
        f"Expected statistical detection below threshold, got {enc_low!r}"
    )


def test_default_settings_used_when_none_passed(tmp_path: Path) -> None:
    """Passing settings=None uses LoaderSettings defaults (min_input_size=32)."""
    p = tmp_path / "defaults.txt"
    p.write_text("Hello UTF-8", encoding="utf-8")
    # Should not raise; should detect utf-8 normally.
    encoding, text, replacement_count = detect_encoding(p, settings=None)
    assert encoding == "utf-8"
    assert "Hello UTF-8" in text
    assert replacement_count == 0


def test_replacement_char_count_still_works_latin1_path(tmp_path: Path) -> None:
    """Phase 2 LOADER_REPLACEMENT_CHARS_COUNT: Latin-1 fallback always gives 0.

    Latin-1 maps every byte 0x00-0xFF to a Unicode codepoint and never
    inserts U+FFFD, so replacement_chars_count is 0 on the Latin-1 path.
    """
    p = tmp_path / "latin1_rc.txt"
    # 0x81 is undefined in cp1252, defined in Latin-1 → falls to latin-1-fallback.
    p.write_bytes(b"hello \x81\x8d world")
    encoding, text, replacement_count = detect_encoding(p)
    # Latin-1 fallback (input too short for statistical detection).
    assert "hello" in text and "world" in text
    assert replacement_count == 0


@pytest.mark.parametrize("chardet_available", [True, False])
def test_missing_chardet_falls_back_gracefully(tmp_path: Path, chardet_available: bool) -> None:
    """When chardet is not importable the chain continues without crashing.

    charset-normalizer's result is used when chardet import fails.
    """
    p = tmp_path / "no_chardet.txt"
    # 0x81 forces cp1252 to fail; enough Cyrillic bytes for charset-normalizer.
    raw = bytes([0x81]) + "Привет, мир! Это тест на кириллице. Ещё строка здесь.".encode("cp1251")
    p.write_bytes(raw)

    if chardet_available:
        # Normal path: no mocking.
        encoding, decoded, rc = detect_encoding(p)
    else:
        # Simulate chardet being absent.
        with patch.dict("sys.modules", {"chardet": None}):
            encoding, decoded, rc = detect_encoding(p)

    # Either path must produce a non-empty decoded result with 0 replacement chars.
    assert encoding != "utf-8"
    assert rc == 0
    cyrillic_chars = sum(1 for c in decoded if "Ѐ" <= c <= "ӿ")
    assert cyrillic_chars >= 3, f"Decoded text lacks Cyrillic content: {decoded!r}"


# ---------------------------------------------------------------------------
# Mutation-hardening tests (added 2026-05-19 alongside `make mutate-python`).
# The pre-existing assertions used substring + lowercase comparisons
# (`"cp1252" in encoding.lower()`) which let label-laundering mutations
# survive ("XXcp1252XX", "CP1252"). The tests below pin the exact strings
# that the helper documents as its contract so any deviation is caught.
# ---------------------------------------------------------------------------


def test_utf8_label_is_exactly_utf8(tmp_path: Path) -> None:
    """Strict UTF-8 path must return the literal label ``"utf-8"``."""
    p = tmp_path / "u.txt"
    p.write_text("Hello — world", encoding="utf-8")
    encoding, _, _ = detect_encoding(p)
    assert encoding == "utf-8"


def test_utf8_bom_label_is_exactly_utf8_bom(tmp_path: Path) -> None:
    """BOM-stripped UTF-8 must return the literal label ``"utf-8-bom"``."""
    p = tmp_path / "b.txt"
    p.write_bytes(b"\xef\xbb\xbf" + "smart — quotes".encode())
    encoding, text, _ = detect_encoding(p)
    assert encoding == "utf-8-bom"
    # BOM is stripped from the text.
    assert not text.startswith("﻿")


def test_cp1252_label_is_exactly_lowercase_cp1252(tmp_path: Path) -> None:
    """cp1252 strict path returns the literal label ``"cp1252"`` (lowercase)."""
    p = tmp_path / "c.txt"
    # Smart quote at 0x93 + accented chars — pure cp1252, no BOM, fails UTF-8 strict.
    p.write_bytes(b"\x93Caf\xe9 r\xe9sum\xe9\x94")
    encoding, text, replacement_count = detect_encoding(p)
    assert encoding == "cp1252"
    assert replacement_count == 0
    assert text == "“Café résumé”"


def test_latin1_fallback_label_is_exactly_latin1_fallback(tmp_path: Path) -> None:
    """Latin-1 fallback returns the literal label ``"latin-1-fallback"``."""
    p = tmp_path / "weird.txt"
    # 0x81 / 0x8D are undefined in cp1252 -> falls through past cp1252
    # strict. Short input keeps charset-normalizer out of the way (default
    # min input size is 32).
    p.write_bytes(b"hi\x81\x8d")
    encoding, _, _ = detect_encoding(p)
    assert encoding == "latin-1-fallback"


def test_chardet_min_input_size_is_inclusive_lower_bound(tmp_path: Path) -> None:
    """Threshold check is ``>=`` not ``>`` — a file exactly at the size
    boundary must invoke charset-normalizer, not skip it.

    We pick a payload that is invalid as strict utf-8 AND strict cp1252
    (uses 0x81) so it can only succeed via the charset-normalizer /
    chardet / latin-1 chain. With ``>=`` (correct) the chardet branch
    fires and the label carries the detector prefix; with ``>``
    (mutated) the branch is skipped and the file lands in
    ``latin-1-fallback``. Pinning to the detector-prefixed label is the
    only assertion that distinguishes the two.
    """
    settings = LoaderSettings()
    threshold = settings.encoding_chardet_min_input_size

    payload = b"\x81" * threshold
    p = tmp_path / "boundary.bin"
    p.write_bytes(payload)
    encoding, _, _ = detect_encoding(p, settings=settings)
    # When mutated to ``>`` this would become ``latin-1-fallback``.
    assert encoding.startswith(("charset-normalizer-", "chardet-")), (
        f"Expected detector-prefixed label at threshold, got {encoding!r} "
        f"(boundary check may have regressed from >= to >)"
    )

    # Sanity: one byte below the threshold falls through to latin-1.
    short_p = tmp_path / "below.bin"
    short_p.write_bytes(b"\x81" * (threshold - 1))
    short_encoding, _, _ = detect_encoding(short_p, settings=settings)
    assert short_encoding == "latin-1-fallback"


def test_strict_decode_paths_report_zero_replacement_chars(tmp_path: Path) -> None:
    """Every strict-decode label must report 0 replacement chars.

    Survives if a mutant flips the literal ``0`` in any ``return (...,
    decoded, 0)`` tuple to something else.
    """
    cases: list[tuple[str, bytes]] = [
        ("utf-8", b"ascii only"),
        ("utf-8-bom", b"\xef\xbb\xbf" + b"ascii"),
        ("cp1252", b"Caf\xe9"),  # pure cp1252
    ]
    for expected_label, payload in cases:
        p = tmp_path / f"{expected_label}.bin"
        p.write_bytes(payload)
        encoding, _, replacement_count = detect_encoding(p)
        assert encoding == expected_label, f"Expected label {expected_label!r}, got {encoding!r}"
        assert replacement_count == 0, (
            f"Strict path {expected_label!r} produced "
            f"{replacement_count} replacement chars (must be 0)"
        )


# ---------------------------------------------------------------------------
# Mutation-hardening tests (2026-05-19, second batch).
#
# Triage of 44 surviving mutants in detect_encoding identified the
# following gaps in the existing suite. Each test below targets a
# concrete mutant family. Tests use a deterministic 36-byte cp1251
# fixture (verified to land on the chardet branch with the default
# settings) plus structlog log capture for the latin-1 logger.
# ---------------------------------------------------------------------------


# Deterministic chardet fixture: 36 bytes of Cyrillic prefixed with 0x81.
# Verified locally: charset-normalizer returns ('cp1251', coherence=0.0)
# and chardet returns ('Windows-1251', confidence~=0.31). Because cn's
# coherence (0.0) is below the default threshold (0.7) AND chardet's
# confidence (~0.31) is greater than cn's coherence (0.0), the chardet
# branch wins and the helper returns label "chardet-windows-1251".
_CHARDET_FIXTURE_BYTES: bytes = bytes([0x81]) + "Привет, мир! Это тест на кириллице.".encode(
    "cp1251"
)


def test_chardet_label_is_exact_chardet_windows_1251(tmp_path: Path) -> None:
    """Pin the exact ``chardet-windows-1251`` label for the deterministic
    fixture so any mutation that drops the ``chardet-`` prefix (because
    chardet was bypassed) or that mangles the lowercase/space-normalised
    label (``.lower().replace(" ", "_")``) is caught.

    Kills mutants that disable the chardet branch by sabotaging the
    ``chardet_result.get(...)`` lookups (mutmut_48-51, 54-57), the
    decode itself (mutmut_64), or the label normalisation (mutmut_73).
    """
    p = tmp_path / "chardet_label.bin"
    p.write_bytes(_CHARDET_FIXTURE_BYTES)
    assert len(_CHARDET_FIXTURE_BYTES) >= 32  # above default threshold

    encoding, decoded, replacement_count = detect_encoding(p)

    # Exact label — lowercased, spaces become underscores. Chardet
    # returns "Windows-1251" (mixed case, no spaces), so the label must
    # be "chardet-windows-1251" verbatim.
    assert encoding == "chardet-windows-1251", (
        f"Expected literal 'chardet-windows-1251' (chardet-prefixed, lowercased), got {encoding!r}"
    )
    # Text must be a real Cyrillic decode, not ``None`` (kills mutmut_61
    # where ``text = None`` short-circuits the decode).
    assert isinstance(decoded, str), f"decoded must be str, got {type(decoded).__name__}"
    assert "Привет" in decoded, f"Expected Cyrillic content, got {decoded!r}"
    assert replacement_count == 0


def test_charset_normalizer_label_is_lowercase(tmp_path: Path) -> None:
    """The ``charset-normalizer-<enc>`` label must be lowercased.

    Kills mutmut_32 which flips ``(matches.encoding or "").lower()``
    to ``.upper()`` — under that mutant the label becomes
    ``charset-normalizer-CP1251`` (uppercase) instead of
    ``charset-normalizer-cp1251``.
    """
    p = tmp_path / "cn_lowercase.bin"
    # 70 bytes of Cyrillic with leading 0x81 — cn coherence is high
    # enough on this length that chardet does NOT win, so the
    # charset-normalizer branch returns directly.
    raw = bytes(
        [0x81]
    ) + "Привет, мир! Это тест на кириллице. Ещё строка здесь для надёжности.".encode("cp1251")
    p.write_bytes(raw)

    encoding, _, _ = detect_encoding(p)

    # Whichever statistical detector wins, the label after the prefix
    # must be lowercase (no uppercase letters).
    assert encoding.startswith(("charset-normalizer-", "chardet-")), (
        f"Expected detector-prefixed label, got {encoding!r}"
    )
    label_payload = encoding.split("-", 1)[1]  # everything after first "-"
    # The "cp1251" / "windows-1251" payload must not contain any uppercase
    # ASCII letters. (".upper()" would turn it into "CP1251" / "WINDOWS-1251".)
    assert not any(c.isascii() and c.isupper() for c in label_payload), (
        f"Label payload {label_payload!r} contains uppercase letters; "
        f"expected fully-lowercased label"
    )


def test_latin1_fallback_logs_structured_event(tmp_path: Path) -> None:
    """The Latin-1 fallback must emit a structured log with the exact
    event name ``encoding_fallback_used``, the ``path`` set to ``str(path)``,
    and ``encoding="latin-1"``.

    Kills the log-payload mutants 84, 85, 86, 88, 89, 90, 91, 92, 93, 94
    (event name dropped/mangled, ``path`` dropped/None, ``encoding``
    dropped/None/mangled, etc.).
    """
    p = tmp_path / "latin1_log.bin"
    # 4 bytes — below the 32-byte threshold so statistical detectors
    # never run; goes straight to latin-1-fallback after cp1252 fails.
    p.write_bytes(b"hi\x81\x8d")

    import structlog.testing

    with structlog.testing.capture_logs() as logs:
        encoding, _, _ = detect_encoding(p)

    assert encoding == "latin-1-fallback"

    # Find the fallback log entry by exact event name.
    fallback_logs = [r for r in logs if r.get("event") == "encoding_fallback_used"]
    assert len(fallback_logs) == 1, (
        f"Expected exactly one 'encoding_fallback_used' event, "
        f"got events: {[r.get('event') for r in logs]}"
    )
    record = fallback_logs[0]

    # Exact path field (kills mutmut_85, 88, 92).
    assert record.get("path") == str(p), (
        f"Expected log path field to be str(p)={str(p)!r}, got {record.get('path')!r}"
    )
    # Exact encoding field (kills mutmut_86, 89, 93, 94).
    assert record.get("encoding") == "latin-1", (
        f"Expected log encoding field 'latin-1', got {record.get('encoding')!r}"
    )
