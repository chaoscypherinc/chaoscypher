---
title: "ADR-0003: Replace PyMuPDF with pypdf"
description: Decision record replacing AGPL-3.0 PyMuPDF with permissively-licensed pypdf for PDF text extraction in Chaos Cypher.
---

# 0003. Replace PyMuPDF with pypdf

Date: 2026-02-06

## Status

Accepted

## Context

Chaos Cypher uses PyMuPDF4LLM (AGPL-3.0) for PDF text extraction. As documented in ADR-0002, AGPL-3.0 is incompatible with our license policy requiring permissive licenses only.

The `PyMuPDFLoader` class in `packages/core/src/chaoscypher_core/services/sources/loaders/pdf_pymupdf.py` uses `pymupdf4llm.to_markdown()` with `IdentifyHeaders` for font-based header detection, producing structured Markdown output from PDFs.

## Decision

Replace PyMuPDF4LLM with **pypdf** (BSD-3) for PDF text extraction.

### Changes

1. **Delete** `pdf_pymupdf.py` (PyMuPDF-based loader)
2. **Create** `pdf_loader.py` (pypdf-based loader) following `*_loader.py` naming convention for auto-discovery
3. **Replace** `pymupdf4llm` dependency with `pypdf` in `pyproject.toml`
4. **Update** all import references from `PyMuPDFLoader` to `PdfLoader`

### Technical Differences

| Aspect | PyMuPDF4LLM (old) | pypdf (new) |
|--------|-------------------|-------------|
| License | AGPL-3.0 | BSD-3 |
| Output format | Structured Markdown | Plain text |
| Headers | Font-size analysis (IdentifyHeaders) | Not preserved |
| Tables | Markdown tables | Not preserved |
| Speed | ~0.12s/page | Comparable |
| Dependencies | PyMuPDF (C library) | Pure Python |
| Metadata | extraction_method: "pymupdf4llm" | extraction_method: "pypdf" |

## Rationale

- pypdf is the most widely-used permissively-licensed PDF library in Python
- Pure Python with no C dependencies simplifies builds and deployment
- Adequate for text extraction feeding into LLM-based entity extraction pipeline
- Structure preservation is less critical since downstream processing (chunking, entity extraction) works on raw text

## Consequences

### Positive

- **License compliance** - BSD-3 is fully permissive
- **Simpler builds** - No C compilation required (pure Python)
- **Auto-discovery** - New `pdf_loader.py` name follows `*_loader.py` convention, removing need for special-case registration in the registry

### Negative

- **No markdown structure** - Headers, tables, and formatting not preserved in extraction output
- **Lower structure_score** - Normalizer quality metrics will report lower structure preservation

### Neutral

- **Same output contract** - Returns `list[dict[str, Any]]` with `content` and `metadata` keys
- **Page separator** - Pages are joined with a blank line (`\n\n`). (Updated: an earlier `\n\n-----\n\n` separator was later simplified.)
- **Entity extraction unaffected** - LLM-based extraction works on raw text content

## References

- [pypdf documentation](https://pypdf.readthedocs.io/)
- [pypdf GitHub](https://github.com/py-pdf/pypdf)
- ADR-0002: Dependency License Policy
