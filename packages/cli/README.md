# ChaosCypher CLI

Command-line tools for ChaosCypher knowledge graph library.

## Installation

### From PyPI (recommended)

```bash
pipx install chaoscypher-cli
```

> **Note:** The CLI requires Python 3.14+, so pipx must run on a 3.14 interpreter (`pipx install --python python3.14 chaoscypher-cli` if 3.14 isn't your default).

[pipx](https://pipx.pypa.io/) installs the CLI in an isolated environment and automatically adds it to your PATH. If you don't have pipx, install it first:

```bash
# macOS
brew install pipx

# Linux (Debian/Ubuntu)
sudo apt install pipx

# Windows
scoop install pipx
```

### From source

```bash
git clone https://github.com/chaoscypherinc/chaoscypher.git
cd chaoscypher
# Install uv first: https://docs.astral.sh/uv/getting-started/installation/
uv sync --package chaoscypher-cli
```

> **Note:** On Windows, the `chaoscypher` command lands in `.venv\Scripts\` after `uv sync`. Run it via `uv run chaoscypher` from the repo root, or activate the venv (`.venv\Scripts\activate`) for a bare `chaoscypher` invocation.

### Development install

```bash
git clone https://github.com/chaoscypherinc/chaoscypher.git
cd chaoscypher
uv sync --all-packages --extra dev   # full workspace + dev tools
```

## Usage

```bash
# Show help
chaoscypher --help

# Create a new database
chaoscypher db create my-graph

# Add source documents
chaoscypher source add documents/

# Add with explicit upload-time options (API parity with POST /sources)
chaoscypher source add paper.pdf \
    --vision \
    --content-filtering \
    --normalize \
    --filtering-mode strict \
    --skip-duplicates

# Export knowledge graph
chaoscypher graph package export --output my-knowledge.ccx

# Import knowledge graph
chaoscypher graph package load my-knowledge.ccx

# Search the graph
chaoscypher source search "artificial intelligence"
```

### Upload-time flags (API parity)

`source add` exposes the same upload-time choices the API does. Each flag persists on the source row and is preserved across recovery / retry / re-extract.

| Flag | Default | API equivalent |
|---|---|---|
| `--vision/--no-vision` | `--vision` | `enable_vision` |
| `--content-filtering/--no-content-filtering` | `--content-filtering` | `content_filtering` |
| `--normalize/--no-normalize` | (file-type default — on for prose, off for CSV/JSON/XML) | `enable_normalization` |
| `--filtering-mode {maximum,strict,balanced,lenient,minimal,unfiltered}` | unset (resolves to `balanced`) | `filtering_mode` |
| `--skip-duplicates` | off | `skip_duplicates` |

Run `chaoscypher source add --help` for the full flag list.

## Features

- **Graph Management**: Create, delete, and manage knowledge graphs
- **Data Import**: Import documents (PDF, DOCX, TXT, CSV, JSON, audio, archives)
- **Data Export**: Export graphs as `.ccx` packages (CCX — Chaos Cypher eXchange)
- **Search**: Full-text and vector search across the knowledge graph
- **Chat**: Interactive AI chat with graph-grounded RAG
- **Quality**: Run extraction quality scoring and reports
- **Benchmark**: Run reproducible extraction benchmarks across models

## Development

From the repo root:

```bash
# Run tests
uv run pytest packages/cli/tests --import-mode=importlib

# Format code (ruff replaces black; ruff format is the formatter)
uv run ruff format packages/cli
uv run ruff check packages/cli

# Type checking
uv run mypy packages/cli/src
```

## Architecture

The CLI is a thin wrapper around the `chaoscypher` core library, providing:
- User-friendly command-line interface using Click
- Rich terminal output with progress bars and formatting
- Configuration management via the unified `settings.yaml` and `CHAOSCYPHER_*` environment variables (managed with `chaoscypher config show/get/set/edit`)
- Batch operations and scripting support

## Requirements

- Python 3.14+
- chaoscypher-core>=0.1.0

## License

AGPL-3.0 License - see LICENSE file for details.

## Links

- [ChaosCypher monorepo](https://github.com/chaoscypherinc/chaoscypher)
- [Documentation](https://chaoscypher.com)
