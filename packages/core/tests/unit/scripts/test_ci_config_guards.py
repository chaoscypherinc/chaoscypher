# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Guards on the CI and container configuration that is kept in step by hand.

* Every CI-type workflow job runs where the ``CI_RUNS_ON`` repository variable
  says (a self-hosted runner when set, GitHub-hosted ``ubuntu-latest`` when
  not), so moving CI between runners is one variable, not fifteen edits.
* Publishing workflows stay on GitHub-hosted runners: they publish with OIDC
  trusted publishing and run on the public repository.
* Every PyPI upload carries PEP 740 attestations (#825): a release without
  provenance gave an automated scanner nothing to check its verdict against.
  The publish action is pinned at v1.14.3 or later (GHSA-5xjx-3w67-w9x9).
* Each base image (``python``, ``node``) carries one tag across every
  Dockerfile: the test and multi-container images had drifted three patches
  behind the shipped image (3.14.3 vs 3.14.7) because only the top-level
  Dockerfile was ever bumped.
* Each base image is pinned on its ``FROM`` line: Dependabot bumps ``FROM``
  lines and never an ``ARG`` default, so on 2026-10-17 its Python bump (#784)
  moved seven Dockerfiles and left cortex's and neuron's ARG-pinned 3.14.7.
* setup-uv keeps its own cache default: on the home runners' shared cache, a
  forced Actions cache made the post-job prune time out.
* Graph rows are never deleted by ``source_id`` outside the membership release
  (#795, D5): a node merged across sources has one ``source_id`` and many
  members, so a delete keyed on ``source_id`` destroys the other sources' part.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
import yaml


_ROOT = Path(__file__).resolve().parents[5]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_RUNS_ON_SWITCH = "${{ fromJSON(vars.CI_RUNS_ON || '\"ubuntu-latest\"') }}"
_HOSTED_ONLY = frozenset(
    {
        "auto-tag.yml",
        "publish-ghcr.yml",
        "publish-mcp-registry.yml",
        "publish-pypi.yml",
        "release.yml",
    }
)


def _jobs(path: Path) -> dict[str, dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs", {})


def _workflow_files() -> list[Path]:
    return sorted(_WORKFLOWS.glob("*.yml"))


def test_hosted_only_workflows_exist() -> None:
    """The hosted-only list names real files (a rename would silently drop the guard)."""
    names = {p.name for p in _workflow_files()}
    assert names >= _HOSTED_ONLY, sorted(_HOSTED_ONLY - names)


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_every_job_runs_where_its_workflow_class_says(path: Path) -> None:
    """CI jobs use the CI_RUNS_ON switch; publishing jobs stay on ubuntu-latest."""
    want = "ubuntu-latest" if path.name in _HOSTED_ONLY else _RUNS_ON_SWITCH
    wrong = {
        name: job.get("runs-on")
        for name, job in _jobs(path).items()
        if "uses" not in job and job.get("runs-on") != want
    }
    assert not wrong, f"{path.name}: runs-on must be {want!r}: {wrong}"


_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)", re.MULTILINE | re.IGNORECASE)


def _dockerfiles() -> list[Path]:
    return sorted((_ROOT / "packages" / "docker").rglob("Dockerfile*"))


def _base_image_refs() -> list[tuple[str, str, str | None]]:
    """Every (image name, tag, digest or None) a Dockerfile pins."""
    refs: list[tuple[str, str, str | None]] = []
    for dockerfile in _dockerfiles():
        for ref in _FROM.findall(dockerfile.read_text(encoding="utf-8")):
            if ref.startswith("$") or ":" not in ref:
                continue  # an ARG reference (refused below) or a stage name
            image, _, digest = ref.partition("@")
            name, tag = image.rsplit(":", 1)
            refs.append((name, tag, digest or None))
    return refs


def test_no_dockerfile_takes_its_base_image_from_an_arg() -> None:
    """``FROM ${VAR}`` hides the pin from Dependabot, which bumps ``FROM`` lines only."""
    hidden = [
        f"{path.relative_to(_ROOT)}: FROM {ref}"
        for path in _dockerfiles()
        for ref in _FROM.findall(path.read_text(encoding="utf-8"))
        if ref.startswith("$")
    ]
    assert hidden == [], hidden


def test_the_base_images_are_found() -> None:
    """The scan sees the images it guards (an empty scan would pass vacuously)."""
    assert {"python", "node"} <= {name for name, _, _ in _base_image_refs()}


@pytest.mark.parametrize("image", ["python", "node"])
def test_each_base_image_has_one_tag_across_every_dockerfile(image: str) -> None:
    """One tag per base image: bump them together or the test images drift."""
    tags = {tag for name, tag, _ in _base_image_refs() if name == image}
    assert len(tags) == 1, f"{image} is pinned at several tags: {sorted(tags)}"


def test_one_tag_never_carries_two_digests() -> None:
    """Files pinning the same tag pin the same digest (a half-done bump shows up here).

    A digest wins over its tag at pull time: on 2026-10-15 the cortex and neuron
    images read ``python:3.14.7-slim`` by tag but still carried 3.14.3's digest.
    Whether a lone digest matches its tag needs a registry lookup, which this
    offline test cannot do; this catches the cross-file half of that drift.
    """
    digests: dict[str, set[str]] = {}
    for name, tag, digest in _base_image_refs():
        if digest:
            digests.setdefault(f"{name}:{tag}", set()).add(digest)
    clashes = {ref: sorted(d) for ref, d in digests.items() if len(d) > 1}
    assert not clashes, clashes


def test_the_docker_test_tier_mounts_what_these_guards_read() -> None:
    """The docker-test container sees only what it mounts (#500, #623, then these guards).

    Read from the host-side compose file, so it fails in local mode too, before
    the push gate's 40-minute Docker run finds the gap.
    """
    compose = yaml.safe_load(
        (_ROOT / "packages" / "docker" / "test" / "docker-compose.yml").read_text(encoding="utf-8")
    )
    targets = {
        volume.split(":")[1] for volume in compose["services"]["test"]["volumes"] if ":" in volume
    }
    assert {
        "/app/packages/docker",
        "/app/.github",
        "/app/conftest.py",  # the repo-root pytest hooks (-n auto sizing, CI_TEST_SHARD)
        "/app/.nvmrc",
        "/app/packages/interface/package.json",
    } <= targets


def _action_steps(action: str) -> list[tuple[str, str, dict]]:
    """``(workflow, job, with)`` for every step that uses ``action`` (``owner/name``)."""
    return [
        (path.name, name, step.get("with", {}))
        for path in _workflow_files()
        for name, job in _jobs(path).items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith(f"{action}@")
    ]


def test_every_node_setup_reads_the_one_floor() -> None:
    """One node floor, ``.nvmrc``; only the frontend matrix names versions (the floor and newest).

    Three jobs pinned "24" by hand, and the unit-test job had none, so its leaderboard test ran
    the runner's system node, which cannot strip types (2026-10-15).
    """
    steps = _action_steps("actions/setup-node")
    assert steps
    stray = [
        (wf, job, w)
        for wf, job, w in steps
        if w.get("node-version-file") != ".nvmrc" and w.get("node-version") != "${{ matrix.node }}"
    ]
    assert not stray, stray


def test_no_uv_setup_forces_the_actions_cache() -> None:
    """setup-uv's ``enable-cache: auto`` saves to the Actions cache on GitHub-hosted runners only.

    Every job forced it on with ``true``. The home runners share one persistent
    ``~/.cache/uv``, so the post-job ``uv cache prune`` waited on other runners' uv processes,
    timed out after 300 s, and failed a job whose checks had passed (main, 2026-10-16).
    """
    steps = _action_steps("astral-sh/setup-uv")
    assert steps
    forced = [(wf, job) for wf, job, w in steps if "enable-cache" in w or "prune-cache" in w]
    assert not forced, forced


def test_the_floor_is_the_packages_engine_and_the_matrix_floor() -> None:
    import json

    floor = (_ROOT / ".nvmrc").read_text(encoding="utf-8").strip()
    engines = json.loads(
        (_ROOT / "packages" / "interface" / "package.json").read_text(encoding="utf-8")
    )["engines"]["node"]
    assert engines == f">={floor}.0.0"
    matrix = _jobs(_WORKFLOWS / "ci.yml")["frontend"]["strategy"]["matrix"]["node"]
    assert matrix[0] == floor


_PUBLIC_MIRROR_GUARD = "github.repository == 'chaoscypherinc/chaoscypher'"


@pytest.mark.parametrize("name", sorted(_HOSTED_ONLY))
def test_every_publishing_job_runs_on_the_public_mirror_only(name: str) -> None:
    """The dev repo receives the same vX.Y.Z tags; a publish there would ship from the wrong tree.

    Found 2026-10-15 while enabling Actions on the dev repository: auto-tag, the PyPI, GHCR and MCP
    registry publishes and the release notes had no repository check (they were disabled there
    by hand first; this guard makes it hold whatever the settings say).
    """
    jobs = _jobs(_WORKFLOWS / name)
    assert jobs
    unguarded = [
        job for job, spec in jobs.items() if _PUBLIC_MIRROR_GUARD not in str(spec.get("if", ""))
    ]
    assert not unguarded, f"{name}: jobs without the public-mirror guard: {unguarded}"


_PYPI_PUBLISH = "pypa/gh-action-pypi-publish"
_UNATTESTED_UPLOAD = re.compile(r"\buv\s+publish\b|\btwine\s+upload\b")


def test_every_pypi_upload_carries_pep740_attestations() -> None:
    """Every PyPI upload goes through the PyPA action with attestations on, never ``uv publish``.

    ``uv publish`` uploads no provenance. v0.3.0 shipped without it, and an automated scanner
    listed chaoscypher-cli and -neuron 0.3.0 as malicious (#825) with nothing on PyPI to show
    which workflow built them. The action signs a PEP 740 attestation per file.
    """
    steps = _action_steps(_PYPI_PUBLISH)
    assert [(wf, job) for wf, job, _ in steps] == [("publish-pypi.yml", "publish")], steps
    for wf, job, w in steps:
        assert str(w.get("attestations", "true")).lower() != "false", (wf, job)
    publish = _jobs(_WORKFLOWS / "publish-pypi.yml")["publish"]
    assert publish["permissions"]["id-token"] == "write"
    uses = next(
        s["uses"] for s in publish["steps"] if str(s.get("uses", "")).startswith(_PYPI_PUBLISH)
    )
    assert re.fullmatch(rf"{_PYPI_PUBLISH}@[0-9a-f]{{40}}", uses), uses
    unattested = [
        (path.name, name)
        for path in _workflow_files()
        for name, job in _jobs(path).items()
        for step in job.get("steps", [])
        if _UNATTESTED_UPLOAD.search(str(step.get("run", "")))
    ]
    assert not unattested, f"uploads without attestations: {unattested}"


# GHSA-5xjx-3w67-w9x9 (critical, 2026-10-21): up to v1.14.2 the action trusted a
# revoked CA (emSign) for Trusted Publishing and Sigstore signing.
_PYPI_PUBLISH_FLOOR = (1, 14, 3)
_PYPI_PUBLISH_PIN = re.compile(
    rf"uses:\s*{_PYPI_PUBLISH}@[0-9a-f]{{40}}\s+#\s*v(\d+)\.(\d+)\.(\d+)\s*$", re.MULTILINE
)


def test_the_pypi_publish_pin_is_past_the_revoked_ca_advisory() -> None:
    """The SHA pin carries its ``# vX.Y.Z`` tag, and that tag is the patched v1.14.3 or later."""
    text = (_WORKFLOWS / "publish-pypi.yml").read_text(encoding="utf-8")
    pins = [tuple(int(n) for n in m.groups()) for m in _PYPI_PUBLISH_PIN.finditer(text)]
    assert len(pins) == text.count(f"{_PYPI_PUBLISH}@"), "a pin without its # vX.Y.Z comment"
    assert pins, "no pypa/gh-action-pypi-publish pin found"
    assert all(v >= _PYPI_PUBLISH_FLOOR for v in pins), (pins, "GHSA-5xjx-3w67-w9x9")


_CORE_SRC = _ROOT / "packages" / "core" / "src" / "chaoscypher_core"
_GRAPH_NAME = re.compile(r"\bGraph(?:Node|Edge|Template)\b|\bgraph_(?:nodes|edges|templates)\b")
_SQL_DELETE = re.compile(r"\bDELETE\s+FROM\s+graph_(?:nodes|edges|templates)\b", re.IGNORECASE)
_RELEASE_HOME = _CORE_SRC / "adapters" / "sqlite" / "repos" / "graph" / "sqlite_membership_ops.py"


def _code(node: ast.AST) -> str:
    """``node``'s source without docstrings (a docstring may name both words harmlessly)."""
    clone = ast.parse(ast.unparse(node))
    for item in ast.walk(clone):
        body = getattr(item, "body", None)
        if (
            isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body[0] = ast.Pass()
    return ast.unparse(clone)


def _deletes_graph_rows(func: ast.AST) -> bool:
    """A delete of a graph table: ``delete(GraphX...)``, ``<...GraphX...>.delete()``, raw SQL."""
    for node in ast.walk(func):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _SQL_DELETE.search(node.value):
                return True
            continue
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in {"delete", "sa_delete"}:
            if node.args and _GRAPH_NAME.search(ast.unparse(node.args[0])):
                return True
        if isinstance(node.func, ast.Attribute) and node.func.attr == "delete" and not node.args:
            if _GRAPH_NAME.search(ast.unparse(node.func.value)):
                return True
    return False


def _deletes_by_source_id(tree: ast.AST) -> list[int]:
    """Lines of the functions that both delete graph rows and mention ``source_id``.

    Read per function, not per statement: a delete keyed on ``source_id`` is often
    built over several statements (``stmt = delete(GraphNode)`` then
    ``stmt = stmt.where(GraphNode.source_id == x)``) or from a prepared condition.
    """
    return sorted(
        func.lineno
        for func in ast.walk(tree)
        if isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef)
        and _deletes_graph_rows(func)
        and "source_id" in _code(func)
    )


_BANNED = """
def one(s, x):
    s.exec(delete(GraphNode).where(GraphNode.source_id == x))
def split(s, x):
    stmt = delete(GraphNode)
    stmt = stmt.where(GraphNode.source_id == x)
    s.exec(stmt)
def prepared(s, x):
    cond = col(GraphEdge.source_id) == x
    s.exec(sa_delete(GraphEdge).where(cond))
def core(s, x):
    s.execute(GraphNode.__table__.delete().where(GraphNode.__table__.c.source_id == x))
def query(s, x):
    s.query(GraphTemplate).filter_by(source_id=x).delete()
def raw(s, x):
    s.execute(text("DELETE FROM graph_edges WHERE source_id = :x"), {"x": x})
"""

_ALLOWED = '''
def by_id(s, ids):
    s.exec(delete(GraphNode).where(col(GraphNode.id).in_(ids)))
def citations(s, x):
    s.exec(delete(SourceCitation).where(SourceCitation.source_id == x))
def documented(s, ids):
    """Deletes by id, never by source_id."""
    s.exec(delete(GraphEdge).where(col(GraphEdge.id).in_(ids)))
'''


def test_the_graph_delete_guard_sees_a_delete_by_source_id() -> None:
    """The scan finds the shapes it bans (a guard that matches nothing guards nothing)."""
    assert _deletes_by_source_id(ast.parse(_BANNED)) == [2, 4, 8, 11, 13, 15]
    assert _deletes_by_source_id(ast.parse(_ALLOWED)) == []


def test_no_graph_delete_by_source_id_outside_membership_ops() -> None:
    """Every source delete path releases memberships instead (D5); only the release may key on it.

    Until #795 the re-commit cleanup and the force re-extract deleted a source's nodes and edges by
    ``source_id``, taking every other source's contribution merged into them.
    """
    assert _RELEASE_HOME.is_file()
    offenders = [
        f"{path.relative_to(_ROOT)}:{line}"
        for path in sorted(_CORE_SRC.rglob("*.py"))
        if path != _RELEASE_HOME
        for line in _deletes_by_source_id(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not offenders, offenders


# A sweep that finds graph rows by their home source and deletes them by id is a delete by
# ``source_id`` split over two functions, which the scan above reads one function at a time:
# the orphan cleanup deleted merged nodes another source still attested (#795). Every
# finder ``*_by_source`` whose ids a function hands to a repository id delete must exclude
# what a source still holds: a node or edge with a membership, a template a row uses.

#: Repository id deletes, and what a ``*_by_source`` finder feeding each must read to exclude
#: what a source still holds.
_ID_DELETES = {
    "delete_nodes_batch": "GraphNodeSource",
    "delete_edges_batch": "GraphEdgeSource",
    "delete_templates_batch": "template_id",
}
_KIND_OF_FINDER = {
    "nodes": "delete_nodes_batch",
    "edges": "delete_edges_batch",
    "templates": "delete_templates_batch",
}


def _source_finders_feeding_id_deletes() -> set[str]:
    """Every ``find_*_by_source`` called in a function that also calls a repository id delete."""
    found: set[str] = set()
    for root in _SRC_ROOTS:
        for path in sorted(root.rglob("*.py")):
            for func in _functions(path):
                names = {_called(n) for n in ast.walk(func) if isinstance(n, ast.Call)}
                if names & set(_ID_DELETES):
                    found |= {n for n in names if n and re.fullmatch(r"find_\w+_by_source", n)}
    return found


def test_a_source_finder_feeding_a_graph_delete_excludes_what_a_source_holds() -> None:
    """The orphan sweep's finders read membership (nodes, edges) or usage (templates)."""
    finders = _source_finders_feeding_id_deletes()
    assert finders == {"find_orphaned_nodes_by_source", "find_orphaned_templates_by_source"}
    for finder in sorted(finders):
        kind = next(k for k in _KIND_OF_FINDER if f"_{k}_" in finder)
        needs = _ID_DELETES[_KIND_OF_FINDER[kind]]
        bodies = [
            _code(func)
            for path in sorted(_CORE_SRC.joinpath("adapters").rglob("*.py"))
            for func in _functions(path)
            if func.name == finder
        ]
        assert bodies, f"{finder} has no implementation"
        assert all(needs in body for body in bodies), f"{finder} does not read {needs}"


# A GraphRepository on an adapter's session is built in one place (rule 3)
#
# ``repo_factories.get_transaction_graph_repository`` is the one constructor of a repository
# on an adapter's own session: hand-built copies drifted (a None check here, a different
# error there, a third copy in the benchmark). ``get_graph_repository(adapter.session, ...)``
# was a second route to the same thing with no None check (#795 follow-up N2), so both
# builders are checked, and ``get_graph_repository`` is the one place ``GraphRepository`` is
# constructed at all.

#: The calls that build a graph repository on a session they are handed.
_REPO_BUILDERS = frozenset({"GraphRepository", "get_graph_repository"})
_REPO_FACTORIES = _CORE_SRC / "repo_factories"
#: Script trees outside the packages that build repositories too (tests excluded: fixtures).
_SCRIPT_ROOTS = [_ROOT / "e2e" / "fixtures", _ROOT / "internal" / "benchmarks" / "tools"]


def _guarded_files() -> list[Path]:
    """Every Python file the repository guards scan: package src plus the script trees."""
    return [
        path
        for root in [*_SRC_ROOTS, *(r for r in _SCRIPT_ROOTS if r.is_dir())]
        for path in sorted(root.rglob("*.py"))
        if "tests" not in path.parts
    ]


def _import_aliases(tree: ast.AST, names: frozenset[str]) -> dict[str, str]:
    """Local name -> imported name for every ``from x import name [as alias]`` of ``names``."""
    return {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in names
    }


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """The nodes of ``scope`` itself: nested functions and classes are scopes of their own."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        out.append(node)
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            stack.extend(ast.iter_child_nodes(node))
    return out


def _session_aliases(nodes: list[ast.AST]) -> set[str]:
    """Targets (``s``, ``self._s``) assigned from ``x.session`` among ``nodes``."""
    aliases: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Attribute) and value.attr == "session":
            aliases.update(
                ast.unparse(t) for t in targets if isinstance(t, ast.Name | ast.Attribute)
            )
    return aliases


def _session_arg(call: ast.Call) -> ast.expr | None:
    """The session a repository build is handed: its first argument or ``session=``."""
    if call.args:
        return call.args[0]
    return next((k.value for k in call.keywords if k.arg == "session"), None)


def _adapter_session_builds(tree: ast.AST) -> list[int]:
    """Lines that build a graph repository on an adapter's session (``x.session``).

    A builder imported under another name counts, and so does a session alias:
    a name assigned from ``x.session`` in the same function (``s = adapter.session``
    or ``s: Session = adapter.session``), or an attribute of ``self`` assigned
    from it anywhere in the class (``self._s = adapter.session``). Names are
    scoped: one function's alias is not another's plain ``session``.
    """
    builders = set(_REPO_BUILDERS) | set(_import_aliases(tree, _REPO_BUILDERS))
    lines: set[int] = set()

    def check(nodes: list[ast.AST], aliases: set[str]) -> None:
        for node in nodes:
            if not (isinstance(node, ast.Call) and _called(node) in builders):
                continue
            arg = _session_arg(node)
            if (isinstance(arg, ast.Attribute) and arg.attr == "session") or (
                isinstance(arg, ast.Name | ast.Attribute) and ast.unparse(arg) in aliases
            ):
                lines.add(node.lineno)

    def visit(scope: ast.AST, inherited: set[str]) -> None:
        nodes = _own_nodes(scope)
        if isinstance(scope, ast.ClassDef):
            methods = [n for n in nodes if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
            own = {
                a for m in methods for a in _session_aliases(_own_nodes(m)) if a.startswith("self.")
            }
            inherited = inherited | own
        else:
            check(nodes, inherited | _session_aliases(nodes))
        for node in nodes:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(node, {a for a in inherited if a.startswith("self.")})

    visit(tree, set())
    return sorted(lines)


def test_the_adapter_session_repository_guard_sees_its_shapes() -> None:
    for banned in (
        "GraphRepository(adapter.session, db)",
        "GraphRepository(self.adapter.session, db)",
        "GraphRepository(session=storage.session, database_name=db)",
        "get_graph_repository(adapter.session, db)",
        "get_graph_repository(self.session, db)",
        "def f(adapter, db):\n    s = adapter.session\n    return get_graph_repository(s, db)",
        "def f(adapter, db):\n    s: Session = adapter.session\n    return GraphRepository(s, db)",
        "from x import GraphRepository as GR\ndef f(adapter, db):\n    return GR(adapter.session, db)",
        "from x import get_graph_repository as ggr\nggr(adapter.session, db)",
        "class K:\n    def __init__(self, adapter):\n        self._s = adapter.session\n"
        "    def repo(self, db):\n        return GraphRepository(self._s, db)",
    ):
        assert _adapter_session_builds(ast.parse(banned)), banned
    for allowed in (
        "GraphRepository(session, db)",
        "get_graph_repository(session, settings.current_database)",
        "GraphRepository(self._graph_session, db)",
        "get_transaction_graph_repository(adapter, db)",
        # another function's alias is not this function's request session
        "def f(adapter):\n    session = adapter.session\n    return session\n"
        "def g(session, db):\n    return get_graph_repository(session, db)",
    ):
        assert not _adapter_session_builds(ast.parse(allowed)), allowed


def test_a_repository_on_an_adapters_session_is_built_only_by_the_factory() -> None:
    """``get_transaction_graph_repository(adapter, db)``: never ``GraphRepository``/``get_graph_repository`` on ``x.session``."""
    offenders = [
        f"{path.relative_to(_ROOT)}:{line}"
        for path in _guarded_files()
        if _REPO_FACTORIES not in path.parents
        for line in _adapter_session_builds(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not offenders, offenders


def _constructions(tree: ast.AST) -> list[int]:
    """Lines that call ``GraphRepository`` under its own name or an import alias."""
    names = {"GraphRepository"} | set(_import_aliases(tree, frozenset({"GraphRepository"})))
    return [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and _called(n) in names]


def test_the_constructor_guard_sees_an_aliased_import() -> None:
    assert _constructions(ast.parse("from x import GraphRepository as GR\nGR(s, db)\n")) == [2]
    assert not _constructions(ast.parse("get_graph_repository(s, db)\n"))


def test_only_the_factory_constructs_a_graph_repository() -> None:
    """``get_graph_repository`` is the one constructor; ``get_transaction_graph_repository`` delegates to it."""
    offenders = [
        f"{path.relative_to(_ROOT)}:{line}"
        for path in _guarded_files()
        if _REPO_FACTORIES not in path.parents
        for line in _constructions(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not offenders, offenders
    factory = ast.parse((_REPO_FACTORIES / "graph_factory.py").read_text(encoding="utf-8"))
    builders = {
        func.name
        for func in ast.walk(factory)
        if isinstance(func, ast.FunctionDef) and _constructions(func)
    }
    assert builders == {"get_graph_repository"}, builders


# ---------------------------------------------------------------------------
# A whole-index rebuild runs only where a full rebuild is the job (#795 follow-up N1)
# ---------------------------------------------------------------------------
#
# ``rebuild_node_fulltext`` reads every graph node and rewrites every full-text row under
# the writer lock. The identity resolver ran it after any merge, so a source delete that
# triggered a merge rewrote the whole table; it now refreshes the rows it changed
# (``refresh_node_fulltext``). A caller that knows what it changed takes the scoped form.

#: Callers of the whole-index rebuild, and why theirs is a full rebuild.
_FULL_REBUILDERS = {
    "packages/core/src/chaoscypher_core/benchmark/reindex.py": "an explicit reindex of the whole graph",
    "packages/core/src/chaoscypher_core/adapters/sqlite/repos/search.py": (
        "reindex_all_nodes on a keyword-only repository: the full-text half of a full rebuild"
    ),
}


def _full_rebuild_calls(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called(node) == "rebuild_node_fulltext"
    ]


def test_the_full_rebuild_guard_sees_a_call() -> None:
    assert _full_rebuild_calls(ast.parse("def f(s, r):\n    s.rebuild_node_fulltext(r)\n")) == [2]
    assert not _full_rebuild_calls(ast.parse("def f(s, ids):\n    s.refresh_node_fulltext(ids)\n"))


def test_the_whole_node_index_is_rebuilt_only_by_a_full_reindex() -> None:
    seen: set[str] = set()
    offenders: list[str] = []
    for path in _guarded_files():  # the roots the repository guards scan, scripts included
        lines = _full_rebuild_calls(ast.parse(path.read_text(encoding="utf-8")))
        if not lines:
            continue
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _FULL_REBUILDERS:
            seen.add(rel)
            continue
        offenders += [f"{rel}:{line}" for line in lines]
    assert not offenders, f"take refresh_node_fulltext(changed ids) instead: {offenders}"
    assert seen == set(_FULL_REBUILDERS), sorted(set(_FULL_REBUILDERS) - seen)


# ---------------------------------------------------------------------------
# Graph rows are filtered by source through their membership (#795, D7)
# ---------------------------------------------------------------------------
#
# A merged node is attested by every source it came from; its ``source_id`` is
# only its home. A reader that filters graph rows by source on the home column
# (``GraphNode.source_id.in_(...)``, or a join of it to ``sources`` for the
# enabled flag) hides or miscounts merged rows. Readers build their condition
# in ``source_visibility`` (``visible``, ``attested_by``, ``in_source_scope``,
# ``attestations``); the release code keeps the home column on purpose.

_HOME_SOURCE_FILTER = re.compile(
    # SQL: an ``IN`` or ``==`` on a graph entity's (or an aliased one's) home column, or the
    # home column joined to ``sources`` for its enabled flag.
    r"\b[A-Z]\w*(Node|Edge)\.source_id\)?\.in_\("
    r"|\b[A-Z]\w*(Node|Edge)\.source_id\)?\s*=="
    r"|\bGraphTemplate\.source_id\s*==\s*SourceRow\.id"
    # In memory: a loaded row's home column tested against a set of sources.
    r"|getattr\(\w+, \"source_id\"[^)]*\)\s+(not\s+)?in\b"
    r"|\b\w+\.source_id\s+(not\s+)?in\s+\w*source"
)
#: The tool handlers read a loaded node's scope through ``item_in_source_scope`` only: any
#: ``getattr(node, "source_id")`` there is a home-column scope check split over two lines.
_HANDLERS = _CORE_SRC / "services" / "workflows" / "tools" / "engine" / "handlers"
_HANDLER_HOME_READ = re.compile(r"getattr\(\w+, \"source_id\"")


def _home_source_filters(text: str, *, handler: bool = False) -> list[int]:
    return [
        i
        for i, line in enumerate(text.splitlines(), 1)
        if _HOME_SOURCE_FILTER.search(line) or (handler and _HANDLER_HOME_READ.search(line))
    ]


def test_the_home_source_filter_guard_sees_its_shapes() -> None:
    """The scan finds the shapes it bans and passes the shared conditions."""
    banned = (
        "stmt.where(col(GraphNode.source_id).in_(ids))\n"
        "stmt.where(GraphEdge.source_id.in_(ids))\n"
        "stmt.outerjoin(SourceRow, GraphNode.source_id == SourceRow.id)\n"
        "stmt.where(col(SrcNode.source_id).in_(ids))\n"
        "stmt.where(GraphNode.source_id == sid)\n"
        'if getattr(n, "source_id", None) not in sources:\n'
        "nodes = [n for n in nodes if n.source_id in source_ids]\n"
        "stmt.outerjoin(SourceRow, GraphTemplate.source_id == SourceRow.id)\n"
    )
    allowed = (
        "stmt.where(attested_by(GraphNode, ids))\n"
        "stmt.where(col(GraphNode.id).in_(ids))\n"
        "stmt.where(~exists().where(SourceRow.id == GraphNode.source_id))\n"
        "stmt.where(GraphTemplate.source_id == sid)\n"
        "if item_in_source_scope(n, source_ids):\n"
    )
    assert _home_source_filters(banned) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert _home_source_filters(allowed) == []
    two_lines = 'sid = getattr(node, "source_id", None)\nif sid and sid not in source_ids:\n'
    assert _home_source_filters(two_lines) == []
    assert _home_source_filters(two_lines, handler=True) == [1]


def test_no_graph_reader_filters_by_the_home_source() -> None:
    """Every graph reader's source filter reads membership (``source_visibility``), not the home column."""
    assert _HANDLERS.is_dir()
    offenders = [
        f"{path.relative_to(_ROOT)}:{line}"
        for root in _SRC_ROOTS
        for path in sorted(root.rglob("*.py"))
        if path != _RELEASE_HOME
        for line in _home_source_filters(
            path.read_text(encoding="utf-8"), handler=_HANDLERS in path.parents
        )
    ]
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# Source rows are deleted only after a release (#795)
# ---------------------------------------------------------------------------
#
# ``graph_*.source_id`` is ``ON DELETE SET NULL``: deleting a source row takes no graph rows
# with it, and a row it leaves behind has no member and no home, so no later release can
# find it. Every function that deletes source rows is known here, and every caller of one is
# listed with why it is safe.

_SRC_ROOTS = [_ROOT / "packages" / p / "src" for p in ("core", "cortex", "neuron", "cli")]

#: Functions that delete ``SourceRow``s themselves: (file under packages/, function).
_SOURCE_ROW_DELETERS = {
    ("core/src/chaoscypher_core/adapters/sqlite/mixins/source_deletion.py", "delete_source_db"),
    ("core/src/chaoscypher_core/adapters/sqlite/mixins/sources.py", "delete_all_sources"),
    ("core/src/chaoscypher_core/adapters/sqlite/mixins/sources_citations.py", "clear_all"),
}
#: Method names that delete source rows when called (``SourcesMixin.delete_source`` wraps
#: ``delete_source_db``; ``SourceCitationsMixin.clear_all`` takes the database name, while
#: ``GraphRepository.clear_all()`` takes nothing and is not one).
_DELETING_CALLS = frozenset(
    {"delete_source_db", "delete_all_sources", "delete_source", "clear_all"}
)

_RELEASES_FIRST = "releases the graph first, in the same transaction"
#: Every caller of a deleting call: (file under packages/, function, method) -> why it is safe.
_SOURCE_DELETE_CALLERS = {
    (
        "core/src/chaoscypher_core/services/graph/management/source.py",
        "_delete_source_impl",
        "delete_source_db",
    ): _RELEASES_FIRST,
    (
        "core/src/chaoscypher_core/services/reset/data_reset.py",
        "reset_source_processing_history",
        "delete_all_sources",
    ): _RELEASES_FIRST,
    (
        "core/src/chaoscypher_core/services/reset/operations.py",
        "_delete_source_data",
        "delete_all_sources",
    ): _RELEASES_FIRST,
    (
        "core/src/chaoscypher_core/adapters/sqlite/mixins/sources.py",
        "delete_source",
        "delete_source_db",
    ): "the SourcesMixin.delete_source wrapper itself; its callers are listed here",
    (
        "core/src/chaoscypher_core/operations/sources/url_fetch_handler.py",
        "import_url_page",
        "delete_source",
    ): "the URL-fetch placeholder row: created and deleted in one fetch, never extracted, "
    "so it never holds graph rows",
    (
        "core/src/chaoscypher_core/operations/sources/site_import_handler.py",
        "handle_page",
        "delete_source",
    ): "a site page's failed-fetch placeholder (status error, stage url_fetch): it never "
    "held content, so it never holds graph rows",
    (
        "core/src/chaoscypher_core/mcp/memory.py",
        "remove_source",
        "delete_source",
    ): "SourceService.delete_source, which releases",
    (
        "cli/src/chaoscypher_cli/sources/deletion.py",
        "delete_source_cascade",
        "delete_source",
    ): "SourceService.delete_source, which releases (source delete and source site delete)",
    (
        "cortex/src/chaoscypher_cortex/features/sources/service.py",
        "delete_source",
        "delete_source",
    ): "the engine SourceService.delete_source, which releases",
}


def _functions(path: Path) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


def _rel(path: Path) -> str:
    return path.relative_to(_ROOT / "packages").as_posix()


def _deleting_calls(func: ast.AST) -> list[tuple[str, int]]:
    """(method, line) of every call in ``func`` that deletes source rows."""
    return [
        (node.func.attr, node.lineno)
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _DELETING_CALLS
        and (node.func.attr != "clear_all" or node.args or node.keywords)
    ]


def _deletes_source_rows(func: ast.AST) -> bool:
    code = _code(func)
    return "delete(SourceRow" in code or (
        "get(SourceRow" in code and re.search(r"session\.delete\(", code) is not None
    )


def test_every_function_that_deletes_source_rows_is_known() -> None:
    """A new way to delete source rows must join the list, and so the caller guard below."""
    found = {
        (_rel(path), func.name)
        for root in _SRC_ROOTS
        for path in sorted(root.rglob("*.py"))
        for func in _functions(path)
        if _deletes_source_rows(func)
    }
    assert found == _SOURCE_ROW_DELETERS


def test_every_source_row_delete_is_preceded_by_a_release() -> None:
    """Each caller is listed with its reason; a releasing caller releases before it deletes."""
    callers: dict[tuple[str, str, str], tuple[ast.AST, int]] = {}
    for root in _SRC_ROOTS:
        for path in sorted(root.rglob("*.py")):
            for func in _functions(path):
                if (_rel(path), func.name) in _SOURCE_ROW_DELETERS:
                    continue
                for method, line in _deleting_calls(func):
                    callers.setdefault((_rel(path), func.name, method), (func, line))
    assert set(callers) == set(_SOURCE_DELETE_CALLERS), sorted(
        set(callers) ^ set(_SOURCE_DELETE_CALLERS)
    )
    for key, reason in _SOURCE_DELETE_CALLERS.items():
        if reason != _RELEASES_FIRST:
            continue
        func, line = callers[key]
        releases = [
            node.lineno
            for node in ast.walk(func)
            if isinstance(node, ast.Call) and _called(node) in _RELEASES
        ]
        assert releases, f"{key} deletes source rows without a release"
        assert min(releases) < line, f"{key} releases only after deleting the source rows"


# ---------------------------------------------------------------------------
# One source is released only through release_source_graph (#795, D3/D5)
# ---------------------------------------------------------------------------
#
# ``release_source`` takes a source's memberships and leaves every survivor as it was,
# still carrying what the released source wrote. ``release_source_graph`` releases and
# then rebuilds each survivor from the sources it still has, so it is the only caller.

#: Calls that release graph rows: the repository's and the rebuilding wrapper.
_RELEASES = frozenset({"release_source", "release_all_sources", "release_source_graph"})
_RELEASE_WRAPPER = _CORE_SRC / "services" / "graph" / "management" / "release.py"


def _called(node: ast.Call) -> str | None:
    """The name a call calls: ``f(...)`` and ``x.f(...)`` both give ``f``."""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def test_a_single_source_is_released_only_through_the_rebuilding_wrapper() -> None:
    """A bare ``release_source`` call would leave survivors holding the released source's text."""
    assert _RELEASE_WRAPPER.is_file()
    offenders = [
        f"{_rel(path)}:{node.lineno}"
        for root in _SRC_ROOTS
        for path in sorted(root.rglob("*.py"))
        if path not in {_RELEASE_WRAPPER, _RELEASE_HOME}
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and _called(node) == "release_source"
    ]
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# Every release reaches the post-release refresh (#795, D3)
# ---------------------------------------------------------------------------
#
# A release rebuilds survivors in its transaction; their search rows and embeddings
# follow only through ``release.refresh_released_graph`` after the commit. A caller that
# drops the returned release leaves stale vectors naming the released source.

#: Calls that hand back a release (``ReleasedRows``, or a ``SourceDeletion`` carrying one).
_RELEASE_CARRIERS = frozenset(
    {"release_source_graph", "force_re_extract", "_cleanup_previous_commit"}
)
#: Calls that run the post-release refresh.
_REFRESHES = frozenset(
    {
        "refresh_released_graph",
        "refresh_after_release",
        "refresh_after_release_sync",
        "_refresh_released",
    }
)
#: Functions that hand the release on to their callers instead (checked in turn by name).
_RELEASE_PASS_THROUGH = {
    (
        "core/src/chaoscypher_core/services/graph/management/source.py",
        "_delete_source_impl",
    ): "returns it in SourceDeletion: every delete_source(search_repo=...) caller is checked",
    (
        "core/src/chaoscypher_core/services/sources/management/re_extraction.py",
        "force_re_extract",
    ): "returns it: every force_re_extract caller is checked",
    (
        "core/src/chaoscypher_core/services/sources/engine/commit/service.py",
        "_cleanup_previous_commit",
    ): "returns it in its stats: every _cleanup_previous_commit caller is checked",
}


def _hands_back_a_release(node: ast.Call) -> bool:
    name = _called(node)
    if name in _RELEASE_CARRIERS:
        return True
    # SourceService.delete_source takes search_repo; the adapter's delete_source does not
    return name == "delete_source" and any(k.arg == "search_repo" for k in node.keywords)


def test_every_release_reaches_the_post_release_refresh() -> None:
    """Each function that gets a release back refreshes it, or is a listed pass-through."""
    seen: set[tuple[str, str]] = set()
    missing: list[str] = []
    for root in _SRC_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if path == _RELEASE_WRAPPER:
                continue
            for func in _functions(path):
                calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)]
                if not any(_hands_back_a_release(c) for c in calls):
                    continue
                key = (_rel(path), func.name)
                seen.add(key)
                if key in _RELEASE_PASS_THROUGH:
                    continue
                if not any(_called(c) in _REFRESHES for c in calls):
                    missing.append(f"{key[0]}::{key[1]}")
    assert not missing, f"releases never refreshed: {missing}"
    assert set(_RELEASE_PASS_THROUGH) <= seen, sorted(set(_RELEASE_PASS_THROUGH) - seen)


# ---------------------------------------------------------------------------
# One reader of the vector switch (memory 0.6.0 item 1, D1 and D2)
# ---------------------------------------------------------------------------
#
# ``embedding_provider_for(settings)`` is the only code that reads
# ``search.enable_vector_search``: everything in the Engine's reach asks it for a
# provider and treats None as keyword-only. A second reader would let one path
# embed while the switch says off. Building a provider straight from the factory
# skips the switch, so ``create_embedding_provider`` is called only where the
# switch does not reach (the Docker stack, D2) or the caller picks its embedder
# on purpose.

_SWITCH_READER = "packages/core/src/chaoscypher_core/adapters/embedding/factory.py"

#: Callers of ``create_embedding_provider`` and why each may skip the switch.
_FACTORY_CALLERS = {
    _SWITCH_READER: "embedding_provider_for, the switch's one reader",
    "packages/core/src/chaoscypher_core/repo_factories/embedding_factory.py": (
        "get_embedding_service, the process-wide provider; its callers are pinned below"
    ),
    "packages/cortex/src/chaoscypher_cortex/features/sources/service.py": "the Docker stack (D2)",
    "packages/neuron/src/chaoscypher_neuron/handlers/template_embedding.py": (
        "the Docker stack (D2)"
    ),
    "packages/neuron/src/chaoscypher_neuron/setup/ops_handlers.py": "the Docker stack (D2)",
    "packages/cli/src/chaoscypher_cli/benchmark/orchestrator.py": (
        "the benchmark picks its embedder explicitly"
    ),
}


#: Callers of ``get_embedding_service`` (the process-wide provider, which never asks the
#: switch) and why each may: only the Docker stack's queue handlers and services reach it.
_SHARED_PROVIDER_CALLERS = {
    "packages/core/src/chaoscypher_core/operations/extraction/extraction_finalizer.py": (
        "the finalize_extraction queue handler (D2)"
    ),
    "packages/core/src/chaoscypher_core/operations/extraction/chunk_extraction_service.py": (
        "the extract_chunk queue handler (D2)"
    ),
    "packages/core/src/chaoscypher_core/streaming/chat/utils.py": (
        "the chat worker's default (SHARED_EMBEDDER, D2); the CLI passes its own provider"
    ),
    "packages/cortex/src/chaoscypher_cortex/features/health/service.py": "the Docker stack (D2)",
    "packages/cortex/src/chaoscypher_cortex/features/search/service.py": "the Docker stack (D2)",
    "packages/cortex/src/chaoscypher_cortex/features/nodes/service.py": "the Docker stack (D2)",
    "packages/neuron/src/chaoscypher_neuron/worker.py": "the Docker stack (D2)",
}


def _switch_reads(tree: ast.AST) -> list[int]:
    """Lines that read an attribute named ``enable_vector_search`` (an assignment is no read)."""
    return [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and n.attr == "enable_vector_search"
        and isinstance(n.ctx, ast.Load)
    ]


def _calls_to(name: str) -> Any:
    """A finder of the lines that call ``name`` under its own name or an import alias."""

    def find(tree: ast.AST) -> list[int]:
        names = {name} | set(_import_aliases(tree, frozenset({name})))
        return [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and _called(n) in names]

    return find


_factory_calls = _calls_to("create_embedding_provider")


def test_the_switch_guards_see_their_shapes() -> None:
    assert _switch_reads(ast.parse("if s.search.enable_vector_search:\n    pass\n")) == [1]
    assert _switch_reads(ast.parse("x = getattr(s, 'y') or s.enable_vector_search\n")) == [1]
    assert not _switch_reads(ast.parse("s.search.enable_vector_search = False\n"))
    assert not _switch_reads(ast.parse("class S:\n    enable_vector_search: bool = True\n"))
    aliased = (
        "from x import create_embedding_provider as make\nmake(s)\nf.create_embedding_provider(s)\n"
    )
    assert _factory_calls(ast.parse(aliased)) == [2, 3]
    assert not _factory_calls(ast.parse("embedding_provider_for(s)\n"))


def _offenders(find: Any, allowed: set[str]) -> tuple[list[str], set[str]]:
    seen: set[str] = set()
    offenders: list[str] = []
    for root in _SRC_ROOTS:
        for path in sorted(root.rglob("*.py")):
            lines = find(ast.parse(path.read_text(encoding="utf-8")))
            if not lines:
                continue
            rel = path.relative_to(_ROOT).as_posix()
            if rel in allowed:
                seen.add(rel)
                continue
            offenders += [f"{rel}:{line}" for line in lines]
    return offenders, seen


def test_only_the_embedding_factory_reads_the_vector_switch() -> None:
    offenders, seen = _offenders(_switch_reads, {_SWITCH_READER})
    assert not offenders, f"ask embedding_provider_for(settings) instead: {offenders}"
    assert seen == {_SWITCH_READER}


def test_create_embedding_provider_is_called_only_where_allowed() -> None:
    offenders, seen = _offenders(_factory_calls, set(_FACTORY_CALLERS))
    assert not offenders, f"build through embedding_provider_for(settings) instead: {offenders}"
    assert seen == set(_FACTORY_CALLERS), sorted(set(_FACTORY_CALLERS) - seen)


def test_the_shared_provider_is_called_only_where_allowed() -> None:
    """``get_embedding_service`` never asks the switch: an Engine-based path must not reach it."""
    offenders, seen = _offenders(_calls_to("get_embedding_service"), set(_SHARED_PROVIDER_CALLERS))
    assert not offenders, f"take the engine's provider (None = keyword-only) instead: {offenders}"
    assert seen == set(_SHARED_PROVIDER_CALLERS), sorted(set(_SHARED_PROVIDER_CALLERS) - seen)


# Every open of a database for writing takes one step (``prepare_database``)
#
# The Engine ran the migrations alone, and only ``init_database`` seeded: a database a client
# built through the CLI or ``chaoscypher mcp`` had none of the shipped templates, and its first
# edge with the default template failed a foreign key. ``prepare_database`` migrates and then
# gives the database the shipped templates it lacks; a direct call of the migration runner is
# allowed only where the step follows it.

#: The calls of ``run_startup_migrations`` outside ``prepare_database``, and why each is safe.
_MIGRATION_RUNNERS = {
    "packages/core/src/chaoscypher_core/database/engine.py": "prepare_database itself",
    "packages/cli/src/chaoscypher_cli/mcp/command.py": (
        "the maintenance gate before the Engine, whose prepare_database follows"
    ),
}


def test_only_prepare_database_runs_the_startup_migrations() -> None:
    """A path that opens a database for writing calls ``prepare_database``, not the runner."""
    offenders, seen = _offenders(_calls_to("run_startup_migrations"), set(_MIGRATION_RUNNERS))
    assert not offenders, f"call prepare_database (migrate + shipped templates): {offenders}"
    assert seen == set(_MIGRATION_RUNNERS), sorted(set(_MIGRATION_RUNNERS) - seen)


def test_the_graph_defaults_are_shipped_templates() -> None:
    """The default node and edge templates are ones every open gives a database."""
    from chaoscypher_core.settings import GraphSettings
    from chaoscypher_core.templates.default_templates import SHIPPED_TEMPLATE_IDS

    graph = GraphSettings()
    assert graph.default_node_template in SHIPPED_TEMPLATE_IDS
    assert graph.default_edge_template in SHIPPED_TEMPLATE_IDS


# Every engine ChaosCypher builds hides a failed statement's values
#
# SQLAlchemy quotes a statement's parameters in its error text, and so in every log of it: a
# node's label and properties, which an agent writes a memory into. ``hide_parameters=True``
# on each production engine keeps them out; a new engine without it would bring them back.

_ENGINE_BUILDERS = frozenset({"create_engine", "engine_from_config", "create_async_engine"})


def _engines_without_hidden_parameters(tree: ast.AST) -> list[int]:
    """Lines building a SQLAlchemy engine without ``hide_parameters=True``."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _called(node) in _ENGINE_BUILDERS
        and not any(
            kw.arg == "hide_parameters"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
    ]


def test_the_engine_guard_sees_its_shapes() -> None:
    assert _engines_without_hidden_parameters(ast.parse("create_engine(url)\n")) == [1]
    assert _engines_without_hidden_parameters(ast.parse("sa.engine_from_config(c, p)\n")) == [1]
    assert not _engines_without_hidden_parameters(
        ast.parse("create_engine(url, hide_parameters=True)\n")
    )
    assert _engines_without_hidden_parameters(
        ast.parse("create_engine(url, hide_parameters=False)\n")
    ) == [1]


def test_every_production_engine_hides_its_parameters() -> None:
    offenders, _seen = _offenders(_engines_without_hidden_parameters, set())
    assert not offenders, f"pass hide_parameters=True: {offenders}"
