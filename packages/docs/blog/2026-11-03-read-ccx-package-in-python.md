---
slug: read-ccx-package-in-python
title: "Read a .ccx Knowledge Package in Python with Nothing Installed but ccx-format"
authors: [denis]
tags: [python, opensource, selfhosted, graphrag]
date: 2026-11-03
draft: true
description: A short, runnable tutorial — open an exported .ccx package with the Apache-2.0 ccx-format reader, list its entities and relationships, print the source passages that cite them, and load the triples into networkx or a DataFrame. No Chaos Cypher required.
---

An export format earns the word "open" when a program that has never heard of the tool that wrote the file can still read it. For `.ccx`, that is now a short Python script.

This post is that script. You open a knowledge package, list what is in it, print the exact source passages behind each entity, and hand the graph to networkx or pandas. Chaos Cypher doesn't need to be installed or running. You need one library, and the script never touches the network.

<!-- truncate -->

## What you'll have at the end

- A `.ccx` file opened and validated in plain Python, with the validator telling you which parts of it you can rely on
- Every entity and relationship in the package, by name
- For each entity, **the sentence in the original document it was extracted from**, read back out of the package itself
- The whole graph as a `networkx.MultiDiGraph` and as a pandas DataFrame, ready for whatever you already do with graphs and tables

The [open-format post](/blog/ccx-open-format) covered *what* is in a `.ccx` file and why the format is written down. This one is the *do it* version: code you can paste.

**In plain English:** a `.ccx` file is your knowledge graph plus the documents it came from, and any Python program can read it.

## Step 0 — get a package

You need a `.ccx` file. If you already use Chaos Cypher, export one. In the app, open **Settings → General → Import & Export** and click **Export**. Keep **sources** included, because they carry the citations this tutorial reads.

![Settings → General with the Import & Export row and its Export and Import buttons](/img/screenshots/settings-general.png)

From the terminal, the same export is one command:

```bash
chaoscypher graph package export --output my-graph.ccx --title "My Research"
```

If you don't run Chaos Cypher yet and want a package of your own, start the published image, add a few documents, and export:

```bash
docker run -d --name chaoscypher \
  -p 80:80 \
  -p 443:443 \
  -v chaoscypher-data:/data \
  --add-host=host.docker.internal:host-gateway \
  ghcr.io/chaoscypherinc/chaoscypher:latest
```

You can also skip the app entirely. The last section of this post *writes* a small `.ccx` in about fifteen lines, and every snippet here runs against that file too.

## Step 1 — install the reader and open the file

```bash
pip install ccx-format        # import name: ccx
```

`ccx-format` is the Apache-2.0 reference reader and writer ([PyPI](https://pypi.org/project/ccx-format/), [source](https://github.com/chaoscypherinc/ccx)). Its only dependencies are `rdflib` and `jsonschema`. It does not import anything from Chaos Cypher.

```python
import ccx

pkg = ccx.open_package("my-graph.ccx")
report = pkg.validate()
print(report.ok, report.classes)
print(pkg.manifest.name, pkg.manifest.package_version, pkg.manifest.license)
```

On a full Chaos Cypher export that prints something like:

```
True ('core', 'sources', 'shapes')
chaoscypher/default 1.0.0 CC-BY-SA-4.0
```

Look at `report.classes` before you trust anything else. `core` means the container, manifest, checksums and graphs all check out. `sources` means every chunk's character offsets land inside the source text it points to, so the citations in Step 4 are real ranges in a real document. A package without `sources` is still valid. It just has no passages for you to cite.

There is a CLI as well, if you'd rather check a file before writing code:

```bash
ccx inspect my-graph.ccx
ccx validate my-graph.ccx
```

```
name:    chaoscypher/default
version: 1.0.0 (ccx 3.0)
license: CC-BY-SA-4.0
graphs:  4
  - ccx.knowledge [default]  (knowledge.jsonld)
  - chaoscypher.templates  (graphs/chaoscypher.templates.jsonld)
  - chaoscypher.lenses  (graphs/chaoscypher.lenses.jsonld)
  - chaoscypher.statistics  (graphs/chaoscypher.statistics.jsonld)
assets:  3
VALID (core)
```

The CLI's last line reports the base level only. Call `validate()` from Python to get the full list of classes.

**In plain English:** one `pip install`, one function call, and the file tells you whether it is intact and whether its citations hold up.

## Step 2 — list the entities

The knowledge lives in the package's **default graph**, a JSON-LD document whose `@graph` array holds one object per entity:

```python
kg = next(g for g in pkg.graph_documents() if g.role == "default")
members = kg.doc["@graph"]

entities = {m["@id"]: m for m in members if m.get("@type") != "ccx:Relationship"}
for iri, node in entities.items():
    print(f'{node["@type"]:<14} {node["name"]}')
```

On a small example package, built from two sentences about the Analytical Engine, the outputs from here on look like this:

```
Person         Ada Lovelace
Person         Charles Babbage
Machine        Analytical Engine
Place          London
```

Each entity has an `@id` (an IRI, such as `urn:ccx:chaoscypher:node/…`), an `@type` (the most specific type extraction assigned), a `name`, and any properties it carries. The other graphs (`chaoscypher.templates`, `chaoscypher.statistics` and so on) belong to Chaos Cypher, under its own namespace. A generic reader can ignore them, and this one does.

**In plain English:** every entity is a small JSON object with an ID, a type and a name.

## Step 3 — list the relationships

Relationships come in two shapes, and a reader has to handle both:

- A **simple** relationship is a key on its subject entity whose value is a reference: `"worked with": {"@id": "urn:…"}`. If the same predicate appears more than once, the value becomes a list.
- A **reified** relationship is a standalone object with `"@type": "ccx:Relationship"` and `ccx:subject` / `ccx:predicate` / `ccx:object`. The exporter uses this shape when a relationship carries its own properties or would otherwise collide with another key.

```python
def refs(value):
    """Yield the IRIs in a {"@id": ...} reference or a list of them."""
    for v in value if isinstance(value, list) else [value]:
        if isinstance(v, dict) and set(v) == {"@id"}:
            yield v["@id"]

triples = []
for m in members:
    if m.get("@type") == "ccx:Relationship":
        triples.append((m["ccx:subject"]["@id"], m["ccx:predicate"], m["ccx:object"]["@id"]))
    else:
        for key, value in m.items():
            if not key.startswith(("@", "cc:", "ccx:")):
                triples += [(m["@id"], key, obj) for obj in refs(value)]

name = lambda iri: entities[iri]["name"] if iri in entities else iri
for s, p, o in triples:
    print(f"{name(s)} --{p}--> {name(o)}")
```

```
Ada Lovelace --worked with--> Charles Babbage
Charles Babbage --designed--> Analytical Engine
Analytical Engine --designed in--> London
```

The prefix filter skips JSON-LD keywords (`@id`, `@type`) and producer metadata such as `cc:template`, which records the schema template an entity was filed under. It is not a relationship.

**In plain English:** a relationship is either a link stored on the entity or its own small record, and twenty lines turns both into `(subject, predicate, object)` rows.

## Step 4 — print the passages that cite each entity

This is the part a plain graph export can't do. Alongside the graph, a package carries `sources.jsonl`, with one record per source document and one per chunk. A source record points at the document's full text, stored as a content-addressed asset inside the package. A chunk record points into that text by character offset and lists the entities extracted from it:

```python
records = pkg.sources()
texts = {r["@id"]: pkg.asset_bytes(r["text"]).decode("utf-8")
         for r in records if r["@type"] == "ccx:Source" and "text" in r}

for chunk in (r for r in records if r["@type"] == "ccx:Chunk"):
    sel = chunk.get("selector")
    passage = texts[chunk["source"]["@id"]][sel["start"]:sel["end"]] if sel else chunk.get("content", "")
    for cite in chunk.get("ccx:citation", []):
        print(f'{name(cite["ccx:citation"]["@id"])!r} <- "{passage.strip()}"')
```

```
'Ada Lovelace' <- "Ada Lovelace worked with Charles Babbage on the Analytical Engine."
'Charles Babbage' <- "Ada Lovelace worked with Charles Babbage on the Analytical Engine."
'Analytical Engine' <- "Babbage designed the Analytical Engine in London."
'London' <- "Babbage designed the Analytical Engine in London."
```

A few details worth knowing:

- A chunk carries either a `selector` (offsets into the retained full text, which is what Chaos Cypher writes) or inline `content`. The `else` branch covers packages from other writers that use `content`.
- Each citation can also carry `ccx:confidence` and `ccx:extractionMethod`, so you can filter out low-confidence extractions before you trust them.
- Chaos Cypher exports also record which chunks justify each *relationship*, under `cc:relationshipCitation`. That key is producer-specific, so it is out of scope for a generic reader. The [spec page](/docs/reference/ccx-format) has the fields if you want them.

**In plain English:** for any entity in the file, you can print the exact sentence it came from, read out of the same file, with no server and no database involved.

## Step 5 — hand it to the tools you already use

Neither of these is needed to *read* the package. They are where the data usually goes next:

```bash
pip install networkx pandas
```

```python
import networkx as nx
import pandas as pd

G = nx.MultiDiGraph()
for iri, node in entities.items():
    G.add_node(iri, label=node["name"], type=node["@type"])
for s, p, o in triples:
    G.add_edge(s, o, predicate=p)
print(G.number_of_nodes(), "nodes,", G.number_of_edges(), "edges")

df = pd.DataFrame([(name(s), p, name(o)) for s, p, o in triples],
                  columns=["subject", "predicate", "object"])
print(df)
```

```
4 nodes, 3 edges
             subject    predicate             object
0       Ada Lovelace  worked with    Charles Babbage
1    Charles Babbage     designed  Analytical Engine
2  Analytical Engine  designed in             London
```

From here it's ordinary graph and table work: centrality, shortest paths, `df.predicate.value_counts()`, a join against your own data.

If you prefer RDF, `pkg.dataset()` returns an `rdflib.Dataset` you can query with SPARQL. It has one gap. A simple relationship whose predicate is free text, like `worked with`, has no IRI in the package's context, so it doesn't appear in the RDF projection. Reified relationships, names and types do. To get every relationship, read the JSON-LD as Step 3 does.

**In plain English:** a few more lines turn the package into a networkx graph or a spreadsheet-shaped table.

## Try it yourself

Steps 1–5 above make one script of about forty-five lines. Paste the Python blocks into `read_ccx.py` in order and run it against any `.ccx` you have:

```bash
pip install ccx-format networkx pandas
python read_ccx.py          # opens my-graph.ccx
```

If you have no package to hand, the next section writes one.

## What a tool that *writes* `.ccx` needs

Reading is half of what makes a format open. The other half is a second program that *writes* it. The same library has a builder, and a minimal package with one cited relationship looks like this:

```python
import ccx

b = ccx.PackageBuilder(name="me/papers", package_version="0.1.0", license="CC-BY-4.0")
b.add_graph("ccx", "knowledge", {"@graph": [
    {"@id": "urn:me:ada", "@type": "Person", "name": "Ada Lovelace",
     "worked with": {"@id": "urn:me:babbage"}},
    {"@id": "urn:me:babbage", "@type": "Person", "name": "Charles Babbage"},
]}, role="default")
b.add_source({"@id": "urn:me:src/notes", "@type": "ccx:Source", "title": "notes.md"},
             text=b"Ada Lovelace worked with Charles Babbage.", source_mode="derived-only")
b.add_source({"@id": "urn:me:src/notes#chunk-0", "@type": "ccx:Chunk",
              "source": {"@id": "urn:me:src/notes"},
              "selector": {"type": "TextPositionSelector", "start": 0, "end": 41},
              "ccx:citation": [{"ccx:citation": {"@id": "urn:me:ada"}}]})

with open("papers.ccx", "wb") as f:
    f.write(b.build())          # build() validates; it raises on a non-conformant package
```

That file validates as `core` and `sources`, and the reader script above prints its entity, its relationship, and the sentence that cites it. A writer of your own, in Python or any other language, needs to do the same things:

1. **A ZIP with a fixed first entry.** The archive starts with an uncompressed `mimetype` entry containing `application/vnd.ccx+zip`.
2. **A manifest with two checksums per member.** Each graph and asset is listed with both a SHA-256 and a SHA-512 digest, base64-encoded.
3. **JSON-LD with no remote context.** The context ships inside the file. A reader must never need the network.
4. **Sources as offsets, not copies.** Store the full text once as a content-addressed asset, and point chunks into it by character range. That's what lets a citation survive the trip.
5. **Stable IRIs.** Mint identifiers once and keep them across exports, so an import updates in place instead of creating duplicates.

Everything beyond that, such as templates, statistics, or your own namespaced graphs, is optional, and a generic reader skips what it doesn't understand.

**In plain English:** the builder does the bookkeeping for you, and the format is small enough to write from scratch in another language.

## The honest framing

`.ccx` is an **open, documented format** with one implementation. We wrote both the spec and the reader. It isn't a standard, and we won't call it one until something we didn't write reads or writes it. The spec is a draft revised in place, so check the revision line at the top of the [spec page](/docs/reference/ccx-format) and pin the version you tested against (`ccx-format>=0.1,<0.2` today).

That is also the invitation. A LlamaIndex or LangChain loader, an exporter from your notes app, a validator in Rust or TypeScript: any of those, written by someone who isn't us, is what turns a documented format into a shared one. The [reader's repository](https://github.com/chaoscypherinc/ccx) is the place to start, and questions and issues there are welcome.

**In plain English:** the format is open and documented, the code above is all it takes to read it, and the next implementation could be yours.
