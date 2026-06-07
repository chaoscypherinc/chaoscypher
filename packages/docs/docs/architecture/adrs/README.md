---
title: Architecture Decision Records
description: Index of Architecture Decision Records (ADRs) documenting significant design choices made in Chaos Cypher.
---

# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for the Chaos Cypher platform.

## What is an ADR?

An ADR documents a significant architectural decision, including:
- The context and problem statement
- The decision that was made
- The rationale and alternatives considered
- The consequences (positive and negative)

## Format

ADRs are numbered sequentially and follow this naming convention:
```
NNNN-title-in-kebab-case.md
```

For example:
- `0001-remove-discovery-and-lenses-features.md`
- `0002-dependency-license-policy.md`
- `0003-replace-pymupdf-with-pypdf.md`
- `0004-redis-to-valkey-migration.md`
- `0005-ollama-instances-only.md`

## ADR Template

```markdown
# [Number]. [Title]

Date: YYYY-MM-DD

## Status

[Proposed | Accepted | Deprecated | Superseded by ADR-XXXX]

## Context

What is the issue that we're seeing that is motivating this decision or change?

## Decision

What is the change that we're proposing and/or doing?

## Rationale

Why did we choose this option? What alternatives did we consider?

## Consequences

### Positive
- What becomes easier?
- What benefits do we gain?

### Negative
- What becomes harder?
- What trade-offs are we making?

### Neutral
- What stays the same?
```

## Current ADRs

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [0001](0001-remove-discovery-and-lenses-features.md) | Remove Discovery and Lenses Features | Accepted | 2026-02-05 |
| [0002](0002-dependency-license-policy.md) | Dependency License Policy | Accepted | 2026-02-06 |
| [0003](0003-replace-pymupdf-with-pypdf.md) | Replace PyMuPDF with pypdf | Accepted | 2026-02-06 |
| [0004](0004-redis-to-valkey-migration.md) | Migrate from Redis to Valkey | Accepted | 2026-03-15 |
| [0005](0005-ollama-instances-only.md) | Ollama configuration via instances list only | Accepted | 2026-04-08 |
| [0006](0006-re-adopt-alembic.md) | Re-adopt Alembic for Schema Migrations | Accepted | 2026-04-20 |

## When to Write an ADR

Create an ADR when making decisions about:
- System architecture patterns
- Technology choices (languages, frameworks, databases)
- API design principles
- Data models and schemas
- Development workflows
- Deployment strategies
- Security approaches

## Process

1. Copy the template above
2. Fill in the sections
3. Propose the ADR (Status: Proposed)
4. Discuss with team
5. Update and mark as Accepted
6. Commit to git

## References

- [Michael Nygard's ADR template](https://github.com/joelparkerhenderson/architecture-decision-record)
- [ADR GitHub organization](https://adr.github.io/)
