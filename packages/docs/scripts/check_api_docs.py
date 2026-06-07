"""Diff FastAPI routes against the reference/api markdown.

Walks every @router.<method> decorator under packages/cortex/src/chaoscypher_cortex/features/
and compares the discovered (method, path) tuples to the headers in
packages/docs/docs/reference/api/*.md.

Exit 0 when in sync; exit 1 (with a report) when drift is found.

Usage:
    python check_api_docs.py
    python check_api_docs.py --report report.md
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
FEATURES_DIR = REPO_ROOT / "packages" / "cortex" / "src" / "chaoscypher_cortex" / "features"
DOCS_DIR = REPO_ROOT / "packages" / "docs" / "docs" / "reference" / "api"
ROUTER_FILE = (
    REPO_ROOT / "packages" / "cortex" / "src" / "chaoscypher_cortex" / "api" / "v1" / "router.py"
)

# Root of the cortex source tree — used to resolve dotted module names to file paths.
CORTEX_SRC = REPO_ROOT / "packages" / "cortex" / "src"

METHOD_HEADER_RE = re.compile(
    r"^#{2,4}\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S+)\s*$",
    re.MULTILINE,
)

# Also match bare ``METHOD /path`` lines inside fenced code blocks (the docs
# use descriptive ### headings and put the path in a plain code block beneath,
# e.g.:
#
#   ### List Chats
#
#   ```
#   GET /api/v1/chats
#   ```
#
# This pattern captures those without requiring the path to appear in the heading.
METHOD_CODEBLOCK_RE = re.compile(
    r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/api/v\d+/\S+)\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True, eq=True)
class Endpoint:
    """An HTTP endpoint as ``method`` + ``path`` (hashable for set diffs)."""

    method: str
    path: str

    def __str__(self) -> str:  # noqa: D105 - obvious dunder
        return f"{self.method} {self.path}"


@dataclass
class Diff:
    """Two-set diff of endpoints: code-only (undocumented) vs docs-only (unimplemented)."""

    undocumented: set[Endpoint] = field(default_factory=set)
    unimplemented: set[Endpoint] = field(default_factory=set)


def _router_prefix(tree: ast.Module) -> str:
    """Find the first module-level APIRouter(prefix='...') assignment.

    Prefers top-level (module-scope) assignments because most feature files
    declare ``router = APIRouter()`` or ``router = APIRouter(prefix="...")`` at
    the module level.

    Falls back to searching all ``APIRouter(prefix=...)`` calls anywhere in the
    file (including inside factory functions) so that files like
    ``local_auth/api.py`` — which create the router inside a helper function —
    are also handled correctly.
    """
    # First pass: module-level assignments only (most reliable).
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        call = node.value
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "APIRouter":
            continue
        for kw in call.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)

    # Second pass: any APIRouter(prefix=...) call anywhere in the file.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "APIRouter":
            continue
        for kw in node.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
    return ""


def _module_to_file(module_name: str, cortex_src: Path) -> Path | None:
    """Convert a dotted module name to an absolute file path.

    Tries both ``<path>.py`` (module) and ``<path>/__init__.py`` (package).
    Returns None if neither exists.
    """
    parts = module_name.split(".")
    base = cortex_src
    for part in parts:
        base = base / part
    pkg = base / "__init__.py"
    if pkg.exists():
        return pkg
    mod = base.with_suffix(".py")
    if mod.exists():
        return mod
    return None


def _resolve_through_init(init_file: Path, attr_name: str, cortex_src: Path) -> Path | None:
    """Follow a re-export through a package ``__init__.py``.

    When router.py imports ``from chaoscypher_cortex.features.dashboard import router``
    (a package import), ``_module_to_file`` returns ``dashboard/__init__.py``.  But
    the actual route definitions live in ``dashboard/api.py``.  This function parses
    ``__init__.py`` to find ``from <sub_module> import <attr_name> [as <alias>]`` and
    resolves to the sub-module's file.  Returns ``None`` if the attribute cannot be
    traced to a sub-module.
    """
    try:
        source = init_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except OSError, SyntaxError:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        for alias in node.names:
            exported_as = alias.asname if alias.asname else alias.name
            if exported_as == attr_name:
                # Found the re-export — resolve the sub-module
                return _module_to_file(node.module, cortex_src)
    return None


def _build_wiring_map(  # noqa: C901, PLR0912 - AST walker; each branch maps a distinct router.py wiring pattern
    router_file: Path, cortex_src: Path | None = None
) -> dict[tuple[Path, str], str]:
    """Parse the top-level router.py and return ``{(resolved_file_path, router_var_name): full_prefix}``.

    The key is a 2-tuple so that files exporting multiple routers (e.g.
    ``pause/api.py`` exports both ``sources_router`` and ``system_router``)
    each get their own prefix entry keyed by the variable name of the router
    object they export.

    The full prefix is ``BASE_PREFIX + sub_prefix`` where:
    - ``BASE_PREFIX`` comes from ``api = APIRouter(prefix="...")`` inside
      ``create_api_router()``.
    - ``sub_prefix`` comes from the ``prefix=`` kwarg on each
      ``api.include_router(...)`` call (empty string when the kwarg is absent).

    For routers that declare their own self-prefix (e.g. ``admin_plugins``,
    ``upgrade``), the wired prefix will be just ``BASE_PREFIX`` (no sub-prefix),
    and ``extract_routes_from_features`` composes it with the file's own prefix.

    ``cortex_src`` defaults to ``router_file.parents[3]``, which is the ``src/``
    directory three levels above ``chaoscypher_cortex/api/v1/router.py``.  Pass
    an explicit value when the router file is not at the standard depth (e.g.
    in tests with a temporary directory layout).
    """
    if cortex_src is None:
        # router_file is at <cortex_src>/chaoscypher_cortex/api/v1/router.py
        # parents[0]=v1, [1]=api, [2]=chaoscypher_cortex, [3]=src
        cortex_src = router_file.parents[3]

    source = router_file.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # --- Determine BASE_PREFIX from create_api_router() body ---
    base_prefix = "/api/v1"
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "create_api_router":
            continue
        for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
                continue
            call = stmt.value
            func = call.func
            func_name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if func_name != "APIRouter":
                continue
            for kw in call.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    base_prefix = str(kw.value.value)

    # --- Build alias → (module_name, attr_name) map from all module-level imports ---
    alias_to_module: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        for alias in node.names:
            local_name = alias.asname if alias.asname else alias.name
            attr_name = alias.name
            alias_to_module[local_name] = (node.module, attr_name)

    # --- Parse include_router calls inside create_api_router() ---
    result: dict[tuple[Path, str], str] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "create_api_router":
            continue
        for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
                continue
            call = stmt.value
            if not isinstance(call.func, ast.Attribute) or call.func.attr != "include_router":
                continue
            if not call.args:
                continue

            first_arg = call.args[0]
            if isinstance(first_arg, ast.Name):
                alias = first_arg.id
            elif isinstance(first_arg, ast.Attribute) and isinstance(first_arg.value, ast.Name):
                # e.g. some_module.router
                alias = first_arg.value.id
            else:
                continue

            sub_prefix = ""
            for kw in call.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    sub_prefix = str(kw.value.value)

            if alias not in alias_to_module:
                continue

            module_name, attr_name = alias_to_module[alias]
            file_path = _module_to_file(module_name, cortex_src)
            if file_path is None:
                continue

            # When the import goes through a package __init__.py (e.g.
            # ``from chaoscypher_cortex.features.dashboard import router``),
            # _module_to_file returns the __init__.py.  Follow the re-export
            # chain to find the actual file that defines the router.
            if file_path.name == "__init__.py":
                real_file = _resolve_through_init(file_path, attr_name, cortex_src)
                if real_file is not None:
                    file_path = real_file

            full_prefix = base_prefix + sub_prefix
            resolved = file_path.resolve()
            key = (resolved, attr_name)
            if key not in result:
                result[key] = full_prefix

    return result


def extract_routes_from_features(  # noqa: C901, PLR0912 - AST walker over many feature files; each branch handles a distinct decorator/router shape
    *,
    source_code: str | None = None,
) -> set[Endpoint]:
    """Walk feature api.py modules; return ``{Endpoint(method, full_path)}``.

    Prefixes are composed from two sources:

    1. The wiring in ``api/v1/router.py`` — ``include_router`` calls with
       a ``prefix=`` kwarg supply the sub-prefix appended to ``/api/v1``.
    2. Any self-declared prefix on the router object in the feature file itself
       (e.g. ``router = APIRouter(prefix="/admin/plugins")``).

    The resulting path is: ``wired_prefix + self_declared_prefix + route_path``.

    When ``source_code`` is supplied (unit tests / single-file inspection), the
    wiring map is skipped and only the self-declared prefix is used.
    """
    routes: set[Endpoint] = set()

    if source_code is not None:
        # Single-file path used by unit tests: self-declared prefix only.
        tree = ast.parse(source_code)
        prefix = _router_prefix(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                func = deco.func
                if not isinstance(func, ast.Attribute):
                    continue
                method = func.attr.upper()
                if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                    continue
                if not deco.args or not isinstance(deco.args[0], ast.Constant):
                    continue
                sub_path = str(deco.args[0].value)
                full = (prefix + sub_path).rstrip("/") or sub_path
                routes.add(Endpoint(method=method, path=full))
        return routes

    # Filesystem path: read the wiring map then walk all feature files.
    # Pass CORTEX_SRC explicitly so tests can patch ROUTER_FILE without also
    # patching CORTEX_SRC — _build_wiring_map derives it from ROUTER_FILE when
    # the argument is None.
    wiring_map = _build_wiring_map(ROUTER_FILE)

    source_files: list[Path] = list(FEATURES_DIR.rglob("api.py"))
    for extra in (
        "extraction_api.py",
        "chunks_api.py",
        "tags_api.py",
        "grounding_api.py",
        "execution_api.py",
        "ollama_models_api.py",
    ):
        source_files.extend(FEATURES_DIR.rglob(extra))

    for file_path in source_files:
        code = file_path.read_text(encoding="utf-8")
        tree = ast.parse(code)

        # Self-declared module-level prefix (ignores function-scoped routers).
        self_prefix = _router_prefix(tree)

        resolved = file_path.resolve()

        # Build a var_name → wired_prefix map for this file from all wiring
        # entries whose file matches.  Most files have exactly one entry; files
        # that export multiple routers (e.g. pause/api.py) have one entry per
        # exported router variable, each with its own wired prefix.
        var_to_wired: dict[str, str] = {
            var_name: wired_prefix
            for (fp, var_name), wired_prefix in wiring_map.items()
            if fp == resolved
        }

        # Fallback for decorators whose router variable isn't re-exported through router.py
        # (e.g. a locally-defined sub-router included via router.include_router(...) inside the file).
        fallback_wired = next(iter(var_to_wired.values()), "")

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                func = deco.func
                if not isinstance(func, ast.Attribute):
                    continue
                method = func.attr.upper()
                if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                    continue
                if not deco.args or not isinstance(deco.args[0], ast.Constant):
                    continue
                sub_path = str(deco.args[0].value)

                # Determine which router variable this decorator belongs to
                # (e.g. ``@system_router.get(...)`` → var_name="system_router").
                decorator_var = func.value.id if isinstance(func.value, ast.Name) else ""

                # Resolve the wired prefix for this specific router variable.
                wired_prefix = var_to_wired.get(decorator_var, fallback_wired)

                # Compose the router's mount point.
                # If the self-declared prefix already starts with /api/v (e.g.
                # local_auth's /api/v1/auth, mounted separately in main.py),
                # trust it as-is.
                if self_prefix.startswith("/api/v"):
                    prefix = self_prefix
                else:
                    prefix = wired_prefix + self_prefix

                full = (prefix + sub_path).rstrip("/") or sub_path
                routes.add(Endpoint(method=method, path=full))

    return routes


def extract_endpoints_from_markdown(*, text: str | None = None) -> set[Endpoint]:
    """Pull (method, path) tuples from markdown API docs.

    Recognises two formats:

    1. **Heading format** (used by some docs):
       ``### GET /api/v1/...`` — the method and path appear directly in the
       heading line, matching ``METHOD_HEADER_RE``.

    2. **Code-block format** (the predominant style in this project):
       A descriptive ``###`` heading followed by a fenced code block that
       contains a bare ``METHOD /api/v1/...`` line, matching
       ``METHOD_CODEBLOCK_RE``.

    Both formats are searched so that existing docs with descriptive headings
    are correctly inventoried alongside any new docs that use inline headings.
    """
    out: set[Endpoint] = set()
    if text is not None:
        for match in METHOD_HEADER_RE.finditer(text):
            out.add(Endpoint(method=match.group(1), path=match.group(2)))
        for match in METHOD_CODEBLOCK_RE.finditer(text):
            out.add(Endpoint(method=match.group(1), path=match.group(2)))
        return out
    for md in DOCS_DIR.rglob("*.md"):
        content = md.read_text(encoding="utf-8")
        for match in METHOD_HEADER_RE.finditer(content):
            out.add(Endpoint(method=match.group(1), path=match.group(2)))
        for match in METHOD_CODEBLOCK_RE.finditer(content):
            out.add(Endpoint(method=match.group(1), path=match.group(2)))
    return out


def diff_endpoints(*, code: set[Endpoint], docs: set[Endpoint]) -> Diff:
    """Return the symmetric diff: code-only routes vs docs-only routes."""
    return Diff(undocumented=code - docs, unimplemented=docs - code)


def main() -> int:
    """Entry point: compare code routes vs documented routes and emit a report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, help="Write a markdown report to this path.")
    args = parser.parse_args()

    code = extract_routes_from_features()
    docs = extract_endpoints_from_markdown()
    diff = diff_endpoints(code=code, docs=docs)

    if not diff.undocumented and not diff.unimplemented:
        print(f"OK: API docs in sync ({len(code)} routes, {len(docs)} documented).")
        return 0

    lines: list[str] = ["# API documentation drift", ""]
    if diff.undocumented:
        lines.append("## Routes in code but missing from docs")
        lines.extend(
            f"- {ep}" for ep in sorted(diff.undocumented, key=lambda e: (e.path, e.method))
        )
        lines.append("")
    if diff.unimplemented:
        lines.append("## Routes in docs but missing from code")
        lines.extend(
            f"- {ep}" for ep in sorted(diff.unimplemented, key=lambda e: (e.path, e.method))
        )
        lines.append("")
    report = "\n".join(lines)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    print(report, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
