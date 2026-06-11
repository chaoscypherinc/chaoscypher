---
id: loaders
title: Document Loaders
description: Chaos Cypher supports PDF, audio, video, images, CSV, JSON, and text files out of the box — add custom formats by dropping a Python loader plugin into the plugins directory.
---

# Document Loaders

Document loaders parse different file formats into text for indexing and extraction. Custom loaders are Python files -- drop one into the plugins directory and it's automatically discovered.

## Built-In Loaders

| Loader | Extensions | Dependencies | Notes |
|--------|-----------|-------------|-------|
| **Text** | `.txt`, `.md`, `.log` | None | Uses the shared `detect_encoding` helper (UTF-8 / cp1252 / Latin-1) so legacy non-UTF-8 files keep their characters |
| **PDF** | `.pdf` | pypdf | Page-by-page extraction, metadata (title, author, page count); raises a scanned-PDF specific error when zero text is recovered |
| **CSV** | `.csv` | csv | Native dialect sniffer detects commas, semicolons, tabs, and quoting style; one document per row |
| **JSON** | `.json`, `.jsonl`, `.ndjson` | None | `.json` parsed as a single document; `.jsonl` / `.ndjson` parsed line-by-line with per-line error isolation (one bad row no longer fails the whole file) |
| **HTML** | `.html`, `.htm`, `.xhtml` | beautifulsoup4 | Strips chrome (`script`, `style`, `nav`, `aside`, `footer`, `header`, `noscript`); captures `<title>` in metadata |
| **RST** | `.rst`, `.rest` | docutils | reStructuredText with directive handling |
| **DOCX** | `.docx` | python-docx | Headings, paragraphs, list items, and tables flattened to text |
| **XLSX** | `.xlsx`, `.xlsm` | openpyxl | One document per worksheet; rows joined with tabs |
| **PPTX** | `.pptx` | python-pptx | One document per slide with shape text concatenated |
| **EPUB** | `.epub` | (none — hand-rolled) | Reads the EPUB ZIP container directly and parses each XHTML chapter; no AGPL dependency |
| **Image** | `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.tiff`, `.tif`, `.bmp` | Pillow | Extracts dimensions/format; vision LLM handles description |
| **Audio** | `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`, `.wma`, `.aac` | faster-whisper, ffmpeg | Transcription via Whisper (CPU, no GPU required) |
| **Video** | `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`, `.wmv`, `.flv` | faster-whisper, ffmpeg | Extracts audio track, then transcribes |
| **Archive** | `.zip`, `.tar.gz`, `.tgz` | Built-in | Auto-detects format and dispatches to handler |

:::info[Encoding detection]

Text-shaped loaders (Text, JSON, CSV, HTML, RST) all share one
`detect_encoding()` helper. It tries UTF-8 strict, then cp1252 strict
(the most common Windows export), then `charset-normalizer` for unusual
files, and finally Latin-1 — which always succeeds. The encoding it
actually used is recorded on the source's `loader_encoding_used` quality
counter so you can see whether a file decoded cleanly or fell back.

:::

## Archive Handlers

Archives are extracted and processed by specialized handlers that auto-detect the documentation format:

| Handler | Detection | Confidence | What It Processes |
|---------|-----------|------------|-------------------|
| **Sphinx HTML** | `_static/`, `genindex.html`, Sphinx CSS | High | HTML content with article body extraction |
| **Markdown** | 10+ `.md` files, `mkdocs.yml`, `docusaurus.config.js` | Medium | Markdown files with frontmatter stripping |
| **OpenAPI** | `openapi.json/yaml`, `swagger.json/yaml` | High | Per-operation chunking with schema docs |
| **Generic** | Always matches | Low (fallback) | Routes each file through the appropriate loader |

The handler with the highest detection confidence is used. The generic handler is the fallback for archives without a recognized documentation structure.

## How Loading Works

1. **File type detection** -- the loader registry matches the file extension to a loader
2. **Text extraction** -- the loader reads the file and returns raw text with metadata
3. **Chunking** -- `ChunkingService` splits text into chunks after normalization
4. **Indexing** -- chunks are stored and embedded for RAG search

Loaders return raw documents; chunking is handled by `ChunkingService` downstream, not by the registry. Chunk size and overlap are configured via `settings.yaml`:

```yaml
chunking:
  small_chunk_size: 900       # Characters per chunk
  small_chunk_overlap: 150    # Overlap between chunks
```

## Optional Dependencies

Some loaders require additional system packages:

**Audio and video transcription:**

```bash
# Install ffmpeg (required for audio/video format conversion)
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Windows
winget install ffmpeg
```

The Whisper model downloads automatically on first use and is cached locally. Transcription runs on CPU -- no GPU is required.

**Image metadata extraction** works with Pillow (included). For image content understanding, the vision LLM service handles description separately from the loader.

## Custom Loaders

Custom loaders are Python files placed in `data/plugins/loaders/`. Files must end with `_loader.py` for auto-discovery. User plugins override built-in loaders for the same file extensions.

```
data/
  plugins/
    loaders/
      excel_loader.py
```

**Minimal loader:**

```python
class ExcelLoader:
    @property
    def supported_extensions(self) -> list[str]:
        return [".xlsx", ".xls"]

    def __init__(self, settings=None):
        self.settings = settings

    def load_document(self, filepath: str) -> list[dict]:
        # Read file and return documents
        import openpyxl
        wb = openpyxl.load_workbook(filepath)
        text = "\n".join(
            str(cell.value) for sheet in wb for row in sheet.iter_rows() for cell in row if cell.value
        )
        return [{"content": text, "metadata": {"source": filepath}}]
```

For the full loader interface and advanced patterns, see the [Building Document Loaders](../developer-guide/building-loaders.md) guide.

## Programmatic Usage

Load text from any supported file format:

```python
from chaoscypher_core import ChaosCypher

text = ChaosCypher.load("document.pdf")
```

## See also

- [Architecture: Plugin System](../architecture/plugins.md) — how the registry auto-discovers loaders, tool plugins, domains, and LLM providers
- [Developer guide: Building Document Loaders](../developer-guide/building-loaders.md) — full `BaseLoader` protocol, step-by-step example, and testing patterns
