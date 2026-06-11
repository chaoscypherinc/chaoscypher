---
id: contributing
title: Contributing
description: How to contribute to Chaos Cypher — filing issues, starting discussions, and the CLA requirement for code contributions via the upstream development workspace.
---

# Contributing

[`CONTRIBUTING.md`](https://github.com/chaoscypherinc/chaoscypher/blob/main/CONTRIBUTING.md) at the repository root is the single source of truth for the contribution workflow. This page summarizes it.

## Where to Contribute

- **Bug reports:** [Open an issue](https://github.com/chaoscypherinc/chaoscypher/issues)
- **Questions and ideas:** [Start a discussion](https://github.com/chaoscypherinc/chaoscypher/discussions)
- **Security reports:** Follow [SECURITY.md](https://github.com/chaoscypherinc/chaoscypher/blob/main/SECURITY.md) — do not open a public issue
- **Code and documentation changes:** Open a pull request (see below)

## Pull Requests

Pull requests are welcome. The workflow, documented in full in [CONTRIBUTING.md](https://github.com/chaoscypherinc/chaoscypher/blob/main/CONTRIBUTING.md):

1. Branch from `main`; keep branches short-lived and rebase (don't merge) to update against `main`.
2. Give the PR a Conventional Commits title (`type(scope): subject`) and link any related issue.
3. Work through the pre-merge checklist: lint, typecheck, `make docker-test`, ≥90% coverage on changed lines, an Alembic migration if SQLModel metadata changed, SPDX headers on new files, and doc updates if a rule or behavior shifted.
4. Pass CI.

For larger changes, open an issue or discussion first describing the problem and the change you recommend — it avoids wasted work if the direction needs adjusting.

Note: the public repository is published from an upstream development workspace, so maintainers may apply your accepted change there and merge it indirectly rather than via the GitHub merge button.

## CLA

Code contributions require a one-time Contributor License Agreement. When you open your first pull request, a maintainer will confirm whether your CLA is already on file and, if not, ask you to complete it before merge. See [CONTRIBUTING.md](https://github.com/chaoscypherinc/chaoscypher/blob/main/CONTRIBUTING.md) and [CLA.md](https://github.com/chaoscypherinc/chaoscypher/blob/main/CLA.md) in the repository root.

## Documentation Feedback

For documentation issues, include the page URL and the section heading. Small corrections are welcome as issues; larger documentation proposals are easier to handle as discussions first.
