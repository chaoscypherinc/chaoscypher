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

- **Quality** — the v7 quality grade (0–100) from
  `chaoscypher_core.services.quality.QualityScorer`, the same scorer
  ChaosCypher uses in production. The headline number per model is the
  unweighted mean across the datasets the model succeeded on.
- **Speed** — median LLM latency per chunk in milliseconds.
- **Cost** — USD cost for the run. Local Ollama models cost $0; commercial
  models look up `(provider, model)` in the dated price registry shipped
  with the harness.

## Full-pipeline scoring (v2)

`chaoscypher benchmark run full` runs three stages and scores each:

- **Extraction** — the v7 quality grade described above.
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
| Extraction | 0.40 | v7 quality grade (mean across datasets) |
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

## What's *not* measured (yet)

- **Factual correctness against a ground truth.** The v7 score is
  *intrinsic* — it rewards rich descriptions, well-justified relationships,
  balanced graph topology, and absence of structural noise (hub skew,
  reciprocal duplicates, low-quality items). It does *not* compare extracted
  entities to a curated answer key. A model that confidently produces 100
  well-described, well-justified, but factually wrong entities can score
  well. This is a known limitation; adding gold sets per dataset is
  the v1.5 step.
- **Raw model capability.** The v7 score is calculated on the **post-normalized,
  pre-commit** graph — after the import pipeline's deduplication, entity
  normalization, type rescue, evidence validation, and entity cleaning
  (`enable_normalization=True`), which is what a real import commits. This
  reflects what users actually get, but it also means the cleanup pipeline can
  carry a weak model: a model that emits 100 noisy entities (80 discarded by the
  cleaner) can score similarly to one that emits 22 clean ones. A raw-vs-cleaned
  amplification ratio is a v1.5 feature.
- **Variance.** Single shot per `(model, dataset)` at `temperature=0` with
  a fixed seed. Two models within a few grade points of each other should
  be considered tied; if a tie matters operationally, re-run those two
  specifically.
- **Cross-host hardware variance** for local models. Speed numbers depend
  on the host. Cross-host comparisons are advisory; same-host re-runs are
  the trustworthy comparison.

## Reproducibility

Every result row pins:

- `benchmark_version` — the harness version (currently `2.0`).
- `dataset_version` — bumps when a dataset's corpus or domain template
  changes.
- `scorer_version` — currently `7`.
- `seed` and `temperature` — deterministic decoding parameters.
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
| `extraction` | Canonical full leaderboard: 14 models × 3 datasets (see `chaoscypher benchmark list`). Default when no name is provided. |
| `quick` | 3-model smoke on `war_and_peace_tiny` only; ~3 minutes locally. |
| `full` | Three-stage pipeline benchmark (extraction + embedding retrieval + GraphRAG chat). The canonical example of the `embedders:`/`chats:`/`judge:` config shape used by the v2 chat-eval path. |
| `workstation` | Large-iron local extraction benchmark (llama3.1:70b ~40 GB, gpt-oss:120b ~80 GB — not pulled by default; 48 GB+ VRAM recommended) plus Opus 4.8 as a quality-ceiling reference, 3 datasets. |

## Running the benchmark

```bash
# Canonical leaderboard (14 models, 3 datasets):
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
domain: technical          # see chaoscypher_core domains
corpus_path: my_internal_docs.txt   # sibling-relative, just the filename
description: "What's special about this corpus."
```

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
if you set `chats:`, you must also set a `judge:`. Each model entry
optionally takes `kinds: [extraction]` to scope it to specific dataset kinds.

Full-pipeline configs can additionally set `defaults:` and `weights:`
(see [Composite "Overall" column](#composite-overall-column)):

```yaml
# Pinned embedder/chat held fixed when attributing retrieval/chat scores
# back to an extractor in the composite Overall column.
defaults:
  embedder: "ollama/qwen3-embedding:8b"
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
