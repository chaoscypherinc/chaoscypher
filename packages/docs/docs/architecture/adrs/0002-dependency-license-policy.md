---
title: "ADR-0002: Dependency License Policy"
description: Decision record establishing that all Chaos Cypher direct dependencies must use permissive licenses (MIT, BSD, Apache-2.0) to stay compatible with the enterprise edition.
---

# 0002. Dependency License Policy

Date: 2026-02-06

## Status

Accepted

## Context

Chaos Cypher is licensed under AGPL-3.0. While the project itself uses a copyleft license, we maintain a policy of using **permissive licenses for all direct dependencies** to keep the dependency tree clean and avoid license conflicts with the proprietary enterprise extension package.

A dependency audit revealed that **PyMuPDF/pymupdf4llm is licensed under AGPL-3.0**, which while compatible with Chaos Cypher's AGPL-3.0 license, would create issues for the proprietary enterprise edition. All other direct dependencies use permissive licenses (MIT, BSD, Apache-2.0, ISC, MPL-2.0, EPL-2.0).

### Python Dependencies (Core)

| Package | License | Compatible |
|---------|---------|------------|
| sqlmodel | MIT | Yes |
| structlog | MIT/Apache-2.0 | Yes |
| pydantic | MIT | Yes |
| platformdirs | MIT | Yes |
| sqlite-vec | MIT | Yes |
| anthropic | MIT | Yes |
| openai | Apache-2.0 | Yes |
| **pymupdf4llm** | **AGPL-3.0** | **No** |
| unstructured | Apache-2.0 | Yes |
| pyspellchecker | MIT | Yes |
| simhash | MIT | Yes |
| ftfy | Apache-2.0 | Yes |
| trafilatura | Apache-2.0 | Yes |
| python-dotenv | BSD-3 | Yes |
| tqdm | MIT/MPL-2.0 | Yes |
| httpx | BSD-3 | Yes |
| jsonschema | MIT | Yes |
| langchain | MIT | Yes |
| langchain-core | MIT | Yes |
| langchain-text-splitters | MIT | Yes |
| langgraph | MIT | Yes |
| langchain-ollama | MIT | Yes |
| langchain-openai | MIT | Yes |
| langchain-anthropic | MIT | Yes |
| langchain-google-genai | MIT | Yes |
| pypdf | BSD-3 | Yes |

### Python Dependencies (Cortex/Neuron)

| Package | License | Compatible |
|---------|---------|------------|
| fastapi | MIT | Yes |
| uvicorn | BSD-3 | Yes |
| pydantic-settings | MIT | Yes |
| bcrypt | Apache-2.0 | Yes |
| PyJWT | MIT | Yes |
| PyYAML | MIT | Yes |
| dynaconf | MIT | Yes |
| valkey (Python client) | MIT | Yes |
| ~~arq~~ | ~~MIT~~ | ~~Removed~~ |
| ~~requests~~ | ~~Apache-2.0~~ | ~~Removed~~ |
| ~~beautifulsoup4~~ | ~~MIT~~ | ~~Removed~~ |
| ~~lxml~~ | ~~BSD-3~~ | ~~Removed~~ |

> **Note:** The Redis *server* (redis:7.4+) changed to RSALv2/SSPLv1. We use Valkey (BSD-3-Clause) as our Redis-compatible server. See ADR-0004.

### Node.js Dependencies (Interface)

| Package | License | Compatible |
|---------|---------|------------|
| react | MIT | Yes |
| typescript | Apache-2.0 | Yes |
| vite | MIT | Yes |
| @mui/material | MIT | Yes |
| @tanstack/react-query | MIT | Yes |
| axios | MIT | Yes |
| react-router-dom | MIT | Yes |
| zustand | MIT | Yes |

## Decision

1. **Require permissive licenses only** for all direct dependencies (MIT, BSD, Apache-2.0, ISC, MPL-2.0).
2. **Replace PyMuPDF/pymupdf4llm (AGPL-3.0)** with pypdf (BSD-3) for PDF text extraction (see ADR-0003).
3. **Audit new dependencies** before adding them to ensure license compatibility.

## Rationale

- While Chaos Cypher itself is AGPL-3.0, keeping dependencies permissive avoids license conflicts with the proprietary enterprise extension
- AGPL-3.0 dependencies would require the enterprise edition to also be AGPL-3.0, preventing proprietary distribution
- pypdf provides equivalent PDF text extraction under BSD-3, a permissive license
- All other dependencies already use permissive licenses

### Alternatives Considered

| Option | Pros | Cons |
|--------|------|------|
| Keep PyMuPDF | Better markdown output, faster | AGPL contaminates entire codebase |
| pdfplumber | Good table extraction | GPL-3.0 (same problem) |
| **pypdf (chosen)** | BSD-3, mature, well-maintained | Plain text only (no markdown structure) |
| pdfminer.six | MIT, detailed layout | Slower, more complex API |

## Consequences

### Positive

- **License-clean codebase** - All dependencies are permissively licensed
- **Commercial viability** - No AGPL obligations for network deployment
- **Clear policy** - Future dependency additions must pass license check

### Negative

- **PDF quality reduction** - pypdf extracts plain text, not structured markdown
- **Ongoing vigilance** - Must check licenses when adding new dependencies

### Neutral

- **Existing functionality preserved** - PDF extraction still works, just produces plain text instead of markdown
- **Plugin architecture unchanged** - Custom loaders can still use any library the user chooses

> **Note:** Alongside this policy, Alembic was temporarily dropped as a dependency in favour of a reflective auto-migrator that ran `ALTER TABLE` at startup (the original 2026-04-18 addendum describing it is not part of this public copy). That approach was superseded in part on 2026-04-20 — see [ADR-0006 — Re-adopt Alembic](./0006-re-adopt-alembic.md).

## References

- [AGPL-3.0 License Text](https://www.gnu.org/licenses/agpl-3.0.html)
- [PyMuPDF License](https://github.com/pymupdf/PyMuPDF/blob/main/COPYING)
- [pypdf License](https://github.com/py-pdf/pypdf/blob/main/LICENSE)
- ADR-0003: Replace PyMuPDF with pypdf
