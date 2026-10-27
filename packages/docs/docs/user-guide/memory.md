---
sidebar_position: 5
title: Memory
---

# Memory

The memory tools let an agent remember what it is told, in plain words, and get it back later as facts with their provenance. A memory is one short text: a sentence, a note, a pasted paragraph. ChaosCypher stores it as a source of type `memory`, links the people, places and things it names to the ones the graph already knows, and dates it. Asking later returns what the graph knows in the shape the question has: a card for one thing, a grouped list for a set, a chain for a connection, each fact tagged with who said it and when.

The five tools are served by the [MCP server](mcp.md); the `memory` [profile](mcp.md#profiles) lists them alone. `remember` and `forget` are write tools, so serve them with `--mode write`; in read mode (the default) the profile lists only `recall`, `list_memories` and `digest`.

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
- When the linking has to wait (no extraction model, a model that fails or does not answer in time), the text is kept and the answer says so; see [When linking has to wait](#when-linking-has-to-wait).
- With vector search off (`search.enable_vector_search: false`) the memory tools make no embedding call at all; see [Keyword-only](mcp.md#keyword-only).

## When linking has to wait

A memory is stored before it is linked, so the text is kept and searchable at once even when the model is busy, failing or missing. When the linking cannot finish while `remember` waits, the answer's second line says why, and the memory is linked later:

```
Remembered [alice 2026-10-20] "Pierre moved to Lyon."
Not linked yet: the model did not answer in time. Kept as a note; it links later.
```

The other two lines are `Not linked yet: the model failed. Kept as a note; it links later.` and `Not linked yet: no extraction model is set up. Kept as a note; it links once one is.` A text that could not be stored at all is refused with `Not stored: the database is busy. Try again.`

"It links later" means: a linking the wait gave up on goes on in the same server, and a memory left unlinked stays marked with why and is linked the next time a write-mode server starts (in the background, oldest first, once the client has connected), or at once with `chaoscypher memory relink`. A memory remembered with no extraction model set up is linked once one is, against the graph as it is then. A memory whose link keeps failing waits longer after each failure, never more than a day, and is tried again at the next server start after that; it is never dropped. `chaoscypher memory relink` tries it again at once. Set `mcp.memory.relink_on_start: false` to link only when the command runs, from a timer of your own for example. Until it is linked a memory is a note: `recall` shows it under `Notes:` and `forget` removes it.

`list_memories` ends an unlinked memory's line with its state: `(linking)`, `(not linked yet: interrupted)` (its server stopped while linking it), `(not linked yet: no extraction model)`, `(not linked yet: the model failed, N tries)`, `(not linked yet)`, and `(no facts found)` for a memory that linked with no facts. `digest` adds one line after `Forgotten:` while any memory of the scope is not linked yet: `Not linked yet: N (M no model, K failed, J interrupted)`.

## Scopes

A memory is shown, before its facts are extracted, the things in the graph it is likely to mean, so it writes them by the names the graph already has. A memory without a scope is shown the things its own words name, and the ones `about` names. A scoped memory is also shown what its scope is about: the things the scope's other memories name, the most often named first, and the nodes `mcp.memory.scope_nodes` lists for the scope. What a memory is shown is capped by `extraction.known_entities_limit`: the memory's own names come first, and in the room left the nodes `scope_nodes` lists are kept before the scope's most named things, so a large scope never crowds either out.

```yaml
mcp:
  memory:
    scope_nodes:
      trader: ["Trading journal"]
```

With that setting, `remember(text="Bought 0.1 coin at the open; wrote it down.", scope="trader")` is shown "Trading journal" (a node made with `create_node`, which no memory names) and "ETH market" (named by an earlier `trader` memory), so "wrote it down" lands on the journal, not on a new node. A scoped memory that writes the exact name of a node its scope offers and that a person made (with `create_node`: no type, no source) is attached to that node: the memory is linked to it, and the node keeps its own name, type and properties. Forgetting the memory takes away its link and the facts only it stated; the node and the facts the person made stay. A memory without a scope, or a node offered only because the text names it, keeps today's behaviour. A name in `scope_nodes` that matches no node is skipped and logged once.

A scoped memory may also use the database's own templates. It is shown, beside the built-in types, the templates its scope's nodes use and the ones `mcp.memory.scope_templates` names for the scope, each with its name and description, and a thing it writes with one of those types lands on that template instead of the generic one:

```yaml
mcp:
  memory:
    scope_templates:
      trader: ["Market"]
```

With a "Market" template made with `create_template`, a `trader` memory that names "ETH market" as a market puts it on the Market template. Forgetting the memory never deletes such a template: it is not the memory's own. Only templates a person made are offered: not the built-in ones, not one a source owns (an import's), and not one named like a built-in type such as Person. Only a few are offered at once, each on one line, cut to fit. A name in `scope_templates` that matches no template is skipped and logged once. A memory without a scope, and every document, is shown none of them and is put on templates as before.

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

Arguments: `scope` limits the memories to one scope (documents stay in scope); `as_of` shows the facts as they stood on a date; `more` doubles the answer's length; `ids` appends the node id to each card head and the edge id to each fact (` #ID`). An answer longer than the budget ends with `(N more, ask with more=true)`.

`list_memories(scope?, about?, limit?)` lists the memories newest first (`limit` defaults to 10, at most 50), one line each: `[alice 2026-10-20] "Pierre moved to Lyon."`. `digest(scope?, since?)` says what a window added (`since` defaults to seven days ago): `Since DATE: N memories, M facts added.`, the facts grouped under their subject, then what was replaced and how many memories were forgotten (`Forgotten` counts every scope, not only the one asked for).

## Forgetting

`forget(query="Lyon")` previews what matches, with a handle per memory and what its removal would take:

```
1. a1b2c3 [alice 2026-10-20] "Pierre moved to Lyon." - 1 facts go, 0 stays
To forget one: forget(handle="a1b2c3", confirm=true)
```

A fact stays when another memory or a document also states it. `forget(handle="a1b2c3", confirm=true)` removes the memory, its chunks and the facts only it stated, and reopens what it had replaced (the earlier home comes back as the current one). The answer is `Forgotten a1b2c3: 1 facts removed, 0 kept.` A handle is at least six characters; a shorter one is refused, and a preview lists at most five matches and says how many more there are. A handle that has become ambiguous is refused with the length that would make it unique; `confirm` without a handle only previews. Every forget leaves an audit row with the handle, who and the counts, never the text, which `digest` counts as `Forgotten: N`.

## Memory packs

A scope's memories travel as a memory pack, a `.ccx` package:

```bash
chaoscypher memory export --scope work work.ccx
chaoscypher graph package load work.ccx
```

`memory export` writes the memories of one scope (every memory without `--scope`) with the facts they link, the sentences that state them and what each memory replaced; `--title` names the pack (`memory:work` by default, `memory:all` without `--scope`). `graph package load` imports it into another database: each memory comes back as a memory, with its `by`, scope and date, tagged `[alice 2026-10-20]` rather than `[pack NAME]`, so it can be recalled, forgotten and replaced there like one remembered on the spot. The import checks a key signature (ed25519) the package declares against its manifest bytes and refuses a package whose signature does not verify, which catches a package changed after signing; it does not say who signed it, and a package with no signature is imported as before. A pack carries the nodes as the graph knows them, so a node that other scopes or documents also describe travels with their reading of it.

## Several processes, one database

Any number of processes may write one memory database at once: an MCP server per agent (`chaoscypher mcp -m write`), `chaoscypher` commands, and timers that run them. They must run on the same machine, with the database on a local disk; a network filesystem is not supported, because SQLite's write-ahead log needs memory the processes share. Several servers may also start at the same moment on a database that does not exist yet: the first one builds it and the others wait for it.

What they can rely on:

- **A memory links whole or not at all.** Its facts land in one transaction.
- **No write is lost.** Calls made at the same moment all land. Each write waits up to 60 seconds for the database's writer lock; the wait is fixed, not a setting. Forgetting a memory and linking one try a busy write again a few times before they give up, so under load they can take longer than one wait. A call that still cannot write answers with an error that says the database is busy (`Not stored: the database is busy. Try again.` from `remember`, `Not done: the database is busy with another process's write. Try again.` from the other memory tools); it never hangs and never drops the write silently.
- **The graph settles as if the calls had taken turns.** Once the writers stop, which names were joined and which facts were replaced are what the same calls would give run one after another in the order they were saved. Each save joins names and settles replaced facts while it holds the writer lock, so the last one sees every saved fact.
- **Every call reads fresh.** A tool call sees every write any process finished before the call started, so a server that has run for hours sees a memory another agent saved a moment ago.

Not promised: a `recall` running at the same moment as another process's `remember` may or may not see that memory.

Copy the database file only when every writer has stopped. A copy taken during a write can miss the newest writes, which sit in the write-ahead log beside the file.

## Settings

```yaml
mcp:
  memory:
    result_chars: 1600   # the most characters a memory tool answers with
    wait_seconds: 20     # how long remember waits for the linking before answering that it links later
    default_by: user     # who a memory is by when the call does not say
    relink_on_start: true  # link the memories not linked yet when a write-mode server starts
```
