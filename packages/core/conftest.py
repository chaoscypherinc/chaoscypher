# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Top-level pytest conftest for the core package.

The sole purpose of this file is to make mutation testing work.

mutmut copies the package into ``mutants/`` and only mirrors the
modules it mutates. The partial ``mutants/src/chaoscypher_core/`` tree
then sits on ``sys.path`` ahead of the workspace install and shadows
the rest of the package. Two failure modes follow:

1. Submodules that the test suite needs (``chaoscypher_core.testing``,
   ``.exceptions``, ``.app_config``, ...) are absent from the partial
   copy and import fails with ``ModuleNotFoundError``.
2. Re-bridging the package ``__path__`` to the canonical source
   triggers ``chaoscypher_core.services.__init__`` which mass-imports
   chat / search / numpy. mutmut runs pytest in-process twice (stats +
   real run), so numpy raises "cannot load module more than once per
   process" on the second pass.

To dodge both, this conftest:

* Pre-empts ``chaoscypher_core.services`` with a *stub* module so the
  real ``__init__.py`` never runs. Tests that import
  ``chaoscypher_core.services.llm.spend`` still work because Python
  will treat the stub as the parent package and look up submodules
  against its ``__path__``.
* Extends every relevant package's ``__path__`` to also point at the
  canonical source tree, so missing siblings resolve while the mutated
  files keep priority.

Outside of mutmut this file is a no-op: when the workspace install
already serves ``chaoscypher_core`` the extra ``__path__`` entry is a
duplicate that ``importlib`` deduplicates, and the stub-injection only
runs if the real package's ``__init__.py`` hasn't been imported yet.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


def _candidate_src_roots() -> list[Path]:
    """Return ordered candidate locations of the canonical ``src/`` tree.

    ``here`` is the directory containing this file. Under normal pytest
    it is ``packages/core/`` and the canonical source is ``here/src/...``.
    Under mutmut the file is also_copy'd to ``mutants/conftest.py`` so
    ``here`` is ``packages/core/mutants/`` and the canonical source is
    ``here.parent/src/...``. We always want the *canonical* (complete)
    source, NEVER the mutmut copy at ``here/src/...`` (which is partial).
    """
    here = Path(__file__).resolve().parent
    if here.name == "mutants":
        # Inside the mutmut copy -- the parent's src/ is the canonical tree.
        return [here.parent / "src" / "chaoscypher_core"]
    # Normal pytest -- our own src/ is the canonical tree.
    return [here / "src" / "chaoscypher_core"]


def _install_root() -> Path | None:
    """First existing candidate, or *None* if neither resolves."""
    for cand in _candidate_src_roots():
        if cand.is_dir():
            return cand
    return None


def _extend_path(module_name: str, install_root: Path, relative: str) -> None:
    """Append ``install_root / relative`` to ``module.__path__`` if not already."""
    try:
        mod = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return

    mod_path = getattr(mod, "__path__", None)
    if mod_path is None:
        return

    sibling = install_root / relative
    if sibling.is_dir() and str(sibling) not in list(mod_path):
        mod_path.append(str(sibling))


def _prepend_path(module_name: str, mutant_root: Path, relative: str) -> None:
    """Prepend ``mutant_root / relative`` to ``module.__path__`` if present.

    This is the inverse of :func:`_extend_path`: it makes the mutated copy
    win the submodule lookup so that ``from chaoscypher_core.services.llm
    import spend`` loads the mutated ``spend.py`` rather than the editable-
    install copy. Used to defeat the editable-install precedence problem
    inside the mutmut worker.
    """
    try:
        mod = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return

    mod_path = getattr(mod, "__path__", None)
    if mod_path is None:
        return

    sibling = mutant_root / relative
    if not sibling.is_dir():
        return
    sibling_str = str(sibling)
    # Drop any existing occurrence so we end up at index 0.
    while sibling_str in list(mod_path):
        mod_path.remove(sibling_str)
    mod_path.insert(0, sibling_str)


def _install_services_stub(install_root: Path) -> None:
    """Replace ``chaoscypher_core.services`` with a stub package.

    The real ``services/__init__.py`` re-exports the entire engine
    layer (chat, search, sources, workflows) which transitively
    imports numpy / torch / faster-whisper. mutmut runs pytest twice
    in the same interpreter; the second numpy load fails with
    "cannot load module more than once per process".

    The stub keeps ``services`` importable as a package -- with both
    the mutated and canonical locations on its ``__path__`` -- but
    skips the heavy re-exports. Tests that need them already import
    via the submodule path (``chaoscypher_core.services.llm.spend``)
    rather than the barrel.
    """
    if "chaoscypher_core.services" in sys.modules:
        # The real __init__ already ran (normal pytest path) -- bail.
        return

    parent_name = "chaoscypher_core"
    try:
        importlib.import_module(parent_name)
    except ModuleNotFoundError:
        return

    stub = types.ModuleType(f"{parent_name}.services")
    stub.__doc__ = "Mutmut stub for chaoscypher_core.services (heavy barrel skipped)."

    # Collect every directory contributing to the namespace.
    paths: list[str] = []
    for parent_path in sys.modules[parent_name].__path__:
        candidate = Path(parent_path) / "services"
        if candidate.is_dir() and str(candidate) not in paths:
            paths.append(str(candidate))
    canonical = install_root / "services"
    if canonical.is_dir() and str(canonical) not in paths:
        paths.append(str(canonical))

    stub.__path__ = paths  # type: ignore[attr-defined]
    sys.modules[f"{parent_name}.services"] = stub


def _bridge() -> None:
    install_root = _install_root()
    if install_root is None:
        return

    # Extend chaoscypher_core itself first so the stub installer below
    # can iterate its __path__.
    _extend_path("chaoscypher_core", install_root, ".")

    # Install the services stub BEFORE the real __init__.py has had a
    # chance to run. Then bridge subpackage paths so leaf modules
    # resolve.
    _install_services_stub(install_root)
    _extend_path("chaoscypher_core.services.llm", install_root, "services/llm")
    _extend_path("chaoscypher_core.services.quality", install_root, "services/quality")
    _extend_path("chaoscypher_core.utils", install_root, "utils")

    # Under mutmut the editable install of chaoscypher_core wins lookup
    # for every submodule, so the mutated files in mutants/src/... are
    # never imported and every mutant trivially "survives". Detect the
    # mutant copy and prepend it on each bridged subpackage's __path__
    # so the mutated sources win. This is a no-op outside mutmut.
    here = Path(__file__).resolve().parent
    mutant_root = here / "src" / "chaoscypher_core"
    # Detect "we're inside mutants/" by checking the install_root is the
    # *parent* candidate (not the same as `here / "src"`).
    if mutant_root.is_dir() and str(install_root) != str(mutant_root):
        _prepend_path("chaoscypher_core.services.llm", mutant_root, "services/llm")
        _prepend_path("chaoscypher_core.services.quality", mutant_root, "services/quality")
        _prepend_path("chaoscypher_core.utils", mutant_root, "utils")
        # Path prepending only affects FUTURE module lookups via __path__.
        # Anything already in sys.modules (e.g. modules imported during
        # pytest startup before this conftest ran, or by mutmut's own
        # discovery pass) keeps pointing at the canonical install copy.
        # When a test then does ``from chaoscypher_core.utils.id import
        # generate_id``, it gets the canonical version — coverage never
        # tracks the mutant copy, mutmut sees the function as "no tests",
        # and ``mutate_only_covered_lines = true`` filters out every
        # mutation. Evict mutated subpackages so they get re-imported
        # from the mutant copy on first use.
        _evict_mutated_subpackages()


def _evict_mutated_subpackages() -> None:
    """Drop pre-imported mutmut-target subpackages from sys.modules.

    Forces the next import of ``chaoscypher_core.utils.*``,
    ``chaoscypher_core.services.llm.*``, and
    ``chaoscypher_core.services.quality.*`` to go through Python's import
    machinery again, where the ``__path__`` prepend above will redirect
    to the mutant copy. Without this, coverage attribution silently
    skips files that were already loaded from the canonical install.
    """
    targets = (
        "chaoscypher_core.utils.",
        "chaoscypher_core.services.llm.",
        "chaoscypher_core.services.quality.",
    )
    for name in list(sys.modules):
        if any(name.startswith(t) for t in targets):
            sys.modules.pop(name, None)


_bridge()
