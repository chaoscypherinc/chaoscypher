---
id: ccx-format
title: CCX Format Specification (Draft)
description: Draft specification of the CCX 3.0 package format — container layout, manifest schema, JSON-LD graph members, sources.jsonl row schemas, and the validator's conformance classes.
---

# CCX Format Specification (Draft)

**Spec revision:** 3.0 — draft 1 (2026-08-25) · **Format version:** `ccx_version: "3.0"` · **Status: draft**

CCX (Chaos Cypher eXchange, file extension `.ccx`) is an **open, documented format** for portable knowledge packages: a knowledge graph, its templates, the source documents it was extracted from, and optional embeddings, shapes, and signatures — all in one self-contained, offline-verifiable file.

This page is the format's draft specification. It is versioned with the format (`3.0`) and revised in place while the draft status holds; the revision line above changes whenever the content does. CCX is not a standard — there is one reference implementation, described below, and the format graduates from "documented" to anything stronger only if independent implementations appear.

The reference implementation is the permissively licensed reader/writer **`ccx-format`** (Apache-2.0, [PyPI](https://pypi.org/project/ccx-format/), [source](https://github.com/chaoscypherinc/ccx)):

```bash
pip install ccx-format        # import name: ccx
```

```python
import ccx

pkg = ccx.open_package("my-graph.ccx")
report = pkg.validate()
print(report.ok, report.classes)   # e.g. True ('core', 'sources', 'shapes')
```

It reads and validates packages without any ChaosCypher dependency, and ships a CLI: `ccx inspect`, `ccx validate`, `ccx pack`.

## 1. Container

A `.ccx` file is a **deterministic ZIP archive** with media type `application/vnd.ccx+zip`.

Container rules:

- The **first entry** must be a file named `mimetype`, stored **uncompressed** (`ZIP_STORED`), containing exactly `application/vnd.ccx+zip`. (Same trick as EPUB: the media type is readable from the first bytes of the file.)
- Every other entry is `ZIP_DEFLATED`. The reference writer pins entry timestamps to `1980-01-01 00:00` and file modes to `0644`, so identical inputs produce byte-identical archives.
- Readers enforce hard limits before inflating: at most **100,000 entries**, **512 MiB** per uncompressed entry, **2 GiB** total uncompressed.

Canonical layout:

```
mimetype                              # first entry, stored, application/vnd.ccx+zip
manifest.json                         # package metadata + member inventory (§2)
context.jsonld                        # JSON-LD context (§3)
knowledge.jsonld                      # the default graph (§4)
graphs/<namespace>.<name>.jsonld      # additional named graphs (§4)
sources.jsonl                         # source + chunk records (§5) — declared as an asset
shapes.ttl                            # optional SHACL shapes (§7) — declared as an asset
assets/sha256/<hex>                   # content-addressed assets (full text, embedding sidecars)
signatures/manifest.sig               # optional detached signature (§8)
```

The default graph lives at the archive root as `knowledge.jsonld`. Every other graph lives at `graphs/<namespace>.<name>.jsonld`. `sources.jsonl` and `shapes.ttl` are inventoried in the manifest's `assets` array, not its `graphs` array.

## 2. `manifest.json`

The manifest is a JSON object validated against a published JSON Schema (draft 2020-12, `$id`: `https://w3id.org/ccx/schema/3.0/manifest.schema.json`, bundled with the reader at `ccx/schemas/manifest.schema.json`). Serialization is normative for writers: sorted keys, two-space indent, UTF-8 without ASCII escaping.

**Required fields:** `ccx_version`, `name`, `package_version`, `graphs` (with at least one graph).

| Field | Type | Notes |
|---|---|---|
| `ccx_version` | string | Format version; `"3.0"` for this spec. A different value is a validation *warning*, not an error. |
| `name` | string | Package name. |
| `package_version` | string | The package's own version, chosen by its author. |
| `title`, `description`, `author` | string | Optional descriptive metadata. |
| `license` | string | Optional but recommended — validators warn when absent. |
| `created_at` | string | Optional timestamp (not format-constrained by the schema). |
| `base_iri` | string | Base IRI for locally minted identifiers. |
| `generator` | string | Producing tool; the reference writer defaults to `ccx-format@<version>`. |
| `tags` | array of string | Free-form tags. |
| `derived_from`, `dependencies` | object (string values) | Provenance and dependency pointers. |
| `graphs` | array | Member inventory for JSON-LD graphs — see below. **Min 1 item.** |
| `assets` | array | Member inventory for everything that is not a graph. |
| `embeddings` | array | Embedding descriptors (§6). |
| `signatures` | array | Signature descriptors (§8). |

Unknown extra fields are allowed (`additionalProperties: true`).

**`graphs[]` entries** — required: `namespace`, `name`, `path`, `media_type`, `sha256`, `sha512`. Optional: `role` (the only legal value is `"default"`, and only for the `ccx`/`knowledge` graph) and a per-graph `license`. The `ccx` namespace is reserved for the `knowledge` graph; `/` and `..` are rejected in namespace and name.

**`assets[]` entries** — required: `path`, `media_type`, `sha256`, `sha512`. Optional: `license` and `source_mode`, one of `embedded`, `referenced`, `derived-only`.

**`embeddings[]` entries** — required: `model`, `dimensions` (integer ≥ 1). Optional: `provider`, `included` (boolean), `path` (the sidecar asset), `coverage` (string or object).

**`signatures[]` entries** — required: `path`. Optional: `format`.

### Checksums

Every `sha256`/`sha512` value in the manifest is the **base64 encoding** of the raw digest (not hex). Integrity verification requires *both* digests to match. Hex digests appear in exactly one place: content-addressed asset **paths** (`assets/sha256/<hex-of-sha256>`).

## 3. `context.jsonld`

The package carries its JSON-LD context inline. The canonical context defines the `ccx:` vocabulary (`https://w3id.org/ccx/`) and a small schema.org alignment:

- Types: `ccx:Relationship`, `ccx:Source`, `ccx:Chunk`, `ccx:Citation`, plus `schema:Person` / `schema:Organization`.
- Relationship terms: `subject`, `predicate`, `object` (all `ccx:`).
- Source/chunk terms: `selector`, `sourceMode`, `extractedBy`, `citation`, `confidence`, `extractionMethod`, `embeddingModel`, `dimensions`.

**Remote contexts are forbidden.** A conformant reader rejects any `http(s)://` `@context` reference or remote `@import`, anywhere in any document — top-level, nested, or node-scoped — and loads graphs with network access disabled. A package must be fully interpretable offline; this is a security property, not a style rule.

When graph members are projected into RDF, each named graph gets the IRI `https://w3id.org/ccx/graph/<namespace>/<name>`.

## 4. Graph members (JSON-LD)

Each graph member is a JSON-LD document, typically `{"@graph": [ ... ]}`.

**The default graph** (`ccx`/`knowledge`, at `knowledge.jsonld`) holds the knowledge itself:

- **Node objects:** `{"@id": <iri>, "@type": <type>, "name": <label>, ...properties}`. Producers must not let free-form properties shadow the reserved keys `@id`, `@type`, `@context`, `name`, `source`.
- **Simple relationships** are attached to their subject node as a bare predicate key (`"worksFor": {"@id": ...}`); a repeated predicate becomes a list.
- **Reified relationships** are standalone `ccx:Relationship` resources — `{"@id", "@type": "Relationship", "subject": {"@id"}, "predicate", "object": {"@id"}, ...properties}` — used whenever a relationship carries its own properties or its predicate would collide with a reserved or existing key.

Additional named graphs are producer-defined. ChaosCypher, for example, emits `chaoscypher.templates` (template/editor metadata), `chaoscypher.lenses`, `chaoscypher.statistics`, and `chaoscypher.workflows` under its own namespace; a generic CCX reader can ignore any namespace it does not understand.

Identifiers are IRIs. ChaosCypher mints locally scoped URNs (`urn:ccx:chaoscypher:<kind>/<id>`, kinds `node`/`rel`/`source`/`template`) and preserves foreign IRIs on re-export, which is what makes import an **upsert by IRI** rather than a duplicate-creating copy.

## 5. `sources.jsonl`

One JSON object per line (`application/x-ndjson`, sorted keys, UTF-8). Two record shapes:

**`ccx:Source` record** — one per source document:

| Key | Meaning |
|---|---|
| `@id` | Source IRI. |
| `@type` | `"ccx:Source"`. |
| `sourceMode` | One of `embedded`, `referenced`, `derived-only`. |
| `title` | Optional display title. |
| `keywords` | Optional tags (schema.org-aligned). |
| `extractedBy` | Optional extraction mode of the producing pipeline. |
| `extractionDomain` | Optional producer domain hint. |
| `chunking` | Optional chunking provenance (strategy and size/overlap/boundary parameters) so chunk offsets are reproducible. |
| `text` | Optional pointer to the full text as a content-addressed asset path (`assets/sha256/<hex>`). |

Optional keys are omitted when absent — never emitted as `null`.

**`ccx:Chunk` record** — one per retrieval chunk:

| Key | Meaning |
|---|---|
| `@id` | Chunk IRI; the convention is `<source-iri>#chunk-<index>`. |
| `@type` | `"ccx:Chunk"`. |
| `source` | `{"@id": <source-iri>}`. |
| `selector` | `{"type": "TextPositionSelector", "start": <int>, "end": <int>}` — character offsets into the source's `text` asset. |
| `content` | Inline chunk text — the fallback when there is no full-text asset to point into. |
| `ccx:citation` | Optional list of citation objects: `{"ccx:citation": {"@id": <node-iri>}, "ccx:confidence": <float>, "ccx:extractionMethod": <string>}`. |

A chunk carries `selector` (offsets into retained full text) **or** inline `content`, not both. Chunk `@id`s are shared with the embedding sidecar (§6), so vectors join back to chunks byte-identically.

## 6. Embeddings

Embeddings ship as **Parquet sidecar assets** with two columns: `id` (string — node or chunk IRI) and `vector` (list of float). Each sidecar is described by a manifest `embeddings[]` descriptor: `model`, `dimensions`, optional `provider`, `coverage`, `included`, and `path` pointing at the content-addressed asset. Reading them requires the `embeddings` extra (`pip install "ccx-format[embeddings]"`).

Embeddings are optional and off by default in most producers — a package without them is simply not granted the `embeddings` conformance class (§9).

## 7. Shapes

A package may include `shapes.ttl`: SHACL shapes in Turtle, declaring at least one `sh:NodeShape` or `sh:PropertyShape`. Consumers with the `shapes` extra installed can run SHACL validation of the graph against the shipped shapes (`pkg.shacl_validate()`).

## 8. Signatures

Packages can carry detached signatures under `signatures/` (canonically `signatures/manifest.sig`), declared in the manifest with a `format`. Implemented formats: **`ed25519`** (requires the `signed` extra) and **`sigstore`** (requires `signed-sigstore`). Verification is offline and fail-closed. Post-quantum format names (`ml-dsa-44/65/87`, `slh-dsa-128s/256s`) are reserved: recognized but unimplemented, and verification of them reports an error rather than silently passing.

## 9. Validation and conformance classes

`ccx validate <file>` (or `pkg.validate()`) produces a report: `ok`, `errors`, `warnings`, and the granted `classes`.

**Errors** (package invalid): missing or wrong `mimetype` first entry; missing or non-JSON `context.jsonld`; a remote `@context` anywhere; no graphs declared; a declared file missing from the archive; a checksum mismatch; a graph member that is not valid JSON. A manifest that fails the JSON Schema does not even reach the report — `open_package` raises.

**Warnings** (package still valid): a `ccx_version` other than `"3.0"`; no default graph; no declared license; and any issue found in a higher conformance class.

Conformance is layered. `core` means the container, manifest, context, and graphs check out. The higher classes are **independent capabilities on top of core, not a stack** — and *absence is never an error*: a package is only judged on the classes it claims by shipping the relevant artifacts. A claimed-but-malformed class does not fail validation; it surfaces warnings and the class is simply not granted.

| Class | Granted when |
|---|---|
| `core` | Validation produced no errors. |
| `sources` | `sources.jsonl` is present, every line parses, every asset `source_mode` is legal, and every `selector` is in range against its referenced text asset. |
| `shapes` | `shapes.ttl` is present, parses as Turtle, and declares at least one SHACL shape. |
| `embeddings` | The manifest declares embeddings and every `included` descriptor's sidecar asset is declared and present. |
| `signed` | The manifest declares signatures and at least one verifies offline. |

A typical full ChaosCypher export validates as `core`, `sources`, `shapes`.

## 10. Producer notes (ChaosCypher)

For consumers of ChaosCypher-produced packages specifically:

- The default graph is always present (empty for a sources-only export). `chaoscypher.statistics` is always present; `chaoscypher.lenses` only when non-empty.
- `chaoscypher.workflows` currently carries **workflow trigger rows only** — not full workflow definitions — and ChaosCypher's own importer skips that member with a warning. Do not expect workflows to round-trip through CCX today.
- Source full text is retained as content-addressed assets, `sourceMode` is `derived-only`, and chunk records use offset selectors into that text.
- Embedding descriptors are emitted only when an export explicitly includes embeddings; signatures are not emitted at all today, so the `signed` class is currently unreachable from ChaosCypher-produced packages.

## 11. Versioning and compatibility

- `ccx_version` identifies the format generation. Readers targeting 3.0 should accept any `"3.0"` package; an unknown version is a warning so tooling can still inspect the file.
- `package_version` is entirely the package author's; the manifest also offers `derived_from` and `dependencies` for provenance chains.
- This document is the draft 3.0 spec and is revised in place; the revision marker at the top identifies the exact text a package or tool was written against.
