---
id: building-loaders
title: Building Document Loaders
description: Build a custom document loader plugin for Chaos Cypher by implementing the BaseLoader protocol — adds support for new file formats with zero registration required.
---

# Building Document Loaders

Document loaders are plugins that teach Chaos Cypher how to read new file formats. Each loader converts a specific file type into a list of text chunks with metadata, which are then indexed for RAG search and optionally processed for entity extraction.

## The Loader Interface

Every loader must satisfy the `BaseLoader` protocol defined in `packages/core/src/chaoscypher_core/services/sources/loaders/base.py`. The protocol uses structural typing (duck typing), so you do not need to inherit from any base class -- just implement the required properties and methods.

### Required Interface

```python
from typing import Any

class MyLoader:
    """A custom document loader."""

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader handles (e.g., ['.xlsx', '.xls'])."""
        ...

    def __init__(self, settings: Any = None) -> None:
        """Accept optional settings (the engine passes settings during discovery)."""
        ...

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load the file and return document chunks."""
        ...

    def supports_ocr(self) -> bool:
        """Return True if this loader supports OCR for scanned content."""
        ...
```

| Member | Type | Description |
|--------|------|-------------|
| `supported_extensions` | `property` | List of file extensions including the dot, e.g. `[".xlsx", ".XLS"]`. Both case variants should be listed. |
| `__init__(settings)` | method | Constructor. The `LoaderRegistry` passes the engine `settings` object; store it if needed or ignore it. |
| `load_document(filepath)` | method | Load the file at `filepath` and return a list of dicts. Each dict must have a `"content"` key (str) and a `"metadata"` key (dict). |
| `supports_ocr()` | method | Return `True` if the loader can handle scanned/image-based documents. Most loaders return `False`. |

:::tip[Plugin metadata]

Loaders can optionally define a `metadata` property returning a `PluginMetadata` object with `id`, `name`, `version`, and `description`. This is recommended for new loaders as it enables better identification in the plugin registry, but is not required for backwards compatibility.

:::

### Conventions for new loaders

The shipped May 2026 loaders (HTML, RST, DOCX, XLSX, PPTX, EPUB)
follow three conventions worth borrowing for any new loader:

- **Filename ends with `_loader.py`.** This is required for
  auto-discovery — the registry only scans files matching `*_loader.py`.

- **Wrap library errors in `ValidationError`.** The indexing handler
  catches `ValidationError` and writes a clean `error_message` on the
  source row. Letting a third-party library trace propagate verbatim
  produces opaque errors users can't act on.

  ```python
  from chaoscypher_core.exceptions import ValidationError

  try:
      workbook = load_workbook(filepath, read_only=True, data_only=True)
  except Exception as exc:
      raise ValidationError(f"Could not open Excel file: {exc}") from exc
  ```

- **For text-shaped formats, route through `detect_encoding()` and
  call `set_loader_encoding()`.** The shared encoding helper produces
  strict UTF-8 / cp1252 / Latin-1 fallbacks (no `errors="replace"`
  silent corruption); the counter helper records which encoding the
  loader actually used so the user can see it on the [Data Quality tab](../user-guide/data-quality.md).

  ```python
  from chaoscypher_core.services.quality.counters import set_loader_encoding
  from chaoscypher_core.utils.encoding import detect_encoding

  encoding_used, text = detect_encoding(Path(filepath))
  # ... pair with set_loader_encoding(...) when an adapter is reachable
  ```

  Text, CSV, JSON, HTML, RST, and EPUB all use this pattern. PDF,
  Office, and binary formats can skip it because their underlying
  libraries handle bytes themselves.

- **Raise specific errors when content is empty post-extraction.** A
  PDF that produces zero text (scanned image, OCR-only) is a different
  failure from a corrupt file. The PDF loader raises
  `"scanned PDF — enable vision to extract content"` so the user has
  a hint to act on. Do the same for new loaders when the format
  permits empty-but-valid files.

### Return Format

`load_document` must return a list of dictionaries with this shape:

```python
[
    {
        "content": "The extracted text content...",
        "metadata": {
            "source": "/path/to/file.xlsx",
            # Any additional metadata you want to preserve
        }
    }
]
```

:::note[Chunking is handled downstream]

You do **not** need to split the text into small chunks yourself. Loaders return raw documents; the `ChunkingService` handles chunking downstream after normalization. Your loader should return the full document content (one entry per logical section, or one entry for the whole file).

:::

## Step-by-Step Example: Building an Excel Loader

This example builds a complete loader for `.xlsx` and `.xls` files using the `openpyxl` library.

:::note[Mirrors the built-in `xlsx_loader.py`]

`.xlsx` is now handled by a built-in loader. The example below mirrors
the shipped `packages/core/src/chaoscypher_core/services/sources/loaders/xlsx_loader.py`
so you can read the example end-to-end, but you don't need to ship it
yourself unless you're customizing behaviour. If you want to override
the built-in for your install, drop your version in `data/plugins/loaders/`
and the registry will use yours over the built-in.

:::

### 1. Create the loader file

Create a file named `excel_loader.py`. The filename must end with `_loader.py` for auto-discovery to find it.

```python
"""Excel Document Loader.

Loads Excel spreadsheets (.xlsx, .xls) using openpyxl.
Implements BaseLoader protocol for auto-discovery.
"""

from pathlib import Path
from typing import Any

import structlog


logger = structlog.get_logger(__name__)


class ExcelLoader:
    """Excel spreadsheet loader.

    Converts each worksheet into a text representation
    where rows are separated by newlines and columns by tabs.
    """

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [".xlsx", ".XLSX", ".xls", ".XLS"]

    def __init__(self, settings: Any = None) -> None:
        """Initialize Excel loader.

        Args:
            settings: Engine settings (not currently used).
        """
        self.settings = settings

    def load_document(
        self, filepath: str
    ) -> list[dict[str, Any]]:
        """Load Excel file and convert sheets to text.

        Args:
            filepath: Path to the Excel file.

        Returns:
            List of document chunks, one per worksheet.
        """
        try:
            from openpyxl import load_workbook
        except ImportError:
            logger.error("openpyxl_not_installed")
            raise ImportError(
                "openpyxl is required for Excel loading. "
                "Install it with: pip install openpyxl"
            )

        logger.info("excel_loading_started", filepath=filepath)

        workbook = load_workbook(filepath, read_only=True, data_only=True)
        documents: list[dict[str, Any]] = []

        for sheet_name in workbook.sheetnames:
            sheet = workbook[sheet_name]
            rows: list[str] = []

            for row in sheet.iter_rows(values_only=True):
                cell_values = [str(cell) if cell is not None else "" for cell in row]
                rows.append("\t".join(cell_values))

            content = "\n".join(rows)

            if content.strip():
                documents.append({
                    "content": content,
                    "metadata": {
                        "source": filepath,
                        "sheet_name": sheet_name,
                        "row_count": len(rows),
                    },
                })

        workbook.close()

        logger.info(
            "excel_loaded",
            sheet_count=len(documents),
            filepath=filepath,
        )

        return documents

    def supports_ocr(self) -> bool:
        """Excel files do not need OCR."""
        return False
```

### 2. Place the file

You have two options for where to put your loader:

| Location | Scope | Survives updates |
|----------|-------|------------------|
| `data/plugins/loaders/excel_loader.py` | User plugin directory | Yes |
| `packages/core/src/chaoscypher_core/services/sources/loaders/excel_loader.py` | Built-in | No (overwritten on upgrade) |

For custom loaders, always use the **user plugin directory**: `data/plugins/loaders/`.

### 3. Restart the application

The `LoaderRegistry` discovers loaders at startup. Restart the Cortex service (or the full Docker stack) to pick up your new loader.

```bash
make docker-dev   # Restart services
```

Your loader will appear in the logs:

```
loader_registered  loader_class=ExcelLoader  extensions=['.xlsx', '.XLSX', '.xls', '.XLS']  path_type=user
```

## Auto-Discovery Mechanism

The `LoaderRegistry` (defined in `packages/core/src/chaoscypher_core/services/sources/loaders/registry.py`) discovers loaders through this process:

1. **Scan built-in directory** -- `packages/core/src/chaoscypher_core/services/sources/loaders/` for files matching `*_loader.py`.
2. **Scan user plugin directory** -- `data/plugins/loaders/` for files matching `*_loader.py`.
3. **Import each file** -- Built-in loaders use standard Python imports; user loaders use `importlib.util.spec_from_file_location`.
4. **Inspect classes** -- For each class in the module, check if it has a `supported_extensions` attribute (duck typing).
5. **Instantiate** -- Create an instance passing `settings` to the constructor.
6. **Register by extension** -- Each extension from `supported_extensions` is registered as a lookup key.

:::warning[User plugins override built-in plugins]

If a user plugin registers the same file extension as a built-in loader, the user plugin takes precedence. This lets you replace the default PDF loader with your own implementation, for example.

:::

### File Naming Rules

- The file **must** end with `_loader.py` (e.g., `excel_loader.py`, `docx_loader.py`).
- Files named `__init__.py`, `base.py`, `registry.py`, and `factory.py` are excluded from discovery.
- The class name can be anything (e.g., `ExcelLoader`, `MyCustomLoader`).

## Testing Your Loader

### Manual Testing

```python
from my_loader import ExcelLoader

loader = ExcelLoader()

# Verify extensions
assert ".xlsx" in loader.supported_extensions

# Test loading
chunks = loader.load_document("/path/to/test.xlsx")
assert len(chunks) > 0
assert "content" in chunks[0]
assert "metadata" in chunks[0]
assert isinstance(chunks[0]["content"], str)
assert len(chunks[0]["content"]) > 0
```

### Unit Test Template

```python
"""Tests for ExcelLoader."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestExcelLoader:
    """Tests for ExcelLoader plugin."""

    def test_supported_extensions(self):
        """Verify declared extensions."""
        from excel_loader import ExcelLoader

        loader = ExcelLoader()
        extensions = loader.supported_extensions
        assert ".xlsx" in extensions
        assert ".xls" in extensions

    def test_load_document_returns_correct_format(self, tmp_path):
        """Verify output format matches BaseLoader contract."""
        from excel_loader import ExcelLoader

        # Create a test file (use openpyxl to write a small workbook)
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Name", "Value"])
        ws.append(["test", "123"])
        test_file = tmp_path / "test.xlsx"
        wb.save(test_file)

        loader = ExcelLoader()
        result = loader.load_document(str(test_file))

        assert isinstance(result, list)
        assert len(result) > 0

        chunk = result[0]
        assert "content" in chunk
        assert "metadata" in chunk
        assert isinstance(chunk["content"], str)
        assert "test" in chunk["content"]

    def test_supports_ocr_returns_false(self):
        """Excel files do not support OCR."""
        from excel_loader import ExcelLoader

        loader = ExcelLoader()
        assert loader.supports_ocr() is False

    def test_accepts_settings_parameter(self):
        """Constructor must accept optional settings."""
        from excel_loader import ExcelLoader

        settings = MagicMock()
        loader = ExcelLoader(settings=settings)
        assert loader.settings is settings
```

## Best Practices

- **Return full content, not small chunks.** Loaders return raw documents; `ChunkingService` handles chunking downstream. Your loader should return one entry per logical section (e.g., one per worksheet, one per chapter) or one entry for the entire document.

- **Include meaningful metadata.** At minimum include `"source": filepath`. Add format-specific metadata like page counts, sheet names, or author information when available.

- **Handle missing dependencies gracefully.** Import optional libraries inside `load_document` and raise a clear error message if they are missing.

- **Use structlog for logging.** Follow the project convention: `logger.info("event_name", key=value)` with no f-strings.

- **Accept `settings` in `__init__`.** Even if you do not use settings today, accept the parameter to be compatible with the registry's instantiation pattern.

- **List both case variants.** Include both `.pdf` and `.PDF` in `supported_extensions` to handle files with uppercase extensions.

- **Handle empty files.** Return an empty list `[]` rather than raising an exception when a file has no extractable content.

- **Test with real files.** Automated tests with synthetic files are useful, but always verify your loader against real-world files of the target format before deploying.

## See also

- [Architecture: Plugin System](../architecture/plugins.md) — registry pattern, plugin types, and auto-discovery mechanism overview
- [User guide: Document Loaders](../user-guide/loaders.md) — built-in loaders reference and optional dependency setup
