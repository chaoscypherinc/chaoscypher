# Changelog

All notable changes to the ChaosCypher package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2024-11-21

### Added
- Initial release of ChaosCypher core library
- Hexagonal architecture with ports and adapters pattern
- Storage adapters: SQLite (production), File (CLI/portable)
- LLM adapters: Ollama, OpenAI, Anthropic, Google Gemini
- Web adapters: HTTP fetcher with auto-retry
- Core services:
  - Workflow orchestration and execution
  - Entity extraction from documents
  - Relationship discovery and mapping
  - Template-based schema management
  - Conversation management for chat interfaces
  - Discovery sessions for knowledge exploration
- Repository layer for domain object access
- Helper utilities for embedding, template analysis, error handling
- Comprehensive type hints (PEP 561 compatible via py.typed)
- Framework-agnostic design (works with FastAPI, CLI, Jupyter)

### Changed
- Package renamed from `chaoscypher` to `chaoscypher` for standalone distribution
- Prepared for PyPI publication as independent library

[0.1.0]: https://github.com/yourusername/chaoscypher/releases/tag/v0.1.0
