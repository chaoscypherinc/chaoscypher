---
slug: obsidian-to-knowledge-graph
title: "From Obsidian Vault to AI-Powered Knowledge Graph in Minutes"
authors: [denis]
tags: [integrations, workflows]
date: 2026-03-12
draft: true
description: Import an Obsidian vault into Chaos Cypher to add AI-discovered entities and relationships on top of your manual wiki-links — no re-linking required.
---

Open your Obsidian graph view and look at it honestly: every line on that screen is a link you typed by hand. The person mentioned in forty notes but never `[[linked]]`, the concept that appears under three different names, the two projects connected by a decision you wrote down once and forgot -- none of it shows up. Your vault knows more than its graph does.

<!-- truncate -->

Chaos Cypher adds the missing layer. It reads the Markdown itself, extracts entities and relationships with an LLM, and makes the whole vault searchable and chat-ready -- without requiring a single connection to be pre-linked. Your manual wiki-links stay exactly what they are: deliberate structure. The AI-discovered layer sits alongside them, surfacing the connections you never typed.

## The Workflow

No plugin required -- the archive loader does the work:

1. **Zip the notes you want to analyze.** Start with a focused folder of at least ~10 notes rather than the entire vault, and leave out private notes you don't want processed.
2. **Upload the ZIP (or TAR.GZ) as a source.** The archive loader recognizes Markdown-heavy projects, strips frontmatter, preserves heading structure, and sends each note through the same pipeline used for PDFs and web pages. Smaller archives are processed by the generic loader, which keeps frontmatter intact -- bundle at least ten notes to engage the Markdown handler's frontmatter stripping.

![Add Source dialog with URL input and file drag-and-drop](/img/screenshots/sources-upload-dialog.png)

3. **Confirm the detected domain.** Indexing (chunking + local embeddings) takes seconds and immediately enables semantic search and RAG chat. After indexing, Chaos Cypher analyzes your notes and proposes an [extraction domain](/docs/user-guide/domains) -- review and confirm (or override) it in the dialog, and extraction starts.

![Domain confirmation dialog proposing a detected extraction domain](/img/screenshots/domain-confirmation-dialog.png)

4. **Wait for extraction.** Extraction builds the graph: typed entities and relationships pulled from the prose itself.
5. **Explore what the AI found.** People mentioned across multiple notes, concepts that appear under different headings, projects connected by shared decisions, recurring themes that were never explicitly linked -- now visible, navigable, and traceable back to the exact notes they came from.

![Knowledge graph visualization showing extracted entities and relationships](/img/screenshots/graph-visualization.png)

6. **Ask questions.** "What decisions led to the current architecture?" or "Where do my notes on attention mechanisms and retrieval overlap?" -- answered with citations back to your own notes, not the internet.

## A Few Tips for a Good First Pass

- Keep filenames and headings descriptive -- they become context the extractor uses.
- If your vault has a specialized vocabulary, review the extracted graph and then tune [domains or templates](/docs/user-guide/domains) to match it.
- Everything runs locally if you pair it with [Ollama](/blog/local-ai-knowledge-graph) -- your journal never leaves your machine.

This workflow shines wherever the value isn't only in the notes but in the relationships between them: research notes, project journals, meeting archives, long-running personal knowledge bases.

## What's Next

A first-class Obsidian importer -- reading vaults directly, preserving wiki-links as graph edges alongside the AI-discovered ones -- is on the roadmap. If that's your use case, [tell us how you'd want it to work](https://github.com/chaoscypherinc/chaoscypher/discussions); vault structures vary wildly and real examples shape the design.
