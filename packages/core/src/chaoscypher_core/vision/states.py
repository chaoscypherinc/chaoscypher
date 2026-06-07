# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Vision pipeline state enums.

Per CC038: new status fields use StrEnum + String column, no
CheckConstraint. These enums are validated Python-side at the
service layer before any write.
"""

from enum import StrEnum


class VisionPageKind(StrEnum):
    """What kind of image a vision_page_descriptions row represents."""

    PDF_PAGE = "pdf_page"
    STANDALONE_IMAGE = "standalone_image"


class VisionPageStatus(StrEnum):
    """Lifecycle state of one vision_page_descriptions row.

    PENDING    — row created during loader-phase enqueue; LLM call has
                 not completed.
    SUCCEEDED  — LLM returned content cleanly (finish_reason='stop').
    FAILED     — LLM returned None, raised, or the per-page render
                 failed. ``error_message`` carries the cause.
    TRUNCATED  — LLM returned content but hit max_tokens
                 (finish_reason='length'). ``description`` is the
                 partial result; counted toward job ``completed``
                 (we got content). Surfaced via the
                 VISION_PAGES_TRUNCATED quality counter.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TRUNCATED = "truncated"
