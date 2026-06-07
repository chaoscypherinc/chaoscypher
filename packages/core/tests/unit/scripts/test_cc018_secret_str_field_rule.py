# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Self-tests for CC018 — secret-carrying Pydantic fields must be ``SecretStr``.

The rule lives in ``check_plain_str_secret_fields`` inside
``scripts/lint_claude_rules.py``.

Pins:
- ``password: str`` on a Pydantic subclass triggers CC018.
- ``password: SecretStr`` is the canonical form — silent.
- Non-secret string fields are silent.
- ``# noqa: CC018`` opts out.
- Classes that don't inherit a Pydantic base are out of scope.
- Test files are out of scope.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


def _load_linter():
    """Import scripts/lint_claude_rules.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "lint_claude_rules.py"
    spec = importlib.util.spec_from_file_location("lint_claude_rules", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LINTER = _load_linter()


_BAD_PLAIN_STR_PASSWORD = """\
from pydantic import BaseModel, Field


class Credentials(BaseModel):
    password: str = Field(...)
"""

_BAD_PLAIN_STR_API_KEY = """\
from pydantic import BaseModel, Field


class Credentials(BaseModel):
    api_key: str | None = None
"""

_OK_SECRET_STR_PASSWORD = """\
from pydantic import BaseModel, Field, SecretStr


class Credentials(BaseModel):
    password: SecretStr = Field(...)
"""

_OK_NON_SECRET_FIELD = """\
from pydantic import BaseModel, Field


class Credentials(BaseModel):
    username: str = Field(...)
"""

_OK_NOQA_OPTOUT = """\
from pydantic import BaseModel, Field


class Credentials(BaseModel):
    password: str = Field(...)  # noqa: CC018 - legacy round-trip JSON
"""

_OK_NON_PYDANTIC_CLASS = """\
class Credentials:
    password: str = ""
"""


def _check(source: str, file_path: Path) -> list:
    tree = ast.parse(source)
    return _LINTER.check_plain_str_secret_fields(file_path, tree)


def test_cc018_flags_plain_str_password(tmp_path: Path) -> None:
    """``password: str`` on a BaseModel subclass fires CC018."""
    target = tmp_path / "packages" / "core" / "src" / "models.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_BAD_PLAIN_STR_PASSWORD)
    violations = _check(_BAD_PLAIN_STR_PASSWORD, target)
    assert len(violations) == 1, f"Expected 1 CC018 violation; got {violations}"
    assert violations[0].rule == "CC018"
    assert "password" in violations[0].message
    assert "SecretStr" in violations[0].message


def test_cc018_flags_plain_str_api_key_optional(tmp_path: Path) -> None:
    """``api_key: str | None`` is also a secret-carrying plain str."""
    target = tmp_path / "packages" / "core" / "src" / "models.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_BAD_PLAIN_STR_API_KEY)
    violations = _check(_BAD_PLAIN_STR_API_KEY, target)
    assert len(violations) == 1, f"Expected 1 CC018 violation; got {violations}"
    assert "api_key" in violations[0].message


def test_cc018_silent_on_secret_str(tmp_path: Path) -> None:
    """``SecretStr`` is the canonical form — no violation."""
    target = tmp_path / "packages" / "core" / "src" / "models.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_OK_SECRET_STR_PASSWORD)
    violations = _check(_OK_SECRET_STR_PASSWORD, target)
    assert violations == [], f"SecretStr should not fire CC018; got {violations}"


def test_cc018_silent_on_non_secret_field(tmp_path: Path) -> None:
    """``username: str`` is not a secret field — out of scope."""
    target = tmp_path / "packages" / "core" / "src" / "models.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_OK_NON_SECRET_FIELD)
    violations = _check(_OK_NON_SECRET_FIELD, target)
    assert violations == [], f"Non-secret field should not fire CC018; got {violations}"


def test_cc018_respects_noqa_optout(tmp_path: Path) -> None:
    """``# noqa: CC018`` on the field's line suppresses the rule."""
    target = tmp_path / "packages" / "core" / "src" / "models.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_OK_NOQA_OPTOUT)
    violations = _check(_OK_NOQA_OPTOUT, target)
    assert violations == [], f"noqa opt-out should suppress CC018; got {violations}"


def test_cc018_silent_on_non_pydantic_class(tmp_path: Path) -> None:
    """Plain Python classes (no Pydantic base) are out of scope."""
    target = tmp_path / "packages" / "core" / "src" / "models.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_OK_NON_PYDANTIC_CLASS)
    violations = _check(_OK_NON_PYDANTIC_CLASS, target)
    assert violations == [], f"Plain class should be out of scope; got {violations}"


def test_cc018_skips_test_files(tmp_path: Path) -> None:
    """Files under a ``tests/`` directory are skipped."""
    target = tmp_path / "packages" / "core" / "tests" / "unit" / "test_thing.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_BAD_PLAIN_STR_PASSWORD)
    violations = _check(_BAD_PLAIN_STR_PASSWORD, target)
    assert violations == [], f"Test files must be out of CC018 scope; got {violations}"
