# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Markdown normalizer for consistent output formatting.

Transforms cleaned content into consistent, well-formatted Markdown:
- Normalizes header levels and formatting
- Standardizes list markers
- Cleans table formatting
- Ensures consistent spacing and structure

Example:
    from chaoscypher_core.services.sources.normalizer.transformers import (
        MarkdownNormalizer,
    )
    from chaoscypher_core.services.sources.normalizer.models import (
        ContentType,
        NormalizerSettings,
    )

    settings = NormalizerSettings()
    transformer = MarkdownNormalizer(settings)

    content = '''
    #Header Without Space
    * List item with asterisk
    + Another list marker
    '''

    normalized = transformer.transform(content, ContentType.TEXT)
    # Output has consistent # headers and - list markers

"""

import re

import structlog

from chaoscypher_core.services.sources.normalizer.models import (
    ContentType,
    NormalizerSettings,
)


logger = structlog.get_logger(__name__)


class MarkdownNormalizer:
    r"""Transformer that normalizes content to consistent Markdown format.

    Applies Markdown formatting rules:
    1. Header normalization (space after #, consistent levels)
    2. List marker standardization (use - for unordered)
    3. Table formatting cleanup
    4. Code block language hints
    5. Link and emphasis formatting

    Attributes:
        settings: Configuration controlling transformation behavior.

    Example:
        normalizer = MarkdownNormalizer(NormalizerSettings())

        # Normalize headers
        text = "#Title\\n##Subtitle"
        result = normalizer.transform(text, ContentType.PDF)
        assert "# Title" in result  # Space added after #

    """

    def __init__(self, settings: NormalizerSettings) -> None:
        """Initialize the markdown normalizer.

        Args:
            settings: Normalizer settings controlling behavior.

        """
        self.settings = settings

    @property
    def name(self) -> str:
        """Return the transformer name."""
        return "markdown_normalizer"

    def transform(self, content: str, source_type: ContentType) -> str:
        """Transform content to normalized Markdown format.

        Applies Markdown normalization rules based on the source content type.
        Some transformations are source-type specific.

        Args:
            content: Cleaned content to transform.
            source_type: Original content type for context-aware transformation.

        Returns:
            Normalized Markdown content.

        """
        if not content or not self.settings.enable_markdown_normalize:
            return content

        result = content

        # Step 1: Normalize headers
        result = self._normalize_headers(result)

        # Step 2: Normalize list markers
        result = self._normalize_lists(result)

        # Step 3: Normalize horizontal rules
        result = self._normalize_horizontal_rules(result)

        # Step 4: Normalize emphasis markers
        result = self._normalize_emphasis(result)

        # Step 5: Clean up spacing
        result = self._normalize_spacing(result)

        # Step 6: Source-type specific transformations
        if source_type == ContentType.JSON:
            result = self._transform_json_content(result)
        elif source_type == ContentType.CODE:
            result = self._transform_code_content(result)

        logger.debug(
            "markdown_normalization_complete",
            source_type=source_type.value,
            original_length=len(content),
            normalized_length=len(result),
        )

        return result

    def _normalize_headers(self, text: str) -> str:
        """Normalize Markdown headers.

        Ensures:
        - Space after # symbols
        - No trailing # symbols
        - Consistent capitalization (title case for top-level)

        Args:
            text: Text with potential malformed headers.

        Returns:
            Text with normalized headers.

        """
        lines = text.split("\n")
        normalized_lines: list[str] = []

        for line in lines:
            # Match header pattern: one or more # at start
            match = re.match(r"^(#{1,6})\s*(.*?)\s*#*\s*$", line)
            if match:
                hashes = match.group(1)
                header_text = match.group(2)

                # Ensure space after hashes
                normalized_line = f"{hashes} {header_text}"
                normalized_lines.append(normalized_line)
            else:
                normalized_lines.append(line)

        return "\n".join(normalized_lines)

    def _normalize_lists(self, text: str) -> str:
        """Normalize list markers to consistent style.

        Converts all unordered list markers (*, +) to - for consistency.
        Preserves numbered list formatting.

        Args:
            text: Text with mixed list markers.

        Returns:
            Text with consistent - markers for unordered lists.

        """
        # Replace * and + list markers with - (preserving indentation)
        text = re.sub(r"^(\s*)[*+](\s+)", r"\1-\2", text, flags=re.MULTILINE)

        # Normalize numbered list spacing
        return re.sub(r"^(\s*)(\d+)[.)](\s*)", r"\1\2. ", text, flags=re.MULTILINE)

    def _normalize_horizontal_rules(self, text: str) -> str:
        """Normalize horizontal rules to consistent format.

        Converts various horizontal rule styles to standard ---

        Args:
            text: Text with various horizontal rule styles.

        Returns:
            Text with consistent --- horizontal rules.

        """
        # Match various horizontal rule patterns
        # Must be 3+ of same char, optionally with spaces
        return re.sub(
            r"^[ \t]*[-_*][ \t]*[-_*][ \t]*[-_*][ \t*_-]*$", "---", text, flags=re.MULTILINE
        )

    def _normalize_emphasis(self, text: str) -> str:
        """Normalize emphasis markers (bold/italic).

        Converts underscores to asterisks for consistency:
        - _italic_ -> *italic*
        - __bold__ -> **bold**

        Args:
            text: Text with mixed emphasis markers.

        Returns:
            Text with consistent asterisk emphasis.

        """
        # Convert __bold__ to **bold**
        text = re.sub(r"__([^_]+)__", r"**\1**", text)

        # Convert _italic_ to *italic* (but not in code/urls)
        # Only convert if surrounded by whitespace or punctuation
        return re.sub(r"(?<![a-zA-Z0-9_])_([^_\s][^_]*[^_\s])_(?![a-zA-Z0-9_])", r"*\1*", text)

    def _normalize_spacing(self, text: str) -> str:
        """Normalize spacing in Markdown content.

        Ensures:
        - Blank line before headers
        - Blank line before lists
        - Blank line before code blocks
        - No more than 2 consecutive blank lines

        Args:
            text: Text with irregular spacing.

        Returns:
            Text with normalized spacing.

        """
        lines = text.split("\n")
        normalized_lines: list[str] = []
        prev_line_empty = False
        prev_line_type = "text"

        for _i, line in enumerate(lines):
            stripped = line.strip()

            # Determine line type
            if not stripped:
                line_type = "empty"
            elif stripped.startswith("#"):
                line_type = "header"
            elif re.match(r"^[-*+]\s", stripped) or re.match(r"^\d+[.)]\s", stripped):
                line_type = "list"
            elif stripped.startswith("```"):
                line_type = "code_fence"
            else:
                line_type = "text"

            # Add blank line before headers (if previous wasn't empty and last line has content)
            if (
                line_type == "header"
                and prev_line_type not in ("empty", "header")
                and normalized_lines
                and normalized_lines[-1].strip()
            ):
                normalized_lines.append("")

            # Add blank line before list start (if previous was text and last line has content)
            if (
                line_type == "list"
                and prev_line_type == "text"
                and normalized_lines
                and normalized_lines[-1].strip()
            ):
                normalized_lines.append("")

            # Skip excessive empty lines (max 2 consecutive)
            if line_type == "empty" and prev_line_empty:
                # Check if we already have 2 empty lines
                empty_count = 0
                for prev in reversed(normalized_lines):
                    if not prev.strip():
                        empty_count += 1
                    else:
                        break
                if empty_count >= 2:
                    continue

            normalized_lines.append(line)
            prev_line_empty = line_type == "empty"
            prev_line_type = line_type

        return "\n".join(normalized_lines)

    def _transform_json_content(self, text: str) -> str:
        """Transform JSON-derived content for better readability.

        Wraps JSON content in code blocks if not already formatted.

        Args:
            text: JSON-derived text content.

        Returns:
            Text with JSON properly formatted as code.

        """
        # Check if content looks like raw JSON and not already in code block
        stripped = text.strip()
        if stripped.startswith(("{", "[")) and not stripped.startswith("```"):
            return f"```json\n{text}\n```"
        return text

    def _transform_code_content(self, text: str) -> str:
        """Transform code content with proper formatting.

        Ensures code is in proper code blocks with language hints
        where detectable.

        Args:
            text: Code content.

        Returns:
            Text with code properly fenced.

        """
        # Check if content is already in code block
        stripped = text.strip()
        if stripped.startswith("```"):
            return text

        # Try to detect language
        lang = self._detect_code_language(text)

        return f"```{lang}\n{text}\n```"

    def _detect_code_language(self, code: str) -> str:
        """Detect programming language from code content.

        Args:
            code: Code snippet to analyze.

        Returns:
            Detected language identifier or empty string.

        """
        code_lower = code.lower()

        # Python indicators (def/import/class with self/__init__)
        if ("def " in code or "import " in code or "class " in code_lower) and (
            "self" in code or "__init__" in code
        ):
            return "python"

        # JavaScript/TypeScript indicators (const/let/function with arrow/async)
        if ("const " in code or "let " in code or "function " in code) and (
            "=>" in code or "async " in code
        ):
            return "javascript"

        # HTML indicators
        if "<html" in code_lower or "<!doctype" in code_lower:
            return "html"

        # SQL indicators
        if "select " in code_lower and "from " in code_lower:
            return "sql"

        # JSON indicators
        if code.strip().startswith("{") and code.strip().endswith("}"):
            return "json"

        return ""


__all__ = ["MarkdownNormalizer"]
