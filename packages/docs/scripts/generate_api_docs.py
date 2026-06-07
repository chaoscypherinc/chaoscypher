"""Generate Python API reference docs for Docusaurus using griffe.

Loads Python source via griffe (same engine mkdocstrings uses) and
renders markdown for each module/class. Run before docusaurus build.

Usage: python scripts/generate_api_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import griffe


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs" / "reference" / "python"

SEARCH_PATHS = [
    str(REPO_ROOT / "packages" / "core" / "src"),
    str(REPO_ROOT / "packages" / "cortex" / "src"),
    str(REPO_ROOT / "packages" / "neuron" / "src"),
    str(REPO_ROOT / "packages" / "cli" / "src"),
]

PAGES = {
    "models": {
        "title": "Models & DTOs",
        "description": "Core Pydantic models used across the ChaosCypher library. These are pure data transfer objects with no ORM or framework dependencies.",
        "modules": ["chaoscypher_core.models"],
    },
    "protocols": {
        "title": "Protocols",
        "description": "Port interfaces (Python Protocols) that define contracts for storage, graph, search, and other operations. These are the hexagonal architecture boundaries that adapters must implement.",
        "modules": [
            "chaoscypher_core.ports.chunk",
            "chaoscypher_core.ports.db",
            "chaoscypher_core.ports.embedding",
            "chaoscypher_core.ports.graph",
            "chaoscypher_core.ports.index",
            "chaoscypher_core.ports.search",
            "chaoscypher_core.ports.storage_chats",
            "chaoscypher_core.ports.storage_extraction_submissions",
            "chaoscypher_core.ports.storage_graph_snapshot",
            "chaoscypher_core.ports.storage_llm_metrics",
            "chaoscypher_core.ports.storage_sources",
            "chaoscypher_core.ports.storage_tools",
            "chaoscypher_core.ports.storage_triggers",
            "chaoscypher_core.ports.storage_workflow_executions",
            "chaoscypher_core.ports.storage_workflows",
        ],
    },
    "services": {
        "title": "Services API",
        "description": "Core business logic services that orchestrate operations across the knowledge graph platform.",
        "modules": [
            "chaoscypher_core.services.sources.engine.extraction.service",
            "chaoscypher_core.services.search.engine.index",
            "chaoscypher_core.services.search.engine.search",
            "chaoscypher_core.services.graph.management.node",
            "chaoscypher_core.services.graph.management.edge",
            "chaoscypher_core.services.graph.management.template",
            "chaoscypher_core.services.sources.engine.commit.service",
            "chaoscypher_core.utils.chunk",
            "chaoscypher_core.bootstrap",
        ],
    },
    "storage-adapters": {
        "title": "Storage Adapters API",
        "description": "Concrete storage implementations that fulfill the protocol contracts defined in the ports layer.",
        "modules": ["chaoscypher_core.adapters.sqlite.adapter"],
    },
}


RST_ROLE_RE = re.compile(r":(?:meth|func|class|attr|mod|data|exc|obj|ref):`~?([^`\n]+)`")
RST_LITERAL_RE = re.compile(r"``([^`\n]+?)``")


def escape_table_cell(text: str) -> str:
    """Escape pipes so code spans like `bytes | None` survive GFM table cells."""
    return text.replace("|", "\\|")


def _convert_rst_inline(text: str) -> str:
    """Convert RST roles / double-backtick literals to Markdown code spans.

    Skips fenced code blocks so code samples are left untouched.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_code_block = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            out.append(line)
            continue
        if in_code_block:
            out.append(line)
            continue
        converted = RST_ROLE_RE.sub(r"`\1`", line)
        converted = RST_LITERAL_RE.sub(r"`\1`", converted)
        out.append(converted)
    return "\n".join(out)


def _convert_literal_blocks(text: str) -> str:
    """Convert RST ``::`` literal blocks into fenced code blocks.

    A line ending in ``::`` followed by a more-indented block becomes the
    line with a single ``:`` plus a fenced block; a bare trailing ``::``
    with no block just collapses to ``:``.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()
        if not stripped.endswith("::") or stripped.endswith(":::"):
            out.append(line)
            i += 1
            continue
        # Locate the indented block after optional blank lines.
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        base_indent = len(line) - len(line.lstrip())
        block_indent = (len(lines[j]) - len(lines[j].lstrip())) if j < len(lines) else 0
        if j >= len(lines) or block_indent <= base_indent:
            out.append(stripped[:-2].rstrip() + ":")
            i += 1
            continue
        head = stripped[:-2].rstrip()
        if head:
            out.append(head + ":")
        out.append("")
        out.append("```")
        k = j
        last_content = j
        while k < len(lines) and (
            not lines[k].strip() or len(lines[k]) - len(lines[k].lstrip()) >= block_indent
        ):
            if lines[k].strip():
                last_content = k
            k += 1
        out.extend(
            block_line[block_indent:] if block_line.strip() else ""
            for block_line in lines[j : last_content + 1]
        )
        out.append("```")
        i = last_content + 1
    return "\n".join(out)


def sanitize_docstring(text: str) -> str:
    """Convert common RST docstring idioms to Markdown-safe equivalents."""
    return _convert_rst_inline(_convert_literal_blocks(text))


def escape_mdx_braces(text: str) -> str:
    """Escape curly braces outside code blocks for MDX compatibility."""
    lines = text.split("\n")
    result = []
    in_code_block = False
    for line in lines:
        if line.startswith("```"):
            in_code_block = not in_code_block
        if in_code_block:
            result.append(line)
        else:
            # Escape { and } but not inside inline code (`...`)
            parts = re.split(r"(`[^`]+`)", line)
            escaped_parts = []
            for part in parts:
                if part.startswith("`") and part.endswith("`"):
                    escaped_parts.append(part)  # Leave inline code alone
                else:
                    escaped_parts.append(part.replace("{", "\\{").replace("}", "\\}"))
            result.append("".join(escaped_parts))
    return "\n".join(result)


def render_docstring(obj: griffe.Object) -> str:
    """Render an object's docstring as markdown."""
    if not obj.docstring:
        return ""
    return sanitize_docstring(obj.docstring.value.strip())


def render_parameters(func: griffe.Function) -> str:
    """Render function parameters as a markdown table."""
    params = [p for p in func.parameters if p.name not in ("self", "cls")]
    if not params:
        return ""
    lines = ["| Parameter | Type | Description |", "|---|---|---|"]
    for p in params:
        annotation = str(p.annotation) if p.annotation else ""
        # Clean up annotation display
        annotation = annotation.replace("chaoscypher_core.", "")
        desc = ""
        if func.docstring:
            for section in func.docstring.parsed:
                if section.kind.value == "parameters":
                    for dp in section.value:
                        if dp.name == p.name:
                            desc = dp.description.replace("\n", " ")
        desc = _convert_rst_inline(desc)
        lines.append(
            f"| `{p.name}` | `{escape_table_cell(annotation)}` | {escape_table_cell(desc)} |"
        )
    return "\n".join(lines)


def render_function(func: griffe.Function, heading: str = "####") -> str:
    """Render a function/method as markdown."""
    sig_params = []
    for p in func.parameters:
        if p.name in ("self", "cls"):
            continue
        annotation = f": {p.annotation}" if p.annotation else ""
        default = f" = {p.default}" if p.default else ""
        sig_params.append(f"{p.name}{annotation}{default}")
    sig = ", ".join(sig_params)

    ret = ""
    if func.returns:
        ret = f" -> {func.returns}"

    parts = [
        f"{heading} `{func.name}({sig}){ret}`",
        "",
    ]
    doc = render_docstring(func)
    if doc:
        parts.append(doc)
        parts.append("")
    param_table = render_parameters(func)
    if param_table:
        parts.append(param_table)
        parts.append("")
    return "\n".join(parts)


def render_class(cls: griffe.Class) -> str:
    """Render a class as markdown."""
    parts = [f"### `class {cls.name}`", ""]
    doc = render_docstring(cls)
    if doc:
        parts.append(doc)
        parts.append("")

    # Bases
    if cls.bases:
        bases = ", ".join(str(b) for b in cls.bases)
        parts.append(f"**Bases:** `{bases}`")
        parts.append("")

    # Public methods
    methods = [
        m
        for m in cls.members.values()
        if isinstance(m, griffe.Function) and not m.name.startswith("_") and m.name != "__init__"
    ]
    if methods:
        parts.append("**Methods:**")
        parts.append("")
        parts.extend(render_function(method) for method in sorted(methods, key=lambda m: m.name))

    # Public attributes (from __init__ or class body)
    attrs = [
        a
        for a in cls.members.values()
        if isinstance(a, griffe.Attribute) and not a.name.startswith("_")
    ]
    if attrs:
        parts.append("**Attributes:**")
        parts.append("")
        for attr in sorted(attrs, key=lambda a: a.name):
            annotation = f": `{attr.annotation}`" if attr.annotation else ""
            doc = render_docstring(attr)
            desc = f" — {doc}" if doc else ""
            parts.append(f"- `{attr.name}`{annotation}{desc}")
        parts.append("")

    return "\n".join(parts)


def render_module(module_path: str, loader: griffe.GriffeLoader) -> str:
    """Render a module's public API as markdown."""
    try:
        mod = loader.load(module_path)
    except Exception as e:
        print(f"  WARNING: Could not load {module_path}: {e}", file=sys.stderr)
        return f"\n> Could not auto-generate docs for `{module_path}`.\n"

    parts = [f"## `{module_path}`", ""]
    doc = render_docstring(mod)
    if doc:
        parts.append(doc)
        parts.append("")

    # Render classes first, then standalone functions
    classes = [
        m
        for m in mod.members.values()
        if isinstance(m, griffe.Class) and not m.name.startswith("_")
    ]
    functions = [
        m
        for m in mod.members.values()
        if isinstance(m, griffe.Function) and not m.name.startswith("_")
    ]

    parts.extend(render_class(cls) for cls in sorted(classes, key=lambda c: c.name))
    parts.extend(
        render_function(func, heading="###") for func in sorted(functions, key=lambda f: f.name)
    )

    return "\n".join(parts)


def generate_page(slug: str, page: dict) -> None:
    """Generate a complete API reference page."""
    output_path = DOCS_DIR / f"{slug}.md"
    loader = griffe.GriffeLoader(search_paths=SEARCH_PATHS)

    parts = [
        "---",
        f'title: "{page["title"]}"',
        "---",
        "",
        f"# {page['title']}",
        "",
        page["description"],
        "",
    ]

    for module_path in page["modules"]:
        print(f"  Loading: {module_path}")
        parts.append(render_module(module_path, loader))

    content = "\n".join(parts)
    content = escape_mdx_braces(content)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"  Wrote: {output_path.relative_to(DOCS_DIR.parent.parent)}")


def main() -> None:
    """Generate all Python API reference pages."""
    print("Generating Python API docs...")
    for slug, page in PAGES.items():
        print(f"\n[{page['title']}]")
        generate_page(slug, page)
    print("\nDone.")


if __name__ == "__main__":
    main()
