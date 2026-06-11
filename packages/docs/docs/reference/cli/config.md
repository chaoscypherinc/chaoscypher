---
title: CLI Configuration
description: Manage chaoscypher CLI configuration — run the setup wizard to configure your LLM provider, or update connection settings directly with chaoscypher config.
---

# Configuration

The `config` and `setup` commands manage Chaos Cypher configuration. Both read and write the unified `settings.yaml` — the same file the web UI **Settings** page edits.

## Setup Wizard

First-time configuration wizard that guides you through LLM provider setup for entity extraction and chat.

```bash
chaoscypher setup
```

| Option | Description |
|--------|-------------|
| `--provider, -p {ollama,openai,anthropic,gemini}` | LLM provider (skip selection prompt) |
| `--vram INTEGER` | VRAM size in GB for Ollama presets |
| `--non-interactive` | Non-interactive mode (for CI/scripts) |
| `--test/--no-test` | Test provider connectivity (default: `--test`) |
| `--force, -f` | Reconfigure even if already configured |

### Example: Interactive Setup with Ollama

```console
chaoscypher setup
╭──────────────────────────────────────────╮
│ Configure LLM for entity extraction      │
╰──────────────────────────────────────────╯

Choose LLM Provider:

  [1]  Ollama          Local LLM - Free, private, no API key required
  [2]  OpenAI          GPT-4o - Cloud-based, requires API key
  [3]  Anthropic       Claude - Cloud-based, requires API key
  [4]  Google Gemini   Gemini Pro - Cloud-based, requires API key

Select provider [1/2/3/4/q] (1): 1
Ollama URL (http://localhost:11434):
Testing connection... Connected successfully

How much GPU VRAM do you have?

  [1]  16GB  (RTX 4080, 5080)        → phi4:14b
  [2]  20GB  (RTX A4000, A4500)      → phi4:14b
  [3]  24GB  (RTX 4090, 3090)        → qwen3:30b
  [4]  32GB  (RTX 5090)              → qwen3:30b
  [5]  48GB  (A6000, 2x 4090)        → qwen3:30b
  [6]  96GB  (RTX 6000 Pro)          → gpt-oss:120b
  [7]  128GB (DGX Spark, Ryzen AI Max+ 395) → gpt-oss:120b
  [8]  Custom                        I'll specify models manually

Select VRAM tier [1/2/3/4/5/6/7/8] (3): 3
Applying 24GB VRAM preset...
  Chat model: qwen3:30b
  Extraction model: qwen3:30b-instruct
  Vision model: qwen3-vl:30b
  Context window: 16384

╭─ ✓ Configuration Complete ───────────────╮
│ Provider       ollama                     │
│ URL            http://localhost:11434      │
│ Chat Model     qwen3:30b                  │
│ Extraction     qwen3:30b-instruct         │
│ Context Window 16384                      │
│ Settings File  ~/.local/share/chaos.../settings.yaml │
╰──────────────────────────────────────────╯

Next steps:
  chaoscypher source add document.pdf  # Process a document
  chaoscypher chat                     # Start interactive chat
```

:::note[Embeddings]

The default embedding provider runs locally on the CPU using sentence-transformers and does not require API keys. Alternative providers (Ollama, OpenAI, Gemini) are also available. The embedding model is configured via `embedding.model` in [`settings.yaml`](../../getting-started/configuration.md).

:::

:::info[Where setup saves your settings]

`chaoscypher setup` writes your configuration — LLM provider, models, API keys, and the embedding settings — into `settings.yaml` in the [data directory](#where-configuration-lives). This is the **same file** the web UI **Settings** page edits and the **same file** the `config` commands on this page manage, so the CLI and the web app always read one config.

If you are upgrading from a pre-unification version (where some of this lived in `cli.yaml`), `cli.yaml` is no longer read at all — it is silently ignored with a one-line stderr notice. Run `chaoscypher setup` once to write a fresh `settings.yaml`, then delete the stale `cli.yaml`.

**In plain terms:** run `chaoscypher setup` once after upgrading, and your CLI and web app will share one config file.

:::

### Quick Setup Examples

```bash
# Ollama with 24GB VRAM preset (skip menus)
chaoscypher setup --provider ollama --vram 24

# OpenAI (reads OPENAI_API_KEY from environment)
chaoscypher setup --provider openai

# Non-interactive for CI (auto-detects provider from env vars)
chaoscypher setup --non-interactive

# Re-run setup after initial configuration
chaoscypher setup --force
```

### Providers

| Provider | Requirements |
|----------|-------------|
| **Ollama** | Local installation, no API key needed |
| **OpenAI** | `OPENAI_API_KEY` environment variable |
| **Anthropic** | `ANTHROPIC_API_KEY` environment variable |
| **Gemini** | `GEMINI_API_KEY` environment variable |

### Ollama VRAM Presets

When using Ollama, specify `--vram` to auto-configure optimal models for your hardware.

| VRAM | GPUs | Chat Model |
|------|------|------------|
| 16 GB | RTX 4080, 5080 | `phi4:14b` |
| 20 GB | RTX A4000, A4500 | `phi4:14b` |
| 24 GB | RTX 4090, 3090 | `qwen3:30b` |
| 32 GB | RTX 5090 | `qwen3:30b` |
| 48 GB | A6000, 2x 4090 | `qwen3:30b` |
| 96 GB | RTX 6000 Pro | `gpt-oss:120b` |
| 128 GB | DGX Spark, Ryzen AI Max+ 395 | `gpt-oss:120b` |

## Config Commands

### Show Configuration

Displays the effective configuration — code defaults, `settings.yaml` values, and any `CHAOSCYPHER_*` environment overrides, all merged. Secret-bearing fields (API keys, the Lexicon token) are masked.

```bash
chaoscypher config show
```

| Option | Description |
|--------|-------------|
| `--format, -f {tree,json,yaml}` | Output format (default: `tree`) |

#### Tree format (default)

The tree view summarizes the most-used groups. The footer reports the `settings.yaml` location and whether it exists yet.

```console
chaoscypher config show
Configuration
├── lexicon
│   ├── url: https://lexicon.chaoscypher.com
│   ├── timeout: 30
│   ├── max_retries: 4
│   └── token: not set
├── llm
│   └── chat_provider: ollama
├── embedding
│   ├── provider: local
│   └── model: Qwen/Qwen3-Embedding-0.6B
├── paths
│   ├── data_dir: /home/user/.local/share/chaoscypher
│   ├── config_dir: /home/user/.config/chaoscypher
│   └── cache_dir: /home/user/.cache/chaoscypher
└── current_database: default

Config file: /home/user/.local/share/chaoscypher/settings.yaml
Status: exists
```

:::note

`config show` reads the unified `settings.yaml` — the **same file** the web UI **Settings** page edits and that [`chaoscypher setup`](#setup-wizard) writes. `current_database` is shown here for reference but is changed with [`chaoscypher db switch`](database.md#switch-database), not `config set`. There is no separate `cli.yaml`.

:::

#### JSON format

The `json` and `yaml` formats emit the **full** effective settings (every group), not just the tree summary — useful for piping into other tools. Configured secrets render as `"configured"`; unset secrets render as `null`.

```console
chaoscypher config show --format json
{
  "lexicon": {
    "url": "https://lexicon.chaoscypher.com",
    "timeout": 30,
    "max_retries": 4,
    "token": null
  },
  "llm": {
    "chat_provider": "ollama",
    "openai_api_key": null
  },
  "embedding": {
    "provider": "local",
    "model": "Qwen/Qwen3-Embedding-0.6B"
  },
  "paths": {
    "data_dir": "/home/user/.local/share/chaoscypher",
    "config_dir": "/home/user/.config/chaoscypher",
    "cache_dir": "/home/user/.cache/chaoscypher"
  },
  "current_database": "default"
}
```

(Output abbreviated — the real dump includes every backend settings group.)

#### YAML format

```console
chaoscypher config show --format yaml
lexicon:
  url: https://lexicon.chaoscypher.com
  timeout: 30
  max_retries: 4
  token: null
llm:
  chat_provider: ollama
embedding:
  provider: local
  model: Qwen/Qwen3-Embedding-0.6B
current_database: default
```

:::note

YAML output requires PyYAML. If not installed, the command falls back to JSON format automatically.

:::

### Get a Value

Retrieve a specific configuration value using dot-separated paths.

```bash
chaoscypher config get KEY
```

Use dot-separated paths for nested values (e.g., `lexicon.url`, `llm.chat_provider`, `paths.data_dir`).

```console
chaoscypher config get lexicon.url
https://lexicon.chaoscypher.com

chaoscypher config get llm.chat_provider
ollama

chaoscypher config get paths.data_dir
/home/user/.local/share/chaoscypher
```

Secret-bearing fields are never shown in plaintext. A configured secret reads as `configured`; an unset one reads as `not set`:

```console
chaoscypher config get lexicon.token
not set

chaoscypher config get llm.openai_api_key
configured
```

When the key points to a group, the full subtree is returned as JSON:

```console
chaoscypher config get lexicon
{
  "url": "https://lexicon.chaoscypher.com",
  "timeout": 30,
  "max_retries": 4,
  "token": null
}
```

If the key does not exist, the command prints an error and exits non-zero:

```console
chaoscypher config get nonexistent.key
Key not found: nonexistent.key
```

### Set a Value

Set a configuration value. The change is validated against the settings schema and written atomically to `settings.yaml`.

```bash
chaoscypher config set KEY VALUE
```

Values are automatically converted to the appropriate type (boolean, integer, float, or string):

```console
chaoscypher config set lexicon.timeout 60
Set lexicon.timeout = 60
Saved to: /home/user/.local/share/chaoscypher/settings.yaml
```

Type conversion happens automatically:

| Input | Converted Type | Converted Value |
|-------|---------------|-----------------|
| `"true"`, `"yes"`, `"on"` | `bool` | `True` |
| `"false"`, `"no"`, `"off"` | `bool` | `False` |
| `"42"` | `int` | `42` |
| `"3.14"` | `float` | `3.14` |
| `"qwen3:30b"` | `str` | `"qwen3:30b"` |

```console
chaoscypher config set llm.chat_provider ollama
Set llm.chat_provider = ollama
Saved to: /home/user/.local/share/chaoscypher/settings.yaml
```

Because writes go through the same validated path the web **Settings** page uses, an out-of-range or unknown key is rejected before anything is saved:

```console
chaoscypher config set lexicon.timeout 1
Error: Invalid setting 'lexicon.timeout':
...
```

:::warning[Changing the active database]

`current_database` is **not** editable with `config set` — it is managed by [`chaoscypher db switch <name>`](database.md#switch-database), which also validates that the database exists. Attempting to set it directly prints:

```console
chaoscypher config set current_database research-2026
Error: current_database is managed by `chaoscypher db switch <name>`
       (which validates the database exists).
```

:::

:::tip[Editing API keys and providers]

For interactive provider/model/API-key setup, [`chaoscypher setup`](#setup-wizard) is usually easier than individual `config set` calls — but both write the same `settings.yaml`.

:::

### Edit Config File

Open `settings.yaml` in your default editor. If the file does not exist yet, it is created from defaults first. When the editor closes, the file is re-validated so any syntax or value error surfaces immediately.

```bash
chaoscypher config edit
```

Uses `$EDITOR` or `$VISUAL` environment variable. Falls back to `nano` on Linux/macOS and `notepad` on Windows.

### Show Config Path

Display the path to the `settings.yaml` configuration file.

```bash
chaoscypher config path
```

```console
chaoscypher config path
/home/user/.local/share/chaoscypher/settings.yaml
Status: exists
```

If the config file has not been created yet:

```console
chaoscypher config path
/home/user/.local/share/chaoscypher/settings.yaml
Status: not created yet
```

### Reset to Defaults

Remove operator overrides from `settings.yaml`, restoring the code defaults.

```bash
chaoscypher config reset
```

| Option | Description |
|--------|-------------|
| `--force, -f` | Skip confirmation prompt |

```console
chaoscypher config reset
Reset /home/user/.local/share/chaoscypher/settings.yaml to defaults? [y/N]: y
Configuration reset to defaults.
Settings file: /home/user/.local/share/chaoscypher/settings.yaml
```

## Where Configuration Lives

As of the 2026-06 config unification, **all** configuration — engine and client alike — lives in a single `settings.yaml`:

| File | Holds | Edited by |
|------|-------|-----------|
| `settings.yaml` | Everything — `llm`, `embedding`, `lexicon`, `current_database`, and every other backend group | `chaoscypher config set` / `config edit`, `chaoscypher setup`, `chaoscypher db switch`, and the web UI **Settings** page |

`settings.yaml` lives inside the **data directory**, so the CLI, the workers, and the web app all read one file. The location depends on your platform:

| Platform | Data directory (`settings.yaml` path) |
|----------|---------------------------------------|
| **Linux** | `~/.local/share/chaoscypher/settings.yaml` |
| **macOS** | `~/Library/Application Support/chaoscypher/settings.yaml` |
| **Windows** | `%LOCALAPPDATA%\chaoscypher\settings.yaml` |
| **Docker** | `/data/settings.yaml` (persisted via volume mount) |

The location can be overridden with the `CHAOSCYPHER_DATA_DIR` environment variable. A minimal `settings.yaml` written by `chaoscypher setup` looks like:

```yaml
# settings.yaml — unified configuration (shared with the web UI)
current_database: default

lexicon:
  url: https://lexicon.chaoscypher.com
  timeout: 30

llm:
  chat_provider: ollama
  ollama_chat_model: qwen3:30b
  ollama_extraction_model: qwen3:30b-instruct

embedding:
  provider: local
  model: Qwen/Qwen3-Embedding-0.6B
```

See the [Configuration guide](../../getting-started/configuration.md) for the full list of settings groups. Use `chaoscypher config set` (or `chaoscypher setup`) to change LLM/embedding/lexicon settings and `chaoscypher db switch <name>` to change `current_database`.

:::tip[Upgrading from a pre-unification version]

Older releases kept client settings in a separate `cli.yaml` in the config directory. **`cli.yaml` is no longer read at all.** A leftover file is silently ignored — the CLI prints a single dim notice on startup so you know it is dead:

```text
chaoscypher: note: ~/.config/chaoscypher/cli.yaml is no longer read and is
ignored (config unification); your settings live in settings.yaml — the old
file can be deleted.
```

Run `chaoscypher setup` once (or set the values you need with `config set`), then delete the stale `cli.yaml`. `chaoscypher doctor` also flags it as safe to delete.

:::

**In short:** one file — `settings.yaml` in the data directory — now holds your entire configuration. The old `cli.yaml` is retired and can be deleted.

## Configuration Hierarchy

Settings are resolved in the following order (later sources override earlier ones):

1. **Built-in defaults** -- hardcoded in the application
2. **Environment-variable fallbacks** -- well-known names like `LEXICON_URL` and `OPENAI_API_KEY` supply the default when the key is absent from `settings.yaml`
3. **`settings.yaml`** -- the unified config file in the data directory; an explicit file value wins over the env-var fallback

Two kinds of variables sit outside this order: `CHAOSCYPHER_DATABASE` genuinely overrides `current_database` when the CLI resolves which database to use, and the `CHAOSCYPHER_*_DIR` path variables effectively override everything because they decide which `settings.yaml` is loaded in the first place.

## Environment Variables

Most of these variables act as **fallback defaults** — they apply only when the key is absent from `settings.yaml`, and an explicit file value wins. None of them rewrite the file, and they are honored everywhere settings are read (CLI, workers, web backend).

| Variable | Applies to | Behavior |
|----------|-----------|----------|
| `LEXICON_URL` | `lexicon.url` | Fallback default |
| `CHAOSCYPHER_LEXICON_TIMEOUT` | `lexicon.timeout` | Fallback default |
| `CHAOSCYPHER_LLM_PROVIDER` | `llm.chat_provider` | Fallback default |
| `OPENAI_API_KEY` | `llm.openai_api_key` | Fallback default |
| `ANTHROPIC_API_KEY` | `llm.anthropic_api_key` | Fallback default |
| `GEMINI_API_KEY` | `llm.gemini_api_key` | Fallback default |
| `CHAOSCYPHER_DATABASE` | `current_database` | **True override** — outranks `settings.yaml` in CLI [database selection](index.md#database-selection) |
| `CHAOSCYPHER_DATA_DIR` | `paths.data_dir` | **True override** — decides which `settings.yaml` is loaded |
| `CHAOSCYPHER_CONFIG_DIR` | `paths.config_dir` | **True override** — decides where config-directory files live |
| `CHAOSCYPHER_CACHE_DIR` | `paths.cache_dir` | **True override** |

A handful of operational variables used in container deployments (`QUEUE_HOST`/`QUEUE_PORT`/`QUEUE_DB`/`QUEUE_PASSWORD`, `CHAOSCYPHER_EDGE_AUTH_TOKEN`, `CHAOSCYPHER_ALLOWED_HOSTS`, `SUPERVISOR_PASSWORD`) also genuinely override their settings; see the [Configuration guide](../../getting-started/configuration.md) for those.

### Color output

There is no color setting in `settings.yaml`. Output coloring follows the standard [`NO_COLOR`](https://no-color.org/) convention natively — set `NO_COLOR` (to any value) to disable ANSI colors. Per-command `--format` flags control table/JSON output and are unaffected by configuration.
