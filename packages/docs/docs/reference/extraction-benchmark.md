# ChaosCypher Extraction Benchmark

> A small, reproducible benchmark measuring how LLMs perform as drop-in
> extractors in the ChaosCypher knowledge graph extraction pipeline.

This benchmark answers one question: **given the ChaosCypher pipeline
(chunking, prompts, post-processing), how does each model rank on extracting
structured entities and relationships from text?**

It is *not* a model-intrinsic benchmark. The score reflects model + the
ChaosCypher pipeline together — that's the pragmatic point. Swap in a
different prompt or a different domain template and you get different
scores. Both prompts and templates live in this repo and are part of the
methodology.

## Status

**v2.0** — extraction leaderboard plus an optional full-pipeline path
(embedding retrieval + GraphRAG chat with an LLM judge,
`chaoscypher benchmark run full`) whose stages roll into a composite
**Overall** column on the same leaderboard. Gold-set ground truth is still
pending (v1.5).

## Vocabulary

- **Dataset** — the test unit: a corpus + metadata + how to evaluate it.
  Datasets are reusable; multiple configs can reference the same dataset
  by id.
- **Corpus** — the body of text inside a dataset (the `.txt` file).
- **Config** — a runnable benchmark recipe (a yaml file). Defines run
  params, the list of dataset ids to evaluate, and the list of models to
  run. Selected by `chaoscypher benchmark run [NAME]`.

## What's measured

For each `(model, dataset)` pair, one run produces:

- **Quality** — the quality grade (0–100; its version is `SCORING_VERSION` in `services/quality/scoring.py`) from
  `chaoscypher_core.services.quality.QualityScorer`, the same scorer
  ChaosCypher uses in production. The headline number per model is the
  unweighted mean across the datasets the model succeeded on.
- **Speed** — median LLM latency per chunk in milliseconds.
- **Cost** — USD cost for the run. Local Ollama models cost $0; commercial
  models look up `(provider, model)` in the dated price registry shipped
  with the harness.

## Full-pipeline scoring (v2)

`chaoscypher benchmark run full` runs three stages and scores each:

- **Extraction** — the quality grade described above.
- **Embedding retrieval** (scorer v1) — each `(extractor, embedder)` graph is
  queried with the dataset's labeled queries; the headline score is
  MRR × 100, with Recall@1 and Recall@3 reported alongside.
- **GraphRAG chat** (scorer v1) — an LLM judge scores each answer for
  faithfulness (0–5), correctness (0–5), and refusal correctness (whether
  out-of-scope questions were properly declined); the headline is the
  weighted mean (faithfulness 0.4, correctness 0.4, refusal 0.2) scaled
  to 0–100.

### Composite "Overall" column

Whenever extraction rows are present, the rendered leaderboard leads with a
unified **Overall Leaderboard** — one row per extractor LLM, with a weighted
composite across five dimensions. Default weights (overridable via the
config's `weights:` key):

| Dimension | Default weight | Source |
|---|---|---|
| Extraction | 0.40 | quality grade (mean across datasets) |
| Retrieval | 0.20 | embedding-retrieval headline for the pinned `defaults.embedder` |
| Chat | 0.20 | chat headline for the pinned `defaults.embedder` + `defaults.chat` |
| Speed | 0.10 | per-chunk latency normalized against fixed anchors (500 ms → 100, 30,000 ms → 0) |
| Cost | 0.10 | total run cost normalized against a fixed $1.00 anchor (free → 100) |

Speed and cost use **fixed** anchors (absolute, not relative) so a model's
Overall does not move when the field of competitors changes. Dimensions that
were not run drop out and the remaining weights are renormalized — an
extraction-only run still gets an Overall from extraction + speed + cost.
The config's `defaults:` key pins which embedder and chat candidate are held
fixed when attributing retrieval/chat scores back to an extractor.

## Instruction probes and the leaderboard

The [leaderboard](/leaderboard) is scored differently from the corpus grade
above. A corpus score blends recall, precision and graph shape into one
number, and it turned out to reward volume: a run cut off mid-answer could
outscore a complete one. The probe suite asks a narrower question, one
instruction at a time: **does the model do what the extraction prompt
says?**

- **A probe** is a short hand-written passage plus the instruction under
  test, run through the real extraction prompts and parser. Pure-function
  checks on the parsed output decide pass or fail; there is no judge model.
- **Sections** follow the prompt: A format compliance, B evidence discipline
  (sentence references, no world knowledge), C entity discipline (names, not
  roles; aliases, not descriptions), D relationship discipline, E robustness
  (runaway generation, decoys, pipes in names).
- **Tiers**: `easy` isolates the instruction in a short passage; `hard`
  adds the phenomenon that trips models on real text (negation, reported
  speech, capitalised roles). The headline is the tier-weighted pass rate.
- **Carrier tier (section H)**: the same instructions and checks, spliced
  into a real ~3,000-character extraction group of Book One: 21 probes in
  the soirée chunk (21 named entities) and, since 2026-10-01, the same 21 in
  a second chunk, the end of Dólokhov's window bet running into the Rostóv
  name day (25 names), 39 in all: three variants are left out because the
  chunk itself contains a person the probe's check forbids (the "stay
  unlinked" probe names Alexander, who is at the soirée being discussed).
  This is the "does it transfer to production-sized chunks?" column, and
  with 39 probes one verdict moves the Overall score by about a point
  instead of two. Two extra checks apply
  there: the carrier's own cast is still found, and nothing outside the
  input is invented. A row run on the first chunk only says so in its notes.
- **Completion gate**: a probe whose response hit the token limit or the
  loop detector fails regardless of what was parsed. Rows also carry the
  truncation and loop counters, and the leaderboard shows them.
- **Pinned**: thinking off (the runner probes each model to confirm it
  obeyed), temperature 0, seed 42. A model that thinks anyway is marked.

Run it with `chaoscypher benchmark run probes --local-only` (the `probes`
config; `--model-timeout 5400` caps a hung model at 90 minutes and moves on).
`scripts/benchmark/rescore_probes.py` re-applies the current checkers to a
saved run without touching the models, and
`scripts/benchmark/export_leaderboard.py` turns a native run and a carrier
run into the data file behind the leaderboard page.

### Grounded-chat probes

A `full` run with no `judge:` scores the chat stage with pass/fail probes
instead of a judge model. Each of the 80 labelled questions on
`tech_encyclopedia_tiny` (20 single-hop, 13 paraphrase, 20 multi-hop, 15
fine-grained discrimination, 12 out-of-scope) is answered from the
retrieved context alone, and passes only if every check for its band
passes:

- **finish_stop** - the answer ran to completion.
- **answers_from_graph** - it names the question's curated answer terms. In
  `queries.yaml` an `answer_terms` item may be a list of aliases, such as
  `[Mary, "Andrew's sister"]`, and naming any one of them satisfies it. An
  alias matches only as a whole phrase: "his sister" does not satisfy
  `"Andrew's sister"`; an alias of the form "X of Y" tolerates up to four
  words between X and Y, so "the father of the prodigal son Anatole" does
  satisfy `"father of Anatole"`. A plain term matches more loosely, on any
  significant word of it.
- **no_unsupported_names** - every proper name is in the retrieved context or
  the question (possessives, headings and acronym expansions are tolerated).
- **no_unsupported_numbers** - every year or number is in the retrieved
  context or the question, so the right names with a wrong year still fail.
- **must_not_contain** - none of the question's declared wrong facts is
  stated: the famous wrong year, or the answer a wrong join between two
  facts produces. A wrong fact named inside a negated clause is not
  stated: "he came on inspection business, not for the name day" does not
  state "for the name day". The clause runs from the nearest negation
  before the phrase ("not", "n't", "never", "no", "nor", "neither",
  "cannot", "rather than", "instead of") to the next clause boundary (a
  full stop, `;`, `:`, a line break, a dash, "but" or "except", a comma
  before a new subject, or the end of the answer), so a negation in the
  sentence before still leaves the wrong fact stated. "Not only X" asserts
  X, unless the declared phrase itself starts with "only": "there is not
  only one Pétya" denies "only one Pétya". A phrase that carries its own
  negation, an exceptive or inner punctuation ("age is not stated",
  "squadron commander beyond Schubert", "his son, Borís") is stated wherever
  it stands, and so is a phrase the fixture marks with a leading `!`: a
  wrong refusal whose negation sits outside the phrase.
- **leads_with_gold** (fine-grained) - the right entity comes before the
  confusable one.
- **declines_when_unsupported** (out-of-scope) - the answer declines and
  asserts none of the tempting world-knowledge answers. A decline is a
  known phrase ("not in the sources") or a pattern: a negation near a
  source word, "does not say", "is not stated", "cannot determine", "no
  mention", "absent from". A labelled final answer that does not itself
  decline ("Answer: Schubert") counts as an assertion, whatever the model
  hedged on the way to it.

The out-of-scope questions ask for famous facts the corpus does not contain,
so a model that answers from memory fails them. Tiers weight the bands as
the instruction probes do: single-hop and paraphrase easy, multi-hop and
fine-grained medium, out-of-scope hard.

`war_and_peace_book1` carries a second set of 80 questions (15 single-hop,
10 paraphrase, 20 multi-hop, 20 fine-grained, 15 out-of-scope) over chapters
I-XIV of Book One of War and Peace, about 114,000 characters. The
`tech_encyclopedia_tiny` corpus is about 6,000 characters, while GraphRAG
retrieval returns about 7,700 characters of context per question, so there
every question is answered from the whole text. On Book One, retrieval returns
well under a tenth of the corpus, so the test becomes finding the right
passages: joining facts from different chapters, keeping apart names the text
keeps apart (the two Annas, Dólokhov and Anatole, Natásha and Sónya), and
declining questions whose famous answers (Austerlitz, Bald Hills, 1812) lie
outside the excerpt.

The leaderboard's **Grounded chat** column is this Book One set, answered
from one fixed reference graph (built by Gemma 4 31B) with thinking on, so
the only thing that changes between rows is the chat model. It shows the
percentage, with the count and the rest of the detail in the hover, `—`
for a model that was not measured, and a flag for a run that hit the
per-model time limit ("did not finish") or for answers cut off at the
output-token cap. Because every model reads the same retrieved context, a
question whose fact never reaches that context measures nothing about the
model: every row is guessing, and a lexical scorer passes whoever names
the right term in passing. The fixture was audited against the retrieved
context on 2026-10-01 and the questions it found unanswerable carry
`answerable: false` (`excluded_ids` in the exported board lists them; the
leaderboard page shows the count): they are still asked, their answers kept, but they are on
no row's count, so every model is scored on the remaining questions (`total` in the board). The page says so, draws them as
dashed cells, and still reports any remaining question every scored model
fails (the retrieval floor). `export_leaderboard.py --chat <run.json>`
adds the column.

The page shows one percentage per job and keeps the counts in the hovers.
On wide screens a detail line under each model draws every probe and every
question as a cell (isolated, in chunks, chat; orange = failed, half-tone =
failed in some runs, dim = a retrieval miss every model shares, dashed =
excluded by the audit), in the same order on every row, so a column of
orange is a question the graph never answered and a lone cell is one
model's miss. Hover a cell for the instruction or question.

**Every score carries a 90% interval**, shown as ± beside the number and
spelled out in the hover. It comes from resampling the row's tasks (2,000
seeded draws; a task's verdict is one of its runs' at random), so it says
how much the score could move if the suite had drawn other tasks of the
same kind. Rows whose intervals overlap are not separable by this board:
sorted by Overall, the page marks such a row with ≈ and the homepage
teaser gives it a tied rank. One in-chunks verdict moves Overall by about a point, so most neighbouring rows are ties. Several
runs of one model pool: the row shows the mean, says "mean of N runs" in
its notes, and each cell is the fraction of runs that passed the task.
Local rows at temperature 0 and seed 42 reproduce themselves, so repeats
narrow nothing there; the interval is about the size of the suite.
**Extraction** blends the two probe boards as pass rates, in chunks counted
twice and isolated once, because in chunks is the number that transfers to
real data and isolated is the diagnostic behind it; the in-chunks strip (one
cell per probe, orange = failed) is that column's bar. **Chat** is the
grounded-chat pass rate. **Overall** is 0.6 × extraction + 0.4 × chat. A row
needs both to get an Overall, and a chat run that did not finish counts as
0, like any probe that does not finish. Speed is left out because it depends
on the GPU, so it stays its own column: a word on fixed anchors (fast under
15 s per probe, moderate under 45 s, slow under 120 s, very slow above) with
the median seconds and the run time in the hover. The blended numbers are
computed once, in `scripts/benchmark/export_leaderboard.py`, and written to
both data files as `scores` (with the weights as `score_weights`), so the
page, the homepage teaser and the interface's model pickers all show the
same figures. This Overall is a different thing from the v2
full-pipeline composite above: that one scores a `benchmark run full`
report, this one scores the probe and chat boards.

A model whose Ollama manifest lacks tool calling is marked "no tools": the
app's chat loop always passes tools to the model, so such a model can score
here but cannot be the app's chat model. The interface's chat and extraction
model dropdowns are generated from the same export (`chat_pct`, `chunks`,
`tools` in `packages/interface/src/data/modelScores.json`); models without
tool calling and chat runs that timed out are listed there under
"Not recommended".

### Where the graph matters: the three-rung retrieval comparison

The chat column proves which chat model does best on ChaosCypher's
retrieval. It does not show what that retrieval adds over the RAG a
prospective user already has, so the leaderboard carries a second section,
"Where the graph matters", that answers the same questions with the same
prompt, the same context renderer and the same chat model under three
retrievals that differ only in what they retrieve:

1. **naive** - the canonical tutorial pipeline, run through LlamaIndex rather
   than code of ours: a sentence splitter (512 tokens, 50 overlap) over the
   corpus text, a vector index embedded with the pack's embedder, pure vector
   similarity, the top four chunks.
2. **hybrid** - ChaosCypher's chunk search without the graph: the hybrid arm
   (BM25 keyword plus cosine over the pack's 900-character chunks, merged by
   max), the top ten chunks, rendered as chunks only.
3. **graphrag** - the product's `graphrag_search`: seed entities, personalised
   PageRank, the chunks the top entities cite merged with the same hybrid
   search (a reserved share of the budget keeps the hybrid arm's best hits),
   the entity and relationship lines plus ten chunks.

The budgets match: four chunks of 512 tokens is about 8,000 characters, ten
900-character chunks about 7,700, so the comparison is about what is
retrieved, not how much; the graphrag rung adds its entity and relationship
lines on top of that matched chunk budget, which is the thing measured.

The questions are the fixture's `graph_advantage` band, written in advance
to need what a graph provides, in four kinds: *cross-chapter join* (the
answer joins facts stated in different chapters), *relationship path* (how
two people are connected, through one or two others), *cast aggregate*
(which people share a property or scene) and *disambiguation* (two people
the text keeps apart by a shared name or title). The keep rule was fixed
before any candidate was run: a candidate is dropped only when no rung
retrieves its answer terms, never because a baseline wins it, and the
section reports every kept question under every rung, the ones plain RAG
wins included. The band is never counted in the chat column.

For each model with rows under all three rungs the section shows, per kind,
for the band as a whole and for the original 80 questions, how many it
answered right under each rung, and the lift of GraphRAG over each baseline
in questions with a 90% interval from a paired bootstrap (resample the
questions, score both rungs on the sample, take the difference; 2,000
seeded draws). Every question is scored with the same checks and the
`answerable` flags off, since a rung's retrieval miss is exactly what the
section measures. Three worked examples follow: the questions with the
largest lift over naive RAG, preferring different kinds, with each rung's
retrieved context trimmed to the lines that hold an answer term plus two
lines around them, the model's answer and its verdict, for the best local
model by Overall (and the best Claude row once one has all three rungs). Nothing in them is hand-edited.

**The headlines and their bars.** The section, and the homepage's figures, state
a headline only when the exporter finds that it holds across the models
measured, never on one or two of them, and only on a board that was not
let through with an override. Each comes straight from the rows:

- *Fewer failed answers on ordinary questions.* The median, over models, of
  the share of plain RAG's failed answers that GraphRAG removes on the 80
  original questions, which were not written for the graph. It holds when the
  median is at least 25% and most models clear 25% too.
- *Fewer failed answers on graph-shaped questions.* The same measure on the
  band, held to the bar against plain RAG and against hybrid search.
- *A smaller model, the same answers.* The best local model on plain RAG over
  the original questions, against the smallest local model the graph lifts to
  that score (with the graph at least as good, and better than its own plain
  RAG). It holds when most models reach that score with the graph and the
  bigger model needs at least 1.8 times the smaller one's GPU memory.
- *Answered only with the graph.* Band questions no model answers on plain
  RAG or hybrid search and at least half do with the graph; it needs 5.

A failed answer is any answer that does not pass. Most of the misses the
graph removes are answers that left out what was asked or declined when the
context held it; wrong facts stated are a small share of the misses under
every rung, so the headlines say "failed", not "wrong" or "hallucinated".
Each corpus is reported on its own: every headline, bar, interval and count
is computed over one corpus's questions and names the corpus it is about,
nothing is pooled across corpora, and a headline that holds on one corpus and
not on another is shown for that corpus only. Where every answer on a corpus
was also graded by hand, that corpus's comparison and headlines are the
hand-graded numbers, and the page says so; the scorer's numbers are kept in
the data file beside them.

**Citation accuracy.** The context labels every chunk (`[CHUNK C3 | ...]`)
and models cite those labels. A reported check, `cites_the_answer`, parses
the citations and asks whether a cited chunk holds an answer term; an
answer that cites nothing is uncited, neither right nor wrong, and an
out-of-scope answer is not judged. The share of cited answers that cite a
chunk holding the answer is shown in the chat hover on the main board and
per rung in the comparison. It is a number, not a gate: a model that answers
right but cites the wrong chunk keeps its point, and is visible.

The comparison runs through `scripts/benchmark/run_mcp_suite.py --retrieval
<rung>` for harness rows and the `retrieval:` field of a chat config for
local models; `export_leaderboard.py` writes it as `suites.chat.comparison`
once a model has all three rungs.

## Benchmark any MCP client

The benchmark can also run against whatever model sits behind an MCP
client - Claude Code, Claude Desktop, Cursor, anything that speaks MCP -
through the ChaosCypher MCP server. The client's model answers each task
itself, so the run uses the client's own subscription or API key; ChaosCypher
calls no LLM for the answers.

Two suites are available:

- **Extraction probes** (`probes` = sections A-E, `probes-carrier` =
  section H, `probes-all`). Each probe takes two stages, as in the pipeline:
  `entities` (the entity harvest prompt, answered with `E|`/`P|` lines), then
  `relationships` (the relationship prompt, built from the entities parsed
  out of the first answer, answered with `R|` lines). When the first answer
  leaves no entities, the pipeline would skip the second pass, and so does
  the run. The answers go through the same parser, filters, loop detector and
  checks as a local run.

  From a source's second group on, the pipeline's entity prompt also carries
  a **KNOWN ENTITIES** block: the named entities the earlier groups of the
  same source produced, one line each with its type, the aliases given so far
  (never a single word, such as a diminutive, a given name alone or a bare
  title, nor a speaker-relative phrase such as "my wife"; the guards still
  read them) and up to three relation phrases (its title, role or family role and its
  edges to other listed entities), ranked by how many groups cite them. It is there
  because each group is otherwise read alone, so a later passage's "the
  margrave" or "the regiment" would become a node of its own instead of the
  entity an earlier group named ("Margrave Oswin Kell", alias "the
  margrave"). The block holds at most `extraction.known_entities_limit` lines
  (default 80; `0` turns the block and its guards off). Two code guards keep
  the model from crossing two listed entities: an alias that is another
  listed entity's name is dropped from the record, and a listed name that
  the record's cited sentences do not support is demoted to the one
  reference phrase they write, or kept and flagged when they write none or
  several; both are flagged on the node and counted on the source's quality
  metrics. A title or kinship word alone never names a listed entity: an
  edge whose endpoint is written so, or is a name the guard kept flagged,
  is kept flagged `known_endpoint_not_named` and left out of the default
  chat context. A record the filters flagged, or whose name the name guard could
  not support, is never listed, so a doubted name is not offered to later
  groups and its aliases never support it there; a record that only lost a
  crossed alias is listed without it. A probe is a single passage, so its
  prompt has no block.
- **Grounded chat** (`chat`). Each question of the chat fixture is one
  `answer` stage. The bridge runs GraphRAG retrieval against a **reference
  pack** - the same extracted graph the local chat runs retrieved from,
  indexed with the same embedder - and hands the client the exact prompt the
  local run sends (one user message: the retrieved context and the
  question). The answer is scored by the local chat board's scorer.

| Tool | What it does |
|---|---|
| `start_benchmark` | Starts a run: `suite`, `client`, `model`, optional `reference` (chat: the pack name, defaulting to the only one installed), `only`, `label`, `client_settings`. Returns the `run_id` and the first task. |
| `get_benchmark_task` | Returns the next task: `task_id`, `kind`, `stage`, `system_prompt`, `user_prompt`, `answer_format`, plus section/tier or band - or `done`. |
| `submit_benchmark_output` | Stores the model's raw answer for one stage (`truncated: true` if the client cut it off). Returns acceptance, progress and the next task - no verdicts. |
| `get_benchmark_progress` | Submitted and pending tasks. |
| `finish_benchmark` | Scores the run and writes `<data dir>/benchmark/results/mcp-<run_id>.json`; returns the headline score and pass counts. |

A reference pack lives in `<data dir>/benchmark/reference/<name>/`
(`manifest.yaml`, a single-file `app.db` snapshot of the graph, the fixture's
`queries.yaml`). Export one from a local run's graph cache:

```bash
chaoscypher benchmark reference export --dataset war_and_peace_book1 \
  --extractor ollama/gemma4:31b --embedder ollama/qwen3-embedding:4b \
  --dedup-embedder ollama/qwen3-embedding:4b
chaoscypher benchmark reference list
```

`--dedup-embedder` names the embedder whose vectors merged the graph's entities during extraction (`benchmark run` prints it; `exact` when semantic deduplication is off). It is part of the graph and of its cache key, unlike `--embedder`, which only indexes the graph for retrieval and can be swapped by re-indexing. `--workspace` points at the benchmark workspace holding `graph_cache/`
(default: the one `benchmark run` uses); the pack is named after the dataset
unless `--name` says otherwise. Export indexes the pack with its embedder
right away (`--no-index` defers that to the first chat task), so the
embedder must be reachable.

**Integrity.** The client sees only what a local run's model sees: the
prompts, the stage and neutral meta. No tool returns the fixture's checks,
expected answers or verdicts before `finish_benchmark`. Each stage is
answered once - a stage already answered, or a completed task, is refused
(`TASK_FINAL`) and the refusal is counted on the result row - and a finished
run takes no more answers. Run state is saved after every call, so a client
that stops can pick up where it left off. The tools are offered in read mode
too, since they never touch the graph. Drive a run with the client's tools
restricted to the five benchmark tools, from a directory outside the
ChaosCypher repository, so the model cannot read the fixture files.

For Claude Code, `scripts/benchmark/run_mcp_suite.py` does exactly that.
It creates and finishes the run on the bridge itself, so the model never
sees the suite's arguments or results, and answers each task in a fresh
`claude -p` session started in an empty directory, loaded with only the
ChaosCypher MCP server and allowed just `get_benchmark_task` and
`submit_benchmark_output`. One session per task also means every question
is answered without the previous ones in context, as in the local runs.
The row's `client_settings` record the model, effort, thinking mode, client
version and the isolation the client allowed. `--client codex` runs the same
loop through `codex exec` (ephemeral, only our server, read-only sandbox);
Codex cannot be denied its shell, so there "read no files" is an instruction,
and the row says so:

```bash
uv run python scripts/benchmark/run_mcp_suite.py --suite chat \
  --reference war_and_peace_book1 --model claude-sonnet-5 --effort high \
  --label "Sonnet 5 via Claude Code"
```

A complete leaderboard row takes three suites with the same `--model`:
`chat`, `probes` (the isolated probes, sections A-E) and `probes-carrier`
(the same instructions inside production-size chunks, section H), or
`probes-all` in place of the last two. A row without a carrier run shows no
"in chunks" score, and that column is two thirds of the extraction score.
A session that answers in text instead of calling `submit_benchmark_output`
leaves its task pending and the driver runs another session for it; the
driver reports every session, the row records the tasks that took more
than one, and the page notes "N extra sessions on M tasks". Claude cannot
be pinned (Claude Code exposes no temperature or seed, and the Claude 5.5
API accepts none), so a harness row is one draw: run the suites again and
pass every run to the exporter, which pools them as repeats.

A row produced this way is a **harness track** row. The benchmark cannot
pin the client's temperature, seed or thinking, and the model name is
whatever the client reports. The row records `pins_applied: false` and
`harness: "mcp:<client>"`. Because the client's effort and thinking settings
can move the score as much as the model does, `start_benchmark` takes an
optional `client_settings` object (for example `{"effort": "high",
"thinking": "adaptive", "client_version": "2.3.1"}`, a flat map of at most 20
short values) that is written on the row as `harness_settings` - a row
without them does not say what was measured. Both suites write the same
`model_id` (`mcp/<client>/<model>`), so the leaderboard export joins a
model's probe and chat rows into one leaderboard row with an Overall score.
The leaderboard marks the row `[via MCP client, effort high, thinking
adaptive, ..., pins not applied]` and leaves it out of the pinned-settings
line. Token counts are estimated from the text unless the client reports
them, and latency is not measured, so a harness row shows "—" in the VRAM and
speed columns; it ignores the VRAM filter because the model runs in the
client, not on your GPU.

## What's *not* measured (yet)

- **Factual correctness against a ground truth.** The quality score is
  *intrinsic* — it rewards rich descriptions, well-justified relationships,
  balanced graph topology, and absence of structural noise (hub skew,
  reciprocal duplicates, low-quality items). It does *not* compare extracted
  entities to a curated answer key. A model that confidently produces 100
  well-described, well-justified, but factually wrong entities can score
  well. This is a known limitation; adding gold sets per dataset is
  the v1.5 step.
- **Raw model capability.** The quality score is calculated on the **post-normalized,
  pre-commit** graph — after the import pipeline's deduplication, entity
  normalization, type rescue, evidence validation, and entity cleaning
  (`enable_normalization=True`), which is what a real import commits. This
  reflects what users actually get, but it also means the cleanup pipeline can
  carry a weak model: a model that emits 100 noisy entities (80 discarded by the
  cleaner) can score similarly to one that emits 22 clean ones. A raw-vs-cleaned
  amplification ratio is a v1.5 feature.
- **Variance.** Single shot per `(model, dataset)` at `temperature=0` with
  a fixed seed (the seed is honoured by Ollama; the hosted providers accept
  no seed, so their rows are pinned by temperature alone). Two models
  within a few grade points of each other should be considered tied; if a
  tie matters operationally, re-run those two specifically.
- **Cross-host hardware variance** for local models. Speed numbers depend
  on the host. Cross-host comparisons are advisory; same-host re-runs are
  the trustworthy comparison.

## How the benchmark is configured, and why

Every published number is produced with these settings. They are pinned in
the run itself (each result row records them, and the runner refuses to run
if a pin did not take), so a leaderboard cannot silently mix configurations.

| Setting | Extraction (probes and corpora) | Chat (grounded-chat column) | Why |
|---|---|---|---|
| Thinking / reasoning | **off** | **on** where the model supports it | Extraction is instruct work with a fixed output format; measured 2026-09-23, thinking made it several times slower and produced *fewer* entities because the reasoning consumed the output budget, with no gain on the structured output. Chat is where the product deliberately uses thinking models, so scoring chat with thinking off would measure a configuration nobody runs. |
| Thinking honoured? | probed per model | probed per model | Some models ignore `think=false` (GPT-OSS 20B) or reject `think=true` (GLM4 9B). The runner sends a probe first and marks the row; the leaderboard shows the mark. |
| Temperature | 0.0 | 0.0 | Deterministic decoding; the question is what the model does, not what it can do on a lucky sample. |
| Seed | 42 | 42 | Pinned where the provider supports it (Ollama does). |
| Attempts | single shot | single shot | No retries and no self-correction loop; production gets one answer per chunk too. |
| Context window | the Ollama default for the preset (32k on the reference machine) | same | Enough for the largest production extraction group with room to spare; a smaller window would turn length into a hidden variable. |
| Completion | a probe whose response hit the token limit or the loop detector **fails** | same | A partial answer can outscore a complete one on any additive metric; measured 2026-09-22 on the corpus grade. Rows also carry the counts and the leaderboard warns. |
| Per-model cap | `--model-timeout 5400` (90 min) | same | One hung model must not stall the sweep; the row is recorded as failed and the next model runs. |
| Reference machine | one 32 GB consumer GPU, models as pulled from Ollama | same | Every local model in the table fits this machine; the VRAM column is the model's weights, and the leaderboard's VRAM filter adds headroom for context. |

Change any of these and you are running a different benchmark. That is fine -
the CLI flags let you - but the results should not be compared against the
published table.

## Reproducibility

Every result row pins:

- `benchmark_version` — the harness version (currently `2.0`).
- `dataset_version` — bumps when a dataset's corpus or domain template
  changes.
- `scorer_version` — currently `8`.
- `seed` and `temperature` — deterministic decoding parameters.
- `thinking` and `thinking_honoured` — whether thinking mode was requested,
  and (Ollama only, measured by a probe before the run) whether the model
  actually honoured the request; `null` when it could not be verified.
- `chunks_truncated` and `chunks_aborted_by_loop` — chunks whose LLM call
  ended on `finish_reason == "length"` or was cut short by the stream loop
  detector. The renderer warns on any non-zero count: such a row scored
  incomplete output.
- `config_name` — the named config (e.g. `extraction`, `quick`) that
  produced the row.
- `dataset_source` — `builtin` (ships in the pip package) or `user`
  (user overlay in the data dir).

The leaderboard renderer flags rows with mismatched versions at the top of
the rendered Markdown. Bumping any of these invalidates that row's
comparability with older runs.

## Built-in datasets (v1)

| Dataset | Genre | ~Words | Corpus |
|---|---|---|---|
| `war_and_peace_tiny` | Literary fiction | 1,500 | Tolstoy, *War and Peace* (Project Gutenberg eBook 2600, public domain) |
| `tech_encyclopedia_tiny` | Encyclopedic technical | 1,300 | Original passage on the origins of ARPANET (AGPL-3.0-only) |
| `scientific_methods_tiny` | Scientific methods | 1,300 | Original passage describing soil microbiome profiling — real instruments and software, illustrative study design (AGPL-3.0-only) |

These ship inside the pip package (under `chaoscypher_cli/benchmark/data/datasets/`)
so `pip install chaoscypher-cli` is enough to run the canonical leaderboard.

## Built-in configs

| Config | What it runs |
|---|---|
| `extraction` | Canonical full corpus benchmark: 29 models × 3 datasets (see `chaoscypher benchmark list`). Default when no name is provided. |
| `quick` | 3-model smoke on `war_and_peace_tiny` only; ~3 minutes locally. |
| `full` | Three-stage pipeline benchmark (extraction + embedding retrieval + GraphRAG chat). The canonical example of the `embedders:`/`chats:`/`judge:` config shape used by the v2 chat-eval path. |
| `probes` | Instruction probes through the real extraction pipeline, isolated and at production density; the source of the [leaderboard](/leaderboard)'s extraction column. Same model list as `extraction`. |
| `chat-probes` | Pass/fail grounded-chat probes (no judge model) over one reference graph per corpus, built once by the top extractor; the source of the leaderboard's chat column. Chat runs with thinking on. |
| `workstation` | Large-iron local extraction benchmark (llama3.1:70b ~40 GB, gpt-oss:120b ~80 GB — not pulled by default; 48 GB+ VRAM recommended) plus Opus 4.8 as a quality-ceiling reference, 3 datasets. |

## Running the benchmark

```bash
# Canonical leaderboard (29 models, 3 datasets):
chaoscypher benchmark run

# Smoke test (3 models, 1 dataset):
chaoscypher benchmark run quick

# A user's custom config:
chaoscypher benchmark run my-bench

# Local-only (no API keys required, free):
chaoscypher benchmark run --local-only

# Override one dataset within a config:
chaoscypher benchmark run --dataset war_and_peace_tiny

# Other overrides:
chaoscypher benchmark run --seed 99
chaoscypher benchmark run --temperature 0.3
chaoscypher benchmark run --keep-db          # preserve per-run temp DBs
chaoscypher benchmark run --out ./my-results

# Print a stage-by-stage LLM-call estimate and exit without running:
chaoscypher benchmark run --estimate
chaoscypher benchmark run --rebuild-graphs   # clear the benchmark graph cache first
chaoscypher benchmark run --model-timeout 5400   # cap each model at 90 min; over it = failed row, sweep continues
```

Outputs land in `<chaoscypher_data_dir>/benchmark/results/` by default
(`~/AppData/Local/chaoscypher/benchmark/results/` on Windows;
`~/.local/share/chaoscypher/benchmark/results/` on Linux). Override with `--out`.

To re-render an old result without re-running:

```bash
chaoscypher benchmark show <results.json>
```

To list available configs and datasets:

```bash
chaoscypher benchmark list
```

## Adding your own dataset (user overlay)

Datasets you add live in your data dir; they're automatically discovered
on the next `chaoscypher benchmark list` or `bench run`.

```
<chaoscypher_data_dir>/benchmark/datasets/
  my_internal_docs/
    manifest.yaml
    my_internal_docs.txt
```

`manifest.yaml` shape:

```yaml
id: my_internal_docs       # must match the directory name
kind: extraction
version: "1.0"
packs: [technical]         # the domain packs added to the floor; [] for the floor alone
corpus_path: my_internal_docs.txt   # sibling-relative, just the filename
description: "What's special about this corpus."
```

`packs` lists the domain packs the corpus is extracted with, on the floor
(the core types and labels) every extraction gets. The older
`domain: technical` is still read, as a one-element list, and
`domain: generic` means the floor alone. Result rows and reference packs
record the packs in their `extraction_domain` field: one pack's name, several
sorted and joined with `+` (`historical+literary`), `generic` for the floor
alone. Until sources store a list of packs, an extraction runs with one pack
or none; a dataset that lists two is refused when it runs.

A user dataset with the same id as a built-in **overrides** the built-in
(useful for swapping a corpus while keeping the metadata structure). The
leaderboard renderer surfaces a note when a run includes user-overlay
datasets so reviewers know what's reproducible from the pip package alone.

## Adding your own config

Easiest path:

```bash
chaoscypher benchmark init my-bench     # scaffolds <data_dir>/benchmark/config/my-bench.yaml
```

Then edit the file. Run with `chaoscypher benchmark run my-bench`.

Raw config shape:

```yaml
name: "My Custom Benchmark"
description: "Internal eval against the docs corpus"

seed: 42
temperature: 0.0

datasets:
  - my_internal_docs              # any built-in or user dataset id

extractors:
  - provider: ollama
    model: llama3.1:8b
    label: "Llama 3.1 8B (local)"
  - provider: openai
    model: gpt-4o
    label: "GPT-4o"
```

For an extraction-only benchmark, `extractors:` is the only role list you
need. The parser also accepts `embedders:`, `chats:`, and `judge:` for the
full-pipeline path — see the built-in `full` config for that
shape. If you set `embedders:` or `chats:`, you must also set `extractors:`;
`chats:` without a `judge:` scores the chat stage with the grounded-chat
probes instead of a judge model. Each model entry
optionally takes `kinds: [extraction]` to scope it to specific dataset kinds.

Full-pipeline configs can additionally set `defaults:` and `weights:`
(see [Composite "Overall" column](#composite-overall-column)):

```yaml
# Pinned embedder/chat held fixed when attributing retrieval/chat scores
# back to an extractor in the composite Overall column.
defaults:
  embedder: "ollama/qwen3-embedding:4b"
  chat: "ollama/qwen3:14b"

# Per-dimension Overall weights (defaults shown).
weights:
  extraction: 0.40
  retrieval: 0.20
  chat: 0.20
  speed: 0.10
  cost: 0.10
```

## Model registry

The model registry at
`packages/cli/src/chaoscypher_cli/benchmark/data/models_registry.yaml` is the
single source of truth for benchmark model metadata, keyed by
`<provider>/<model>`. Each entry carries:

- `provider`, `model`, `label` — identity and display name.
- `tier` — `frontier` / `mid` / `small`.
- `released`, `context`, `open_weight`, `license` — provenance metadata.
- `price: { input, output }` — USD per 1M tokens, with `price_dated:`
  recording when the price was last verified. Local open-weight models cost
  $0 and may omit the price block.
- `vram_gb` — approximate VRAM footprint for local models.
- `tools` — whether `ollama show <model>` lists `tools` under Capabilities; the app's chat needs it.
- `why` / `notes` — inclusion rationale and free-text caveats.

Commercial models you add to a config also need a registry entry with a
`price:` block (and a dated `price_dated:` for provenance) so their runs can
report cost. Run `make benchmark-cards` to regenerate the public model-cards
page after edits.

## Known limitations

- **Temp databases are removed by default.** Each `(model, dataset)` run
  creates a database under the user data directory and removes it when
  the run finishes. v7 metrics live in the result row's JSON, so losing
  the DB does not lose the score breakdown. Pass `--keep-db` to preserve
  for post-hoc inspection.
- **Per-chunk latency is approximated** by dividing total wall-clock by
  chunk count (`SourcePipeline` does not expose per-chunk timings).
- **`temperature=0` on weak Ollama models** can cause degenerate
  repetition or JSON refusal. Per-provider temperature defaults can be
  added to the runner config if this bites in practice; for now a model
  hitting this falls into the `did not complete` section.

## Roadmap

- **v1.5** — Add ground-truth answer keys (gold sets) to existing datasets;
  introduce a `V7PlusGoldScorer` that combines intrinsic + precision/recall.
  Also: capture raw (pre-cleanup) entity counts so the leaderboard can
  surface a cleanup-amplification ratio per model — letting users see
  which models lean heavily on the post-processing pipeline vs. which
  produce clean output natively.
- **v2 — shipped.** Chat evaluation is live via the full-pipeline path
  (`chaoscypher benchmark run full`): datasets carry labeled query sets and
  an LLM-as-judge produces faithfulness + correctness scores. Instead of the
  originally planned separate ranking, the chat stage rolls into the
  composite **Overall** column of the unified leaderboard.
- **v2.5** — Live web leaderboard (static-site generator over
  `benchmark/results/`).

## License

Built-in dataset corpora are public domain or AGPL-3.0-only as marked in
each `manifest.yaml`'s `source` field. Harness code is AGPL-3.0-only,
matching the rest of ChaosCypher.
