---
slug: docker-compose-for-knowledge
title: "docker-compose for Knowledge: Stack Packages into One Graph Your Agent Can Query"
authors: [denis]
tags: [graphrag, opensource, selfhosted, ai]
date: 2026-09-21
draft: true
description: An axiomatize.yaml lists the knowledge packages you want; chaoscypher compose builds one database from all of them, with every citation intact, and serves it over HTTP or MCP. One question, answered across packages.
---

You do not write one giant `main.py`. You compose services. Knowledge should work the same way: a package for your architecture docs, a package for your decision records, a package someone else published for a regulation you have to comply with, and a two-line file that says "these, together, are what my agent knows."

`chaoscypher compose` is that file. It resolves a list of `.ccx` packages, imports them into one runtime database, indexes it, and serves it. A question that needs a fact from one package and a fact from another is answered from the same graph, with the citations both packages carried.

<!-- truncate -->

## What You'll Build

- An `axiomatize.yaml` that lists two knowledge packages built from real documentation
- One composed database containing both, built with a single command
- A local knowledge server started in the background and stopped just as cleanly
- The same composition served to an MCP client, answering a question that spans both packages

Everything in this post ran on one laptop with no cloud account.

## Two packages, one question

The example uses Chaos Cypher's own documentation. The architecture pages (core, cortex, neuron, data flow, storage, plugins) were extracted into one package, and the six architecture decision records into another. That split is deliberate: "which component depends on the queue?" lives in the first package, and "which decision record governs the queue backend?" lives in the second. Neither package can answer the whole question alone.

Both were built the way any package is built: add the documents, let extraction run, export with `chaoscypher graph package export`. The [mount post](/blog/build-once-mount-anywhere) covers that end to end.

## Step 1: Write the stack

`chaoscypher compose init ./architecture.ccx ./decisions.ccx` writes it for you; this is the file:

```yaml
name: chaoscypher-architecture
version: 1.0.0

packages:
  - ./architecture.ccx
  - ./decisions.ccx

settings:
  merge_strategy: namespace
  output_dir: ./output
  port: 8181
```

A package entry can be a local `.ccx` file, an extracted package directory, or a Lexicon Hub reference like `acme/eu-ai-act:1.2.0`. Hub packages are downloaded into a cache the first time and read from disk after that, and a package's declared dependencies are pulled in with it.

## Step 2: Build it

```bash
chaoscypher compose build
```

```
Building composition: chaoscypher-architecture
  Packages:  2
  Strategy:  namespace
  Output:    ./output

Success: Built composition: chaoscypher-architecture
  Packages: 2
    • chaoscypher/archdocs:1.0.0
    • chaoscypher/archdocs:1.0.0
  Entities: 177
  Relationships: 205
```

That is a real Chaos Cypher database at `output/databases/default`, built by importing each package through the same importer `mount` and `graph package load` use. The 14 sources, their chunks, and all 178 citations came along, and every entity kept the stable identifier it had in its package. A `composition.json` next to the database records exactly which package contributed what:

```json
{
  "strategy": "namespace",
  "total_entities": 177,
  "total_relationships": 205,
  "packages": [
    {"name": "chaoscypher/archdocs", "entities": 140, "relationships": 162, "sources": 8, "citations": 141},
    {"name": "chaoscypher/archdocs", "entities": 37, "relationships": 43, "sources": 6, "citations": 37}
  ]
}
```

Two things about how packages meet. Templates are unified by name, so two packages that both define a `Module` type produce one `Module` type. Entities keep their per-package identifiers, so independently built packages never collide, and the same package listed twice is stored once. The strategy setting is recorded for the day cross-package entity resolution lands; today every strategy builds this same database.

The build also writes a `settings.yaml` beside the database. A composition is a local, read-mostly knowledge server, so that file tells the server not to wait for a queue backend and not to ask for an LLM. You can edit it; later builds leave it alone.

<!-- optional screenshot: terminal showing compose build output with the entity and relationship counts -->

## Step 3: Serve it

```bash
chaoscypher compose up --detach
```

```
Starting composition: chaoscypher-architecture
  Config:     axiomatize.yaml
  API port:   8181
  Detached mode: Yes

Success: Composition started in background
  Server: http://localhost:8181
```

In this run the health endpoint answered after six seconds. The server is a Cortex API bound to loopback, its output goes to `output/server.log`, and its process id goes to `output/compose.pid`. That last file is what makes the next command work from any shell, hours later:

```bash
chaoscypher compose down
```

```
Stopping composition: chaoscypher-architecture
Success: Composition stopped
```

Run it again and it tells you the truth rather than claiming a stop:

```
No running composition server found (nothing recorded in output/compose.pid)
```

## Step 4: Ask across the seam

The HTTP API is one way in. The other is MCP, and it needs no server at all:

```bash
chaoscypher compose mcp
```

That builds the composition if it has not been built and then serves the composed database to whichever MCP host launched it. For Claude Desktop, the line goes in the server config:

```json
{
  "mcpServers": {
    "architecture": {
      "command": "chaoscypher",
      "args": ["compose", "mcp", "-c", "/path/to/axiomatize.yaml"]
    }
  }
}
```

The question that needs both packages:

> Which ADR governs the queue backend that Neuron depends on?

`graphrag_search` seeded its graph walk from the entities closest to the question and came back with the relationship chain from the architecture package (the document pipeline calls Neuron, Neuron depends on the message queue, Core depends on the message queue) alongside chunks from both packages: the Neuron and overview pages from the architecture package, and the decision-record index from the decisions package, which is where ADR-0004, the Redis-to-Valkey migration, is catalogued. The evidence for the answer crossed the package boundary because the graph did.

<!-- optional screenshot: Claude Desktop answering the cross-package question with chunk citations from both packages -->

**In plain English:** you listed two packages in a file, and your assistant answered a question neither package could answer alone, with the receipts from both.

## What this is for

The obvious use is stacking your own packages: the codebase graph, the docs graph, the runbooks graph, composed into one thing your coding agent mounts. The more interesting use is stacking packages you did not build. A regulation package from the Hub, your own product documentation package, and a compliance-notes package become one graph that can answer "does feature X trigger Article 6, and what did we decide about it?" That is a graph traversal across three authors' work, and none of the source documents left your machine.

## What it does not do yet

Entity resolution across packages is not there. If two packages describe the same person under different identifiers, the composed graph has two nodes, and the strategy setting does not change that today. The composed server binds to loopback only, on purpose. And there is no `compose watch`: when a package changes, you run `build --clean` again.

## Next steps

- The [compose reference](/docs/reference/cli/compose) covers every option, the injected environment, and the runtime settings file.
- Packages for this come from anywhere Chaos Cypher can export one: your own graph, a colleague's, or the [Lexicon Hub](/docs/lexicon-hub).
- If you only have one package, you do not need a stack: [mount it](/docs/reference/cli/mount).
