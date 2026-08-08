---
slug: citation-click-through
title: "Citation Click-Through: How Chaos Cypher Traces Every Answer to Its Source"
authors: [denis]
tags: [graphrag, ai, rag, selfhosted]
date: 2026-07-21
draft: true
description: Every citation in a Chaos Cypher chat answer is clickable -- it jumps straight to the exact sentence and source chunk the model grounded its answer in.
---

Most AI chat answers ask you to take them on faith. There's no way to tell which sentence came from your documents and which one the model filled in from its own training. Chaos Cypher takes a different position: every claim in a chat answer that's grounded in your knowledge graph carries a citation, and every citation is a link you can click.

<!-- truncate -->

## The Problem With Black-Box Answers

Ask a chatbot a question about your own documents and you get an answer that reads well and might be completely right -- or quietly wrong in a way you'd only catch by re-reading the source yourself. The confidence of the answer tells you nothing about how well-grounded it is. That gap between "sounds right" and "is right" is the core trust problem with RAG-backed chat, and it only gets worse as your knowledge graph grows past what you can hold in your head.

## What Click-Through Looks Like

Ask Chaos Cypher a question in **Chat** and the streamed answer arrives with small inline markers next to the sentences it pulled from your sources -- a document icon for text, a thumbnail for an image chunk. Hover one and a tooltip shows the source it came from, the page number, and a validation badge: **Verified** when the cited sentence was actually found in that chunk, **Invalid** when it wasn't.

<!-- screenshot: chat panel with an inline citation marker and its hover tooltip showing source, page, and verified badge -->

Click the marker and you land on that source's detail page with the exact chunk highlighted -- not "somewhere in this document," the specific paragraph the sentence was grounded in. Answers that pulled from a scanned page or image work the same way: the marker is a thumbnail, and clicking it opens the full image.

<!-- screenshot: source detail page with a chunk highlighted after navigating from a chat citation -->

## Why This Matters More Than a Nice-to-Have

This closes the loop that most RAG chat interfaces leave open. Instead of trusting a generated answer wholesale, you can verify any individual sentence in a click -- confirm it against the source, catch a citation that doesn't hold up, or just read more context around the fact you asked about. It's the same underlying link between graph and source text that powers node detail pages in the graph view; chat citations are that mechanism surfaced at the point where you're actually reading an answer.

In plain English: if Chaos Cypher tells you something, you can check it -- one click, straight to the sentence.

## Try It

Ask a question about a document you've already imported and look for the citation markers in the answer. If you haven't imported anything yet, the [GraphRAG with Ollama in 10 minutes](/blog/graphrag-ollama-10-minutes) quickstart gets you from zero to a cited answer in about ten minutes, fully local.
