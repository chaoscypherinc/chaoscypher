# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Vision LLM prompt templates.

Domain-agnostic prompts for describing visual content in documents and images.

Three prompts cover the three things the vision pass is asked to do:

* :data:`PDF_PAGE_PROMPT` — page has a machine-readable text layer
  that ``pdf_loader.py`` already extracted via ``page.extract_text()``.
  Vision augments with non-text content (charts, diagrams, photos)
  and *does not* re-transcribe body text. Used when the page's
  extracted text is non-empty.
* :data:`PDF_PAGE_SCANNED_PROMPT` — page is image-only (scanned PDF,
  photographed document, etc.) and ``extract_text()`` returned an
  empty/near-empty string. Vision is the only path to the text, so
  the prompt asks for a *verbatim* transcription preserving reading
  order, plus a brief description of non-text content. Selected by
  the caller when the existing page text is below
  :data:`SCANNED_PAGE_TEXT_THRESHOLD_CHARS`.
* :data:`STANDALONE_IMAGE_PROMPT` — the source itself is an image
  (``ImageLoader`` produced a single document with
  ``extraction_method="vision_pending"``). Same intent as the scanned
  prompt — transcribe everything plus describe.

The single-vs-scanned PDF split exists because pre-2026-05-11 the only
PDF prompt explicitly said "Do not transcribe body text", so scanned
PDFs lost all their text: ``extract_text()`` produced "", and the
vision pass was forbidden from filling the gap. See
``indexing_handler._apply_vision_processing`` for the selection logic.
"""

PDF_PAGE_PROMPT = (
    "Describe the visual elements on this page — charts, diagrams, images, "
    "tables, figures, and any other non-text content. Include data values, "
    "labels, legends, and spatial relationships where visible. "
    "Do not transcribe body text that is part of the page's normal text "
    "flow; that text has already been extracted from the page's text layer."
)

PDF_PAGE_SCANNED_PROMPT = (
    "This page has no machine-readable text layer — vision is the only "
    "way to capture its content. Transcribe ALL text visible on the page "
    "verbatim, preserving reading order: headings, paragraphs, captions, "
    "labels, table cells, form fields, page numbers, dates, prices, "
    "addresses, lists, and any text inside images or graphics. Do not "
    "summarise or paraphrase the text. After the transcription, briefly "
    "describe non-text visual content (photos, diagrams, layout) so it "
    "can be searched."
)

STANDALONE_IMAGE_PROMPT = (
    "Describe this image in detail including any text, labels, data, "
    "diagrams, visual information, and spatial relationships present. "
    "Transcribe any text visible in the image verbatim."
)

# Threshold (in non-whitespace characters) below which a PDF page's
# extracted text is treated as "effectively empty" and the scanned-page
# prompt is selected instead of the standard one. Set conservatively:
# real content pages that happen to have <50 non-whitespace characters
# (a page that contains only a chapter heading, say) get the
# transcription prompt and their heading gets duplicated into the
# vision output — harmless for search/embedding. Tightening this
# threshold risks missing real scanned pages that happen to have a
# couple of OCR-extracted glyphs.
SCANNED_PAGE_TEXT_THRESHOLD_CHARS = 50

__all__ = [
    "PDF_PAGE_PROMPT",
    "PDF_PAGE_SCANNED_PROMPT",
    "SCANNED_PAGE_TEXT_THRESHOLD_CHARS",
    "STANDALONE_IMAGE_PROMPT",
]
