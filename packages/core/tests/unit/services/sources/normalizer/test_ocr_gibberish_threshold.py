# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``normalizer.gibberish_threshold`` drives the OCR cleaner's scrambled-case check.

The key is documented as overridable in ``settings.yaml`` and per domain;
it must change which lines the cleaner flags, not just sit in the model.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.normalizer.cleaners.ocr_cleaner import OCRCleaner
from chaoscypher_core.settings import NormalizerSettings


# One scrambled-case word in four: a 25% share.
_LINE = "AbAbAbAb normal words here"


@pytest.mark.parametrize(
    ("threshold", "flagged"),
    [
        (0.5, False),  # the default: under half the words scrambled -> kept
        (0.2, True),  # stricter: a quarter is already too many
        (0.25, False),  # strictly greater-than: exactly at the share -> kept
    ],
)
def test_gibberish_threshold_sets_the_scrambled_word_share(threshold: float, flagged: bool) -> None:
    cleaner = OCRCleaner(NormalizerSettings(gibberish_threshold=threshold))
    assert cleaner._is_gibberish_pattern(_LINE) is flagged
