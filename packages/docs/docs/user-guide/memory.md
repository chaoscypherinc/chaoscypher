---
sidebar_position: 5
title: Memory
---

# Memory

The memory tools let an agent remember what it is told, in plain words, and get it back later as facts with their provenance. A memory is one short text: a sentence, a note, a pasted paragraph. ChaosCypher stores it as a source of type `memory`, links the people, places and things it names to the ones the graph already knows, and dates it. Asking later returns what the graph knows in the shape the question has: a card for one thing, a grouped list for a set, a chain for a connection, each fact tagged with who said it and when.

The five tools are served by the [MCP server](mcp.md); the `memory` [profile](mcp.md#profiles) lists them alone.

## What `remember` stores

```
remember(text="Pierre moved to Lyon.", by="alice", valid_from="2026-10-20")
```

answers

```
Remembered [alice 2026-10-20] "Pierre moved to Lyon."
Linked: Pierre Bezukhov (Person), Lyon (Location)
Facts: Pierre Bezukhov located_in Lyon
```

- `by` is who says it (the configured default is `user`); `valid_from` is from when it holds (today when omitted); `scope` files it under a name (a project, a person); `about` names who it concerns when the text does not say.
- The text is kept whole and is searchable at once; the facts are extracted from it and linked to the graph's existing entities, so a later "Pierre" lands on the same Pierre.
- A text that looks like a key or a password is refused and nothing is stored. A text with no facts in it is kept as a note.
- Without an extraction model the text is stored as a note and the answer says so.

## The notation

Every fact a memory tool answers with has the same shape:

```
SUBJECT relation OBJECT [who DATE] "the sentence that states it"
```

- `[alice 2026-10-20]` is a memory: who said it, from when.
- `[alice 2026-03-02..2026-10-20, replaced]` is a memory a later one replaced (shown with `as_of`).
- `[doc TITLE]` is a fact from a document; `[pack NAME]` from an installed package. Documents are always in scope.

## Asking

`recall(query="Where does Pierre live?")` answers a card:

```
Pierre Bezukhov (Person)
  also: Pierre
  Pierre Bezukhov located_in Lyon [alice 2026-10-20] "Pierre moved to Lyon."
  Pierre Bezukhov born_in Moscow [doc War and Peace]
```

`recall(query="Name everyone Pierre knows.")` answers a grouped list (`Pierre Bezukhov: friend_of (3)` then one fact per line); `recall(query="How is Pierre connected to Paris?")` answers a chain (`Pierre Bezukhov to Paris:` then one hop per line); a question about everything (`Summarize what you remember.`) answers the digest. Memories the facts did not cover come back under `Notes:`. Documents and packs are always in scope, and up to three of their passages come back under `Passages:` with their title, so a question about a manual gets the manual's own words (a procedure rarely becomes a fact).

Arguments: `scope` limits the memories to one scope (documents stay in scope); `as_of` shows the facts as they stood on a date; `more` doubles the answer's length; `ids` appends an id to each card and fact, for `forget`. An answer longer than the budget ends with `(N more, ask with more=true)`.

`list_memories(scope?, about?, limit?)` lists the memories newest first, one line each: `[alice 2026-10-20] "Pierre moved to Lyon."`. `digest(scope?, since?)` says what a window added: `Since DATE: N memories, M facts added.`, the facts grouped under their subject, then what was replaced and how many memories were forgotten.

## Forgetting

`forget(query="Lyon")` previews what matches, with a handle per memory and what its removal would take:

```
1. a1b2c3 [alice 2026-10-20] "Pierre moved to Lyon." - 1 facts go, 0 stays
To forget one: forget(handle="a1b2c3", confirm=true)
```

A fact stays when another memory or a document also states it. `forget(handle="a1b2c3", confirm=true)` removes the memory, its chunks and the facts only it stated, and reopens what it had replaced (the earlier home comes back as the current one). The answer is `Forgotten a1b2c3: 1 facts removed, 0 kept.` A handle that has become ambiguous is refused with the length that would make it unique; `confirm` without a handle only previews. Every forget leaves an audit row with the handle, who and the counts, never the text, which `digest` counts as `Forgotten: N`.

## Memory packs

A scope's memories travel as a memory pack, a `.ccx` package:

```bash
chaoscypher memory export --scope work work.ccx
chaoscypher graph package load work.ccx
```

`memory export` writes the memories of one scope (every memory without `--scope`) with the facts they link, the sentences that state them and what each memory replaced; `--title` names the pack (`memory:work` by default). `package load` imports it into another database: each memory comes back as a memory, with its `by`, scope and date, tagged `[alice 2026-10-20]` rather than `[pack NAME]`, so it can be recalled, forgotten and replaced there like one remembered on the spot. The import checks a key signature (ed25519) the package declares against its manifest bytes and refuses a package whose signature does not verify, which catches a package changed after signing; it does not say who signed it, and a package with no signature is imported as before. A pack carries the nodes as the graph knows them, so a node that other scopes or documents also describe travels with their reading of it.

## Settings

```yaml
mcp:
  memory:
    result_chars: 1600   # the most characters a memory tool answers with
    wait_seconds: 120    # how long remember waits for the linking before answering "Saved, still linking."
    default_by: user     # who a memory is by when the call does not say
```
