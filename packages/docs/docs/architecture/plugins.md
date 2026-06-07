---
id: plugins
title: Plugin System
description: How Chaos Cypher's plugin system auto-discovers loaders, tool plugins, domain configs, LLM providers, cleaners, and presets from designated directories.
---

# Plugin System

Chaos Cypher uses a unified plugin architecture for extensibility. All plugin registries extend `BaseRegistry[T]` with auto-discovery from designated directories.

## Plugin Types

| Type | Location | Pattern | Count | Purpose |
|------|----------|---------|-------|---------|
| **Loaders** | `services/sources/loaders/` | `*_loader.py` | 14 | Parse different file formats |
| **Tool Plugins** | `services/workflows/tools/plugins/` | `*_plugin.py` | 10 | Workflow step implementations |
| **Domain Plugins** | `services/sources/engine/extraction/domains/plugins/` | `*.jsonld` | 19 | Extraction domain configurations |
| **LLM Providers** | `adapters/llm/providers/` | `*_provider.py` | 4 | LLM backend implementations |
| **Cleaners** | `services/sources/normalizer/cleaners/` | `*_cleaner.py` | — | Content normalization rules |
| **Archive Handlers** | `services/sources/loaders/archive/handlers/` | `*_handler.py` | — | Archive format detection |
| **Presets** | `services/presets/plugins/` | `*.json` | 7 | Ollama VRAM configuration presets |

## Loaders

Document loaders parse different file formats into text for indexing and extraction.

| Loader | Formats |
|--------|---------|
| PDF | `.pdf` |
| Text | `.txt`, `.md`, `.log` |
| CSV | `.csv` |
| JSON | `.json`, `.jsonl` |
| Image | `.jpg`, `.png`, `.gif`, `.webp`, `.tiff`, `.bmp` (OCR) |
| Audio | `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg` (transcription) |
| Video | `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm` (audio extraction + transcription) |
| Archive | `.zip`, `.tar.gz` (auto-detect format: Sphinx, Markdown, OpenAPI, mixed) |
| DOCX | `.docx` |
| XLSX | `.xlsx`, `.xlsm` |
| PPTX | `.pptx` |
| EPUB | `.epub` |
| HTML | `.html`, `.htm`, `.xhtml` |
| RST | `.rst` |

### User Plugins

Place custom loaders in `data/plugins/loaders/` to add support for new file formats. User plugins override built-in plugins with the same ID.

## Tool Plugins

Workflow step implementations for the automation system.

| Plugin | Description |
|--------|-------------|
| AI Extract JSON | Extract structured data using LLM |
| AI Generate Embedding | Generate vector embeddings |
| AI Prompt | Custom LLM prompt execution |
| AI Vector Search | Semantic similarity search |
| Data Extract | Extract data from structured sources |
| Data Merge | Merge multiple data sources |
| HTTP Request | Make HTTP requests |
| Logic Conditional | Branch workflow based on conditions |
| Logic Loop | Iterate over collections |
| Templates List | List available templates |

### User Plugins

Place custom tool plugins in `data/plugins/tools/`.

## Domain Plugins

Extraction domains configure how entities and relationships are extracted from different types of content. Each domain is a `.jsonld` file — no Python code required.

**Built-in domains:** biographical, cybersecurity, design, educational, financial, generic, historical, intelligence, investigation, legal, literary, medical, news, philosophical, political, reference, scientific, technical, theological.

Each domain defines: entity types, relationship types, detection rules, LLM guidance, quality scoring, extraction limits, and deduplication behavior.

### User Plugins

Place custom domain configs in `data/plugins/domains/`. User domains override built-in domains with the same name.

[Full domain schema reference and examples](../user-guide/domains.md)

## LLM Providers

| Provider | Features |
|----------|----------|
| **Ollama** | Local inference, multi-instance, load balancing |
| **OpenAI** | GPT models, cloud inference |
| **Anthropic** | Claude models |
| **Gemini** | Google AI models |

Embeddings are handled separately by a dedicated **embedding provider** (`LocalEmbeddingProvider` by default, running sentence-transformers on the CPU), not by LLM providers.

## Registry Pattern

All registries extend `BaseRegistry[T]`:

```python
class LoaderRegistry(BaseRegistry[BaseLoader]):
    """Auto-discovers and registers document loaders."""

    def discover(self) -> None:
        # Scans plugin directories for matching files
        # Loads and registers each plugin
        pass
```

**Key behaviors:**

- Auto-discovery scans designated directories for files matching the pattern
- User plugins (in `data/plugins/`) override built-in plugins with the same ID
- Python plugins implement a `metadata` property and core methods
- Config plugins (`.jsonld`) are loaded as data files
- Registration is idempotent — re-registering replaces the previous plugin

## VRAM Presets

Pre-configured Ollama model selections optimized for different GPU memory sizes:

| Preset | VRAM | Typical Models |
|--------|------|---------------|
| 16GB | 16 GB | Smaller quantized models |
| 20GB | 20 GB | Medium models |
| 24GB | 24 GB | Standard models |
| 32GB | 32 GB | Larger models |
| 48GB | 48 GB | Full-size models |
| 96GB | 96 GB | Multiple large models |
| 128GB | 128 GB | Maximum capability |

## See also

- [User guide: Document Loaders](../user-guide/loaders.md) — built-in loaders, archive handlers, and how to install a custom loader
- [User guide: Tool Plugins](../user-guide/tool-plugins.md) — built-in workflow tools and how to install a custom tool plugin
- [User guide: Extraction Domains](../user-guide/domains.md) — built-in domains and how to install a custom domain
- [Developer guide: Building Document Loaders](../developer-guide/building-loaders.md) — full loader protocol and step-by-step example
- [Developer guide: Building Tool Plugins](../developer-guide/building-tools.md) — full tool plugin interface and execution context
- [Developer guide: Building Extraction Domains](../developer-guide/building-domains.md) — JSON-LD domain schema reference and examples
