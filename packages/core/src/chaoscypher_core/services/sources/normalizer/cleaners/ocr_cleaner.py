# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""OCR artifact cleaner for scanned document output.

Handles common OCR (Optical Character Recognition) artifacts:
- Gibberish/random character sequences
- Duplicate paragraphs (common in multi-column layouts)
- Short noise lines (page numbers, headers misread)
- Common OCR character substitution errors

Uses a hybrid approach for short line validation:
1. Structural words whitelist (BY, TO, THE, etc.) - always kept
2. Roman numerals (I, II, III, IV, etc.) - always kept
3. Dictionary validation via pyspellchecker - real words kept
4. Known OCR artifacts (common noise patterns) - always removed

Example:
    from chaoscypher_core.services.sources.normalizer.cleaners import OCRCleaner
    from chaoscypher_core.services.sources.normalizer.models import NormalizerSettings

    settings = NormalizerSettings(enable_ocr_cleaning=True)
    cleaner = OCRCleaner(settings)

    content = '''
    i
    Hi
    ie f

    BY
    THREE INITIATES

    THE KYBALION

    A study of hermetic philosophy.
    '''

    result = cleaner.clean(content)
    # result.content has "i", "Hi", "ie f" removed but keeps "BY" and other real words
    # result.lines_removed > 0 reflects every gibberish / page-artifact drop
    # result.paragraphs_deduplicated > 0 when duplicate paragraphs were collapsed

"""

import hashlib
import re
from collections import Counter
from typing import TYPE_CHECKING, ClassVar

import structlog
from rapidfuzz import fuzz

from chaoscypher_core.plugins.base import PluginMetadata
from chaoscypher_core.services.sources.normalizer.cleaners.base import CleanerResult


if TYPE_CHECKING:
    from chaoscypher_core.settings import NormalizerSettings


logger = structlog.get_logger(__name__)


# Lazy-loaded spell checker instance (loaded once on first use)
_spell_checker = None
_spell_checker_warned = False


def _get_spell_checker():  # type: ignore[no-untyped-def]
    """Get or create the spell checker instance (lazy loading).

    Returns:
        SpellChecker instance or None if unavailable.

    """
    global _spell_checker, _spell_checker_warned
    if _spell_checker is None:
        try:
            from spellchecker import SpellChecker

            _spell_checker = SpellChecker()
            logger.debug("spell_checker_loaded")
        except ImportError:
            if not _spell_checker_warned:
                logger.warning(
                    "pyspellchecker_not_installed",
                    message="Install pyspellchecker for improved OCR cleaning",
                )
                _spell_checker_warned = True
            return None  # Return None instead of setting to False
    return _spell_checker if _spell_checker else None


def _compute_simhash(text: str) -> int:
    """Compute a 64-bit simhash fingerprint for fuzzy matching.

    Locality-sensitive hash where similar texts produce hashes with low
    Hamming distance. Used to efficiently pre-filter fuzzy duplicate
    candidates before expensive SequenceMatcher comparison.

    Args:
        text: Text to compute hash for.

    Returns:
        64-bit simhash fingerprint as integer.

    """
    import hashlib

    tokens = text.lower().split()
    if not tokens:
        return 0

    # Weight vector: +1 for set bits, -1 for unset bits
    weights = [0] * 64
    for token in tokens:
        h = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
        for i in range(64):
            if h & (1 << i):
                weights[i] += 1
            else:
                weights[i] -= 1

    # Threshold: positive → 1, else → 0
    fingerprint = 0
    for i in range(64):
        if weights[i] > 0:
            fingerprint |= 1 << i
    return fingerprint


def _hamming_distance(h1: int, h2: int) -> int:
    """Count differing bits between two hashes.

    Args:
        h1: First hash value.
        h2: Second hash value.

    Returns:
        Number of differing bits (Hamming distance).

    """
    return bin(h1 ^ h2).count("1")


class OCRCleaner:
    r"""Cleaner for OCR artifacts and scanning errors.

    Applies OCR-specific cleaning operations:
    1. Remove short gibberish lines (using hybrid word validation)
    2. Remove duplicate paragraphs (common from column misdetection)
    3. Fix common OCR character errors (optional)
    4. Remove page artifacts (headers, footers, page numbers)

    Hybrid Word Validation:
    - Structural words (BY, TO, THE, etc.) are always kept
    - Roman numerals (I, II, III, IV, etc.) are always kept
    - Dictionary words (via pyspellchecker) are kept
    - Known OCR artifacts are always removed

    Attributes:
        settings: Configuration controlling cleaning thresholds.

    Example:
        cleaner = OCRCleaner(NormalizerSettings(min_line_length=5))

        text = "i\\nHi\\nie f\\nBY\\nActual content here."
        result = cleaner.clean(text)
        # result.content has "i", "Hi", "ie f" removed; "BY" kept as structural word

    """

    metadata: ClassVar[PluginMetadata] = PluginMetadata(
        name="ocr_cleaner",
        version="1.0.0",
        description="Removes OCR artifacts, filters gibberish, deduplicates lines.",
        priority=10,
    )

    # Workstream 5.5 (2026-05-07): the OCR cleaner is OCR-style cleanup —
    # it has no business running on plain ``.txt`` / ``.md`` / source-code
    # content where short tokens like ``git`` / ``npm`` / ``K8s`` are
    # legitimate identifiers. The set below enumerates every loader-level
    # ``extraction_method`` value that comes through an OCR-flavoured
    # pipeline. Plain text loaders set no ``extraction_method`` (or set
    # ``read_text``); HTML / Office / markdown loaders set their own
    # values; none of them belong here.
    #
    # Both the plan-declared canonical names (``pypdf_extract`` /
    # ``vision_llm``) and the values today's loaders actually emit
    # (``pypdf`` / ``vision`` / ``vision_pending``) are accepted so this
    # predicate works regardless of which W6/W7 loader rewrite lands first.
    OCR_DERIVED_METHODS: ClassVar[frozenset[str]] = frozenset(
        {
            # Plan-declared canonical names (preferred going forward).
            "pypdf_extract",
            "ocr_tesseract",
            "vision_llm",
            "image_ocr",
            # Names today's loaders actually emit. Drop these once W6/W7
            # rewrites them to the canonical set above.
            "pypdf",
            "vision",
            "vision_pending",
        }
    )

    # Structural words that commonly appear alone on lines in documents
    # These are ALWAYS kept regardless of length (case-insensitive)
    STRUCTURAL_WORDS: ClassVar[set[str]] = {
        # Attribution/dedication words
        "by",
        "to",
        "for",
        "from",
        "with",
        # Articles (when used as titles)
        "the",
        "a",
        "an",
        # Conjunctions/transitions
        "and",
        "or",
        "but",
        "nor",
        "yet",
        "so",
        "as",
        "if",
        # Common document labels
        "no",
        "vs",
        "re",
        "ch",
        "pt",
        "vol",
        "ed",
        "rev",
        # Prepositions used structurally
        "in",
        "on",
        "at",
        "of",
        "up",
        "out",
        "off",
        # Other common short words in documents
        "is",
        "it",
        "be",
        "we",
        "he",
        "me",
        "my",
        "do",
        "go",
        "am",
        "us",
        "oh",
        "ok",
        "id",
    }

    # Roman numerals pattern (I, II, III, IV, V, VI, VII, VIII, IX, X, etc.)
    # Matches uppercase Roman numerals up to 39 (XXXIX)
    ROMAN_NUMERAL_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$",
        re.IGNORECASE,
    )

    # Known OCR artifacts - short strings that are ALWAYS noise
    # These appear frequently in scanned documents as misreads
    # Even though some might be "words", they're almost always OCR errors
    OCR_ARTIFACTS: ClassVar[set[str]] = {
        # Single characters that are usually noise (pipe/line misreads)
        "i",
        "l",
        "1",
        "|",
        # Common 2-char OCR misreads
        "ii",
        "ll",
        "il",
        "li",
        "hi",
        "ih",
        # Random short sequences that appear in scans
        "ie",
        "ei",
        "fi",
        "ti",
        "ri",
        "ir",
        # Fragments from page edges
        "f",
        "t",
        "r",
        "n",
        "m",
    }

    # Patterns that indicate OCR noise even if they contain real letters
    OCR_NOISE_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        # Single letter followed by space and another single letter
        re.compile(r"^[a-z]\s+[a-z]$", re.IGNORECASE),
        # Just spaces between single chars: "i e f"
        re.compile(r"^([a-z]\s+){2,}[a-z]?$", re.IGNORECASE),
        # Repeated single characters: "iii" but not Roman numerals
        re.compile(r"^([^ivxlcdmIVXLCDM])\1+$"),
    ]

    def __init__(self, settings: NormalizerSettings) -> None:
        """Initialize the OCR cleaner.

        Args:
            settings: Normalizer settings controlling cleaner behavior.

        """
        self.settings = settings

    @property
    def name(self) -> str:
        """Return the cleaner name."""
        return "ocr_cleaner"

    def applies_to(self, metadata: dict | None) -> bool:
        """Whether this cleaner should fire for content with this metadata.

        Workstream 5.5 (2026-05-07): the OCR cleaner is scoped to OCR-style
        content — PDF text extraction, Tesseract, vision-LLM-derived text,
        and image OCR. Everything else (plain ``.txt`` / ``.md``, HTML
        loaders, Office loaders, …) skips this cleaner entirely so short
        identifiers like ``git`` / ``npm`` / ``K8s`` survive normalization.

        Phase 7 audit-remediation (2026-05-09): the ``enable_ocr_cleaning``
        flag is now checked here as the primary gate. When the flag is
        ``False`` the predicate returns ``False`` immediately so the
        normalizer service skips this cleaner entirely — the user knob does
        what its name says. Previously the only flag check lived inside
        ``clean()``, which is unreachable in normal pipeline operation
        because the service gates cleaners by ``applies_to`` before calling
        ``clean()``.

        Args:
            metadata: Per-document metadata supplied by the loader. The
                relevant key is ``extraction_method``; missing or unknown
                values disable the cleaner (fail-safe: don't run on
                unidentified content).

        Returns:
            ``True`` only when ``enable_ocr_cleaning`` is set and
            ``metadata.get("extraction_method")`` names an OCR-style
            pipeline.
        """
        # Phase 7 audit-remediation (2026-05-09): real kill switch. When the
        # global / domain-resolved enable_ocr_cleaning flag is False, the
        # predicate returns False short-circuiting cleaner execution.
        if not self.settings.enable_ocr_cleaning:
            return False
        if not metadata:
            return False
        method = metadata.get("extraction_method")
        if not isinstance(method, str):
            return False
        return method in self.OCR_DERIVED_METHODS

    def clean(self, content: str, metadata: dict | None = None) -> CleanerResult:
        """Clean OCR artifacts from content.

        Applies OCR cleaning operations based on settings:
        1. Remove gibberish lines (short, low alpha ratio)
        2. Remove duplicate paragraphs
        3. Clean page artifacts

        Args:
            content: The OCR-extracted text content to clean.
            metadata: Optional metadata (may contain 'content_type' hint).

        Returns:
            :class:`CleanerResult` with the cleaned content, ops list, and
            quality counts: ``lines_removed`` is the sum of gibberish-line
            drops and page-artifact drops, ``paragraphs_deduplicated`` is
            the duplicate-paragraph drop count, and ``chars_removed`` is
            the net before/after length delta (clamped at 0).

        """
        if not content:
            return CleanerResult(content=content)

        operations: list[str] = []
        result = content
        lines_removed_total = 0
        paragraphs_deduplicated_total = 0

        # Step 1: Remove gibberish/noise lines
        result, removed_count = self._remove_gibberish_lines(result)
        if removed_count > 0:
            operations.append(f"gibberish_removal:{removed_count}")
            lines_removed_total += removed_count

        # Step 2: Remove duplicate paragraphs
        if self.settings.enable_duplicate_removal:
            result, dup_count = self._remove_duplicate_paragraphs(result)
            if dup_count > 0:
                operations.append(f"duplicate_removal:{dup_count}")
                paragraphs_deduplicated_total += dup_count

        # Step 3: Remove page artifacts (headers/footers patterns)
        result, artifact_count = self._remove_page_artifacts(result)
        if artifact_count > 0:
            operations.append(f"artifact_removal:{artifact_count}")
            lines_removed_total += artifact_count

        chars_removed = max(0, len(content) - len(result))

        if operations:
            logger.debug(
                "ocr_cleaning_complete",
                operations=operations,
                original_length=len(content),
                cleaned_length=len(result),
                lines_removed=lines_removed_total,
                paragraphs_deduplicated=paragraphs_deduplicated_total,
                chars_removed=chars_removed,
            )

        return CleanerResult(
            content=result,
            ops=operations,
            lines_removed=lines_removed_total,
            paragraphs_deduplicated=paragraphs_deduplicated_total,
            chars_removed=chars_removed,
        )

    def _remove_gibberish_lines(self, text: str) -> tuple[str, int]:
        """Remove lines that appear to be gibberish or noise.

        Uses hybrid validation approach with batch spell checking:
        1. Check if line is a known OCR artifact → remove
        2. Check if line matches OCR noise patterns → remove
        3. Check if line is a structural word → keep
        4. Check if line is a Roman numeral → keep
        5. Check if line passes spell check (batched) → keep
        6. Check traditional heuristics (length, alpha ratio) → keep/remove

        Args:
            text: Text with potential gibberish lines.

        Returns:
            Tuple of (cleaned_text, lines_removed_count).

        """
        lines = text.split("\n")

        # First pass: collect words that need spell checking
        words_to_check: list[str] = []
        line_words_map: dict[int, str] = {}  # line_index -> word to check

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or len(stripped) >= self.settings.min_line_length:
                continue

            # Extract word for potential spell check
            word = re.sub(r"[^\w\s]", "", stripped).strip().lower()
            if not word:
                continue

            # Quick checks that don't need spell checking
            if word in self.OCR_ARTIFACTS:
                continue
            if any(pattern.match(stripped) for pattern in self.OCR_NOISE_PATTERNS):
                continue
            if self._is_structural_short_line(stripped):
                continue
            if word in self.STRUCTURAL_WORDS:
                continue
            if stripped and self.ROMAN_NUMERAL_PATTERN.match(stripped):
                continue

            # This word needs spell checking
            words_to_check.append(word)
            line_words_map[idx] = word

        # Batch spell check all words at once
        unknown_words: set[str] = set()
        if words_to_check:
            spell = _get_spell_checker()
            if spell is not None:
                unknown_words = spell.unknown(words_to_check)

        # Second pass: filter lines using batch results
        cleaned_lines: list[str] = []
        removed_count = 0

        for idx, line in enumerate(lines):
            stripped = line.strip()

            # Keep empty lines (paragraph separators)
            if not stripped:
                cleaned_lines.append(line)
                continue

            # Use hybrid validation for short lines
            if len(stripped) < self.settings.min_line_length:
                # Check if this line had a word that was spell-checked
                if idx in line_words_map:
                    word = line_words_map[idx]
                    # Keep if it's a known dictionary word
                    if word not in unknown_words:
                        cleaned_lines.append(line)
                    else:
                        removed_count += 1
                elif self._is_valid_short_line_fast(stripped):
                    cleaned_lines.append(line)
                else:
                    removed_count += 1
                continue

            # For longer lines, use traditional validation
            # Check alpha ratio
            alpha_ratio = self._calculate_alpha_ratio(stripped)
            if alpha_ratio < self.settings.min_alpha_ratio:
                removed_count += 1
                continue

            # Check for gibberish patterns
            if self._is_gibberish_pattern(stripped):
                removed_count += 1
                continue

            cleaned_lines.append(line)

        return "\n".join(cleaned_lines), removed_count

    def _is_valid_short_line_fast(self, line: str) -> bool:
        """Fast validation for short lines (no spell check).

        Used in the second pass of _remove_gibberish_lines() for lines
        that weren't collected in the first pass (e.g., empty word after
        punctuation stripping). Skips dictionary check.

        Args:
            line: The short line to validate.

        Returns:
            True if line should be kept, False if it should be removed.

        """
        word = re.sub(r"[^\w\s]", "", line).strip().lower()

        # Check known OCR artifacts
        if word in self.OCR_ARTIFACTS:
            return False

        # Check OCR noise patterns
        if any(pattern.match(line) for pattern in self.OCR_NOISE_PATTERNS):
            return False

        # Check structural markdown elements
        if self._is_structural_short_line(line):
            return True

        # Check structural words whitelist
        if word in self.STRUCTURAL_WORDS:
            return True

        # Check Roman numerals
        stripped_line = line.strip()
        if stripped_line and self.ROMAN_NUMERAL_PATTERN.match(stripped_line):
            return True

        # No spell check - if we got here, it's unknown (remove)
        return False

    def _is_structural_short_line(self, line: str) -> bool:
        """Check if a short line is structural (headers, markers).

        Args:
            line: The line to check.

        Returns:
            True if the line appears to be structural.

        """
        # Markdown headers
        if re.match(r"^#{1,6}\s+\S", line):
            return True

        # List markers
        if re.match(r"^[-*+•]\s*\S", line):
            return True

        # Numbered lists
        if re.match(r"^\d+[.)]\s*\S", line):
            return True

        # Page separators (horizontal rules)
        return bool(re.match(r"^[-_=*]{3,}$", line))

    def _calculate_alpha_ratio(self, text: str) -> float:
        """Calculate ratio of alphabetic characters in text.

        Args:
            text: Text to analyze.

        Returns:
            Ratio of alphabetic characters (0.0-1.0).

        """
        if not text:
            return 0.0

        alpha_count = sum(1 for c in text if c.isalpha())
        # Count alphanumeric + spaces as "valid" characters
        valid_count = sum(1 for c in text if c.isalnum() or c.isspace())

        # Use valid_count as denominator to be lenient with punctuation
        if valid_count == 0:
            return 0.0

        return alpha_count / valid_count

    def _is_gibberish_pattern(self, line: str) -> bool:
        """Check if line matches common gibberish patterns.

        Common OCR gibberish patterns include:
        - Random consonant clusters without vowels
        - Excessive punctuation or special characters
        - Mixed case with no word boundaries

        Args:
            line: Line to check.

        Returns:
            True if line appears to be gibberish.

        """
        # Skip if line is too short to analyze reliably
        if len(line) < 10:
            return False

        # Check for excessive consonant clusters (no vowels)
        consonant_clusters = re.findall(r"[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]{5,}", line)
        if consonant_clusters:
            # Ratio of consonant cluster chars to total
            cluster_chars = sum(len(c) for c in consonant_clusters)
            if cluster_chars / len(line) > 0.3:
                return True

        # Check for random capitalization pattern (alternating case)
        if re.search(r"([A-Z][a-z]){4,}|([a-z][A-Z]){4,}", line):
            words = line.split()
            # Only flag if words don't look like normal mixed case
            weird_words = sum(1 for w in words if re.match(r"^[A-Z][a-z][A-Z]", w))
            if weird_words > len(words) * 0.5:
                return True

        return False

    def _remove_duplicate_paragraphs(self, text: str) -> tuple[str, int]:
        """Remove duplicate or near-duplicate paragraphs with hash optimization.

        Uses a three-phase approach for O(n) performance instead of O(n²):
        1. Exact MD5 hash check - catches identical duplicates instantly
        2. Normalized hash check - catches case/whitespace variations
        3. Simhash fuzzy matching - catches OCR character errors

        Only falls back to expensive SequenceMatcher for paragraphs that
        pass the simhash pre-filter (Hamming distance < threshold).

        Args:
            text: Text with potential duplicate paragraphs.

        Returns:
            Tuple of (deduplicated_text, duplicates_removed_count).

        """
        paragraphs = re.split(r"\n\n+", text)

        if len(paragraphs) <= 1:
            return text, 0

        seen_exact: set[str] = set()  # Exact content hashes
        seen_normalized: set[str] = set()  # Normalized hashes
        seen_fuzzy: list[tuple[int, str]] = []  # (simhash, text) for fuzzy matching
        unique_paragraphs: list[str] = []
        duplicate_count = 0

        for para in paragraphs:
            para_stripped = para.strip()
            if not para_stripped:
                continue

            # Phase A: Exact hash check (O(1))
            exact_hash = hashlib.sha256(para_stripped.encode()).hexdigest()
            if exact_hash in seen_exact:
                duplicate_count += 1
                continue

            # Phase B: Normalized hash for case/whitespace variations
            normalized = " ".join(para_stripped.lower().split())
            norm_hash = hashlib.sha256(normalized.encode()).hexdigest()
            if norm_hash in seen_normalized:
                duplicate_count += 1
                continue

            # Phase C: Simhash for fuzzy matching (handles OCR character errors)
            is_duplicate = False
            para_simhash = _compute_simhash(para_stripped)

            # Only check recent paragraphs (duplicates are usually nearby)
            for seen_hash, seen_para in seen_fuzzy[-50:]:
                # Hamming distance < 10 bits means ~85% similar
                if _hamming_distance(para_simhash, seen_hash) < 10:
                    # Confirm with rapidfuzz (compiled C++/Rust) for edge cases
                    similarity = fuzz.ratio(para_stripped, seen_para) / 100.0
                    if similarity >= self.settings.duplicate_similarity_threshold:
                        is_duplicate = True
                        duplicate_count += 1
                        break

            if not is_duplicate:
                seen_exact.add(exact_hash)
                seen_normalized.add(norm_hash)
                seen_fuzzy.append((para_simhash, para_stripped))
                unique_paragraphs.append(para)

        return "\n\n".join(unique_paragraphs), duplicate_count

    def _remove_page_artifacts(self, text: str) -> tuple[str, int]:
        """Remove common page artifacts (headers, footers, page numbers).

        Detects and removes:
        - Standalone page numbers
        - Repeated header/footer patterns
        - Copyright lines repeated on each page

        Args:
            text: Text with potential page artifacts.

        Returns:
            Tuple of (cleaned_text, artifacts_removed_count).

        """
        lines = text.split("\n")
        cleaned_lines: list[str] = []
        removed_count = 0

        # Detect repeated short lines (likely headers/footers)
        line_counts: Counter[str] = Counter(
            line.strip()
            for line in lines
            if line.strip()
            and len(line.strip()) < self.settings.ocr_page_artifact_candidate_max_length
        )

        # Lines appearing at or above the repeat threshold are likely artifacts
        artifact_lines = {
            line
            for line, count in line_counts.items()
            if count >= self.settings.ocr_page_artifact_min_repeats
            and len(line) < self.settings.ocr_page_artifact_max_line_length
        }

        # Common page number patterns
        page_patterns = [
            r"^\s*-?\s*\d{1,4}\s*-?\s*$",  # Just numbers: 1, -1-, 42
            r"^\s*page\s+\d+\s*$",  # "Page 1"
            r"^\s*p\.?\s*\d+\s*$",  # "p. 1" or "p 1"
        ]

        for line in lines:
            stripped = line.strip()

            # Remove detected artifact lines
            if stripped in artifact_lines:
                removed_count += 1
                continue

            # Remove page number patterns
            is_page_number = any(
                re.match(pattern, stripped, re.IGNORECASE) for pattern in page_patterns
            )
            if is_page_number:
                removed_count += 1
                continue

            cleaned_lines.append(line)

        return "\n".join(cleaned_lines), removed_count


__all__ = ["OCRCleaner"]
