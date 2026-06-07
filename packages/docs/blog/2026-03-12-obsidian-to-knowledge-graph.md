---
slug: obsidian-to-knowledge-graph
title: "From Obsidian Vault to AI-Powered Knowledge Graph in Minutes"
authors: [denis]
tags: [integrations, workflows]
date: 2026-03-12
draft: true
description: Import an Obsidian vault into Chaos Cypher to add AI-discovered entities and relationships on top of your manual wiki-links — no re-linking required.
---

Obsidian is excellent at capturing linked notes, but the graph it shows is mostly the structure you created by hand. Chaos Cypher can add a second layer: it reads the Markdown itself, extracts entities and relationships, and makes the vault searchable and chat-ready without requiring every connection to be pre-linked.

<!-- truncate -->

The simplest path is to export or zip the notes you want to analyze, then upload the archive as a source. The archive loader recognizes Markdown-heavy projects, strips frontmatter, preserves heading structure, and sends each note through the same indexing and extraction pipeline used for PDFs, web pages, and other documents.

Once indexing finishes, the notes are available for semantic search and RAG chat. After extraction, Chaos Cypher adds typed entities and relationships to the knowledge graph. That means your manual wiki-links can coexist with AI-discovered connections: people mentioned across multiple notes, concepts that appear under different headings, projects connected by shared decisions, or recurring themes that were never explicitly linked.

For a good first pass:

1. Start with a focused folder rather than the entire vault.
2. Remove private notes you do not want processed.
3. Keep filenames and headings descriptive.
4. Upload the folder as a ZIP or TAR.GZ archive.
5. Review the extracted graph, then tune domains or templates if the vault has a specialized vocabulary.

This workflow is useful for research notes, project journals, meeting archives, and long-running personal knowledge bases where the value is not only in the notes themselves, but in the relationships between them.
