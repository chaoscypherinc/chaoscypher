---
title: Benchmark Commands
description: Run reproducible extraction benchmarks across LLM models and datasets with chaoscypher benchmark — compare models on a leaderboard, fully local with --local-only.
---

# Benchmark Commands

The `benchmark` command group runs reproducible extraction benchmarks across
LLM models and datasets, then renders a leaderboard. Each run is fully
local — no API key is required when `--local-only` is set.

```bash
chaoscypher benchmark --help
```

A benchmark **config** names the seed, temperature, datasets, and models for a
run. Configs ship as built-ins and can be
overlaid with user configs at `<data_dir>/benchmark/config/<name>.yaml`.
Datasets are referenced by id and discovered from both built-in locations and
`<data_dir>/benchmark/datasets/`.

Built-in configs:

- `extraction` — canonical multi-genre extraction leaderboard (the default).
- `quick` — 3-model smoke test on a single dataset.
- `full` — three-stage pipeline benchmark (extraction + embedding retrieval +
  GraphRAG chat with an LLM judge).
- `workstation` — large-iron local extraction benchmark (`llama3.1:70b` ~40 GB,
  `gpt-oss:120b` ~80 GB, `qwen3.6:35b-a3b`) plus Claude Opus 4.8 as a
  quality-ceiling reference. The large models are **not pulled automatically**;
  48 GB+ VRAM is recommended to run the full set without offloading.

---

## Run a Benchmark

Execute a named config end-to-end:

```bash
# Run the default config (`extraction`)
chaoscypher benchmark run

# Run a specific config
chaoscypher benchmark run quick

# Run a config but only on one dataset
chaoscypher benchmark run extraction --dataset war_and_peace_tiny

# Run with no commercial models — purely local Ollama
chaoscypher benchmark run extraction --local-only

# Override seed / temperature for ad-hoc comparisons
chaoscypher benchmark run extraction --seed 17 --temperature 0.2

# Preserve the per-run temp DBs for post-hoc inspection
chaoscypher benchmark run extraction --keep-db

# Write outputs somewhere specific (default: <data_dir>/benchmark/results/)
chaoscypher benchmark run extraction --out ./bench-out
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `NAME` | Config name. Optional — defaults to `extraction`. |

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--dataset ID` | string | All datasets in config | Run only one dataset id from the config's `datasets` list. |
| `--local-only` | flag | off | Drop commercial-provider models so the run is free / API-key-free. |
| `--seed N` | int | Config value | Override the config's seed. |
| `--temperature F` | float | Config value | Override the config's temperature. |
| `--keep-db` | flag | off | Preserve per-run temp databases for inspection. |
| `--out DIR` | path | `<data_dir>/benchmark/results/` | Output directory for JSON and Markdown. |
| `--estimate` | flag | off | Print the LLM-call estimate and exit without running. |
| `--rebuild-graphs` | flag | off | Clear the benchmark graph cache before running. |

**Output:**

A run writes three files into the output directory:

- `<timestamp>.json` — machine-readable result rows
- `<timestamp>.md` — rendered Markdown leaderboard
- `latest.md` — overwritten on every run for quick `cat`/preview

---

## List Configs and Datasets

Show every config the runner recognizes (built-in plus any user overlay), and
every dataset configs can reference:

```bash
chaoscypher benchmark list
```

The output is two tables:

- **Configs** — name, source (`builtin` / `user`), description.
- **Datasets** — id, source, kind, version, domain, corpus filename.

Run this before `benchmark run` to see what's available, and after `benchmark
init` to confirm the new user config is picked up.

---

## Show a Saved Leaderboard

Re-render an existing results JSON as a Markdown leaderboard, without re-running
the benchmark:

```bash
# Print to stdout
chaoscypher benchmark show ./bench-out/2026-04-29T1530Z.json

# Write to a file
chaoscypher benchmark show ./bench-out/2026-04-29T1530Z.json --out leaderboard.md
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `RESULTS_PATH` | Path to a results JSON file produced by `benchmark run`. |

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--out PATH` | path | stdout | Write the rendered Markdown to this path instead of stdout. |

---

## Scaffold a User Config

Drop a starter config under `<data_dir>/benchmark/config/<name>.yaml` for local
editing. After init, `benchmark run <name>` loads the user config (which
overrides any built-in with the same name).

```bash
# Create a new config named "smoke"
chaoscypher benchmark init smoke

# Overwrite an existing user config
chaoscypher benchmark init smoke --force
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `NAME` | Config name. Becomes the file name and the value of the `name:` field. |

**Options:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--force` | flag | off | Overwrite an existing user config with the same name. |

The starter config references the `war_and_peace_tiny` built-in dataset and a
single Ollama model, so the resulting `benchmark run <name>` runs end-to-end
without further setup.

---

## Fixture Authoring (advanced)

The `benchmark fixture` subgroup holds fixture-authoring helpers for benchmark
datasets — primarily `benchmark fixture validate <dataset_id>`, which checks
that every in-scope labeled query's gold entities resolve against the dataset's
live entities. This is an advanced workflow for contributors building or
curating benchmark fixtures, not part of a normal benchmarking run.

`validate` builds a reference graph before resolving gold entities; the
`--canonical-extractor PROVIDER/MODEL` option (default: `ollama/llama3.1:8b`)
selects the provider/model used to build that reference graph.

```bash
chaoscypher benchmark fixture --help

# Validate against the default reference extractor
chaoscypher benchmark fixture validate <dataset_id>

# Validate against a different reference extractor
chaoscypher benchmark fixture validate <dataset_id> --canonical-extractor ollama/qwen3:14b
```

---

## Workflow

A typical benchmarking session:

```bash
# 1. See what's already available
chaoscypher benchmark list

# 2. Smoke-test the runner on a small built-in config
chaoscypher benchmark run quick --local-only

# 3. Scaffold a user config for a custom comparison
chaoscypher benchmark init my-comparison

# 4. Edit <data_dir>/benchmark/config/my-comparison.yaml,
#    add datasets / models, then run it
chaoscypher benchmark run my-comparison --local-only

# 5. Re-render the saved leaderboard later without re-running
chaoscypher benchmark show <data_dir>/benchmark/results/<timestamp>.json
```
