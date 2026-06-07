<!-- Copyright (C) 2024-2026 Chaos Cypher, Inc. -->
<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Plugin Trust Boundary

Chaos Cypher's plugin system executes user-provided Python files from
`{data_dir}/plugins/` with **the full privileges of the server process**.
This document describes the trust model and how to opt out.

## What gets executed

At startup, each registry scans its corresponding subdirectory under
`{settings.paths.data_dir}/plugins/`:

| Registry                 | Scanned subdirectory             | File pattern      |
|--------------------------|----------------------------------|-------------------|
| `LoaderRegistry`         | `plugins/loaders/`               | `*_loader.py`     |
| `ToolRegistry`           | `plugins/tools/`                 | `*_plugin.py`     |
| `CleanerRegistry`        | `plugins/cleaners/`              | `*.py`            |
| `ArchiveHandlerRegistry` | `plugins/archive_handlers/`      | `*.py`            |
| `DomainRegistry`         | `plugins/domains/`               | `*.jsonld` (data) |

For Python file patterns, the registry calls
`importlib.util.spec_from_file_location(...)` followed by
`spec.loader.exec_module(module)`. Any top-level code in the file runs
immediately, before any class is instantiated.

## Threat model

- Anyone who can write to `{data_dir}/plugins/` can execute arbitrary
  code as the Chaos Cypher service user.
- This is **intended** for self-hosted single-user deployments, which is
  the default Chaos Cypher distribution shape.
- It is **not safe** for multi-tenant hosting where a lower-trust user
  has filesystem access to `{data_dir}`.

## Mitigations in the codebase

1. **Startup audit log.** Every user plugin file loaded is logged at
   `WARNING` level with its absolute path and a SHA-256 digest of the
   file contents. Grep your logs for `user_plugin_loaded`.
2. **Kill switch env var.** Set `CHAOSCYPHER_ALLOW_USER_PLUGINS=0` to
   disable **all** user plugin discovery. Built-in plugins and
   entry-point plugins installed via pip still load. Default is `1`
   (backwards compatible).
3. **data_dir sanity warning.** If `settings.paths.data_dir` resolves
   to a system-sensitive path (`/etc`, `/root`, etc.), a `WARNING` is
   logged at startup. This is advisory only — it does not block.

## Entry-point plugins

In addition to user-drop files, several registries auto-discover plugins
via Python entry points declared in installed packages' `pyproject.toml`
(`[project.entry-points."<group>"]`). The known groups:

| Entry-point group              | Loader                                                               | Loaded class must subclass |
|--------------------------------|----------------------------------------------------------------------|----------------------------|
| `chaoscypher.providers`        | `core/adapters/llm/providers/__init__.py`                            | `BaseLLMProvider`          |
| `chaoscypher.cleaners`         | `core/services/sources/normalizer/cleaners/registry.py`              | (cleaner contract)         |
| `chaoscypher.archive_handlers` | `core/services/sources/loaders/archive/handlers/registry.py`         | (archive-handler contract) |

### Trust posture

- Entry-point plugins are loaded by `importlib.metadata.entry_points(group=...)`
  and resolved via `ep.load()`, which **imports the target module**. Any
  top-level code in that module runs in the server process with the same
  privileges as ChaosCypher itself.
- The runtime gate is `isinstance(cls, type) and issubclass(cls, BaseLLMProvider)`
  (and a `_METADATA` check for providers). These are **type-safety guards
  — not security guards.** A malicious entry-point package can satisfy
  both and still execute arbitrary code at import time.
- There is **no signature, checksum, or publisher verification.** Trust
  derives entirely from "what's installed in this virtualenv." This
  matches the standard Python ecosystem expectation (cf. pip-installed
  packages running `setup.py`).

### Operator guidance

- Only install entry-point plugin packages from sources you trust (your
  own builds, pinned versions in `pyproject.toml`, an internal index).
- Pin entry-point plugin dependencies in `uv.lock` and re-run
  `make license-check` after upgrading them.
- The kill switch for **user-drop** plugins
  (`CHAOSCYPHER_ALLOW_USER_PLUGINS=0`) does **not** affect entry-point
  plugins — those are scoped to "what's in the venv" and are uninstalled
  via `pip uninstall <package>`.

## Operator checklist for shared deployments

- Run the Chaos Cypher service as a dedicated unprivileged user.
- Set `CHAOSCYPHER_ALLOW_USER_PLUGINS=0` in the service environment.
- Restrict the virtualenv contents to vetted packages only; treat
  every entry-point plugin as code you ship.
