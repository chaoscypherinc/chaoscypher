---
slug: ccx-open-format
title: "The CCX Open Format: A Portable Spec for Knowledge Graphs"
authors: [denis]
tags: [opensource, python, graphrag, selfhosted]
date: 2026-09-01
draft: true
description: CCX is now a documented, openly specified format for portable knowledge packages -- with a draft spec page and an Apache-2.0 reader you can pip install and use without Chaos Cypher.
---

A knowledge graph you cannot take with you is not really yours. You can love the tool that built it, and still want the guarantee that the graph outlives the tool -- readable in five years, on a machine that has never heard of the software that produced it.

That guarantee only exists if the file format is written down.

<!-- truncate -->

So we wrote it down. CCX -- Chaos Cypher eXchange, the `.ccx` file Chaos Cypher already exports -- now has a **[published draft specification](/docs/reference/ccx-format)** and a **standalone reader library** you can install and use with no Chaos Cypher anywhere in the picture:

```bash
pip install ccx-format        # import name: ccx
```

The reader is Apache-2.0 and lives in its own repository ([chaoscypherinc/ccx](https://github.com/chaoscypherinc/ccx), [PyPI](https://pypi.org/project/ccx-format/)) -- deliberately more permissive than Chaos Cypher itself, because a format nobody can implement against is a format nobody adopts.

## What This Changes For You

If you use Chaos Cypher, nothing you do changes today. Export still works the way it did; the file you get is the same file.

What changed is what that file *means*. Before, `.ccx` was an implementation detail -- whatever our exporter happened to write, readable by whatever our importer happened to accept. Now it is a documented artifact with a schema, a validator, and a second piece of software that reads it. Your export is no longer a bet on us staying in business.

**In plain English:** your `.ccx` file is now a document with a spec behind it, not just a save file.

## What's Actually In a Package

A `.ccx` file is a ZIP archive with a fixed layout:

```
mimetype                              # first entry, uncompressed
manifest.json                         # metadata + member inventory
context.jsonld                        # the JSON-LD context, carried inline
knowledge.jsonld                      # the default graph
graphs/<namespace>.<name>.jsonld      # additional named graphs
sources.jsonl                         # source and chunk records
shapes.ttl                            # optional SHACL shapes
assets/sha256/<hex>                   # content-addressed assets
signatures/manifest.sig               # optional detached signature
```

Four decisions in there are worth pulling out, because they are the ones that make the file trustworthy rather than merely readable:

**The archive is deterministic.** Entry timestamps are pinned to `1980-01-01` and file modes to `0644`, so the same inputs produce a byte-identical archive. You can checksum an export and mean something by it.

**The media type is in the first bytes.** The first entry is always an uncompressed file named `mimetype` containing `application/vnd.ccx+zip` -- the same trick EPUB uses. A file identifies itself without being unpacked.

**Everything is checksummed twice.** Every graph and asset in the manifest carries both a SHA-256 and a SHA-512 digest, and integrity verification requires *both* to match. Readers also enforce hard limits before inflating anything -- at most 100,000 entries, 512 MiB per entry, 2 GiB total -- so a malicious package cannot zip-bomb a consumer.

**Remote contexts are forbidden.** A conformant reader rejects any `http(s)://` JSON-LD `@context` reference anywhere in the package, and loads graphs with network access disabled. This is the rule we are most opinionated about: a package must be fully interpretable offline, forever, with no live server anywhere in the chain. It is a security property, not a style preference.

The knowledge itself lives in `knowledge.jsonld` as JSON-LD nodes, with relationships either attached directly to their subject or reified as standalone `Relationship` resources when they carry properties of their own. Sources and retrieval chunks live in `sources.jsonl`, one JSON object per line, with chunks pointing back into retained full text by character offset -- which is what lets a citation survive the trip.

**In plain English:** it's a ZIP with a strict layout, checksums on everything, and a hard rule that it never needs the internet to be read.

## Reading a Package Without Chaos Cypher

This is the part that makes "open" mean something. Install the reader and open any `.ccx` file:

```python
import ccx

pkg = ccx.open_package("my-graph.ccx")
report = pkg.validate()
print(report.ok, report.classes)   # e.g. True ('core', 'sources', 'shapes')
```

There is a CLI too -- `ccx inspect`, `ccx validate`, and `ccx pack` -- so you can check a package without writing any code at all.

<!-- screenshot: terminal output of `ccx validate` on a real exported package, showing ok/errors/warnings and the granted conformance classes -->

Note what `validate()` hands back: not a boolean, but a report with errors, warnings, and a set of **conformance classes**. Those classes are the format's honesty mechanism.

| Class | Granted when |
|---|---|
| `core` | The container, manifest, context, and graphs all check out. |
| `sources` | `sources.jsonl` parses and every chunk offset is in range against its text asset. |
| `shapes` | `shapes.ttl` is present, parses as Turtle, and declares at least one SHACL shape. |
| `embeddings` | The manifest declares embeddings and every included sidecar is present. |
| `signed` | The manifest declares signatures and at least one verifies offline. |

The classes above `core` are **independent capabilities, not a stack**, and absence is never an error -- a package is judged only on what it claims by actually shipping the relevant artifacts. A package with no embeddings is not a failing package; it simply is not granted the `embeddings` class. A package that claims embeddings and ships them broken does not fail validation either: it collects warnings and the class is withheld. So a consumer can ask "does this package have verifiable sources?" and get a straight answer, instead of parsing it and finding out.

A typical full Chaos Cypher export validates as `core`, `sources`, `shapes`.

**In plain English:** any Python program can open a `.ccx` file, and the validator tells it exactly which parts of the package it can trust.

## Producing One From Chaos Cypher

Nothing new here, but for completeness -- the graphical path is **Settings → General → Import & Export**: pick which components to include, click Export, and the browser downloads the `.ccx`.

<!-- screenshot: Settings > General > Import & Export panel with the component toggles and the Export button -->

For a single document's worth of knowledge rather than the whole graph, use **Export Source** in that source's action menu on the Sources page.

CLI users get the same thing with more control:

```bash
# Whole graph, default components
chaoscypher graph package export --output my-research.ccx --title "My Research"

# Templates and lenses only, no entities -- a shareable schema
chaoscypher graph package export --no-knowledge --no-workflows -o schema-only.ccx

# Include embedding vectors (off by default -- only useful for same-model migration)
chaoscypher graph package export --embeddings -o full.ccx

# Load one back
chaoscypher graph package load my-research.ccx
```

Imports are an **upsert by IRI**, not a copy: Chaos Cypher mints locally scoped identifiers and preserves foreign ones on re-export, so loading a package twice does not duplicate its contents.

**In plain English:** two clicks in Settings, or one command in the terminal, and you have a package.

## The Honest Limits

This is draft 1 of a 3.0 spec, and there is a difference between "documented" and "finished." The things we would want to know if we were reading someone else's format announcement:

- **We are not calling it a standard.** There is one reference implementation, written by us. A format with a single implementation is a documented format, and that is all we are claiming. That word is available to us when a second, independent implementation exists -- not before.
- **The spec is revised in place** while draft status holds. The revision marker at the top of the spec page identifies the exact text a tool was written against; check it rather than assuming.
- **Workflows do not round-trip today.** The `chaoscypher.workflows` graph member currently carries workflow *trigger* rows only, not full workflow definitions, and our own importer skips that member with a warning. It is in the package and it is not yet useful; we would rather say so than let you discover it.
- **Signatures are specified but not emitted.** The format defines detached `ed25519` and `sigstore` signatures with offline, fail-closed verification, and reserves post-quantum format names. Chaos Cypher does not produce signed packages yet, so the `signed` class is currently unreachable from our exports.
- **Embeddings are opt-in and model-bound.** They ship as Parquet sidecars and are off by default, because a vector is only useful to someone running the same embedding model.

**In plain English:** the file format is written down and stable enough to build against; the parts that are not finished are named above rather than left for you to trip over.

## What We Want From This

The interesting question is not whether Chaos Cypher can read its own files. It is whether anything else can.

So the specific thing that would make this real: **one independent implementation.** A loader that pulls a `.ccx` into LlamaIndex or LangChain. An exporter that writes one out of Obsidian. A validator in another language. Any of those, written by someone who is not us, is the difference between a documented format and a shared one.

If you are building in this space, the [spec page](/docs/reference/ccx-format) is the whole contract and the [reader](https://github.com/chaoscypherinc/ccx) is the reference. Both are open, and issues and questions on either are genuinely welcome -- draft 1 is exactly the stage where the spec should be argued with.

If you just want your own graph to be portable: it already is. That was the point.

---

*Running Chaos Cypher fully locally with Ollama? See the [local-first setup guide](/blog/local-ai-knowledge-graph) -- packages work identically there, and never touch a network.*
