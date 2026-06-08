---
id: installation
title: Installation
description: Install Chaos Cypher via Docker — all-in-one container or multi-container dev stack. Includes prerequisites, LLM provider setup, and first-run verification steps.
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# Installation

## Prerequisites

| Requirement | Version | Purpose |
|-------------|---------|---------|
| Docker | Latest | Container orchestration |
| Make | Any | Build commands (optional) |
| uv | 0.11+ | Python dependency management (contributors only — end users only need Docker) |

You also need an LLM provider. [Ollama](https://ollama.com/) is recommended for fully local operation.

Install uv via the [official installer](https://docs.astral.sh/uv/getting-started/installation/) — `curl -LsSf https://astral.sh/uv/install.sh | sh` on macOS/Linux, or the equivalent PowerShell command on Windows.

## Docker All-in-One (Recommended)

The fastest way to get Chaos Cypher running. A single container with all services bundled.

### Run the published image (recommended)

The launch install path is the all-in-one image published to the GitHub
Container Registry:

```bash
docker run -d --name chaoscypher \
  -p 80:80 \
  -p 443:443 \
  -v chaoscypher-data:/data \
  ghcr.io/chaoscypherinc/chaoscypher:latest
```

Port `443` is published so HTTPS keeps working if you later enable TLS under
**Settings → General → TLS / HTTPS**; until then only `80` is served.

Prefer Compose? Save this as `docker-compose.yml` and run `docker compose up -d`:

```yaml
name: chaoscypher
services:
  chaoscypher:
    image: ghcr.io/chaoscypherinc/chaoscypher:latest
    container_name: chaoscypher
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - chaoscypher-data:/data
    restart: unless-stopped
volumes:
  chaoscypher-data:
```

:::note[Image publication]

The image is built and pushed by the
[`publish-ghcr.yml`](https://github.com/chaoscypherinc/chaoscypher/blob/main/.github/workflows/publish-ghcr.yml)
workflow. It is the launch install path: if `ghcr.io/chaoscypherinc/chaoscypher`
does not pull yet, the publish workflow has not been run — use **Build from
source** below in the meantime.

:::

### Build from source (alternative / development)

No published image required — clone the repository and build the all-in-one
image locally:

```bash
git clone https://github.com/chaoscypherinc/chaoscypher.git
cd chaoscypher
make docker-up
```

`make docker-up` builds the all-in-one image from `packages/docker/Dockerfile`
and starts it via `docker compose`.

This starts everything at [http://localhost](http://localhost):

| Port | Service |
|------|---------|
| 80 | Web UI + REST API (via Nginx) |
| 443 | HTTPS (optional, requires TLS certs) |

### Startup

While services initialize, the browser shows a **startup page** with live component health indicators and a real-time log viewer. Once all services are ready, the page automatically redirects to the application.

If you encounter an error, custom error pages provide contextual messages and a link to report issues on GitHub with pre-filled diagnostic information.

### Verify

Open [http://localhost](http://localhost) in your browser. You should see the startup page briefly, then the Chaos Cypher interface.

Check the API is responding:

```bash
curl http://localhost/api/v1/health
```

## Multi-Container (Development)

For contributors working from an approved development checkout who need hot-reload and per-service logs. Requires Python 3.14+ and Node.js 22+.

```bash
cd chaoscypher
make install
make docker-dev
```

`make install` handles:

- Installing Python packages for all backend packages (core, cortex, neuron, cli)
- Installing Node.js dependencies for the interface
- Setting up pre-commit hooks
- Building the Docker test image

This starts separate containers:

| Service | URL | Description |
|---------|-----|-------------|
| Interface | [http://localhost:3000](http://localhost:3000) | Web UI (Vite HMR) |
| Cortex API | [http://localhost:8080/api/v1](http://localhost:8080/api/v1) | REST API (auto-restart) |
| Queue Monitor | [http://localhost:3000/queues](http://localhost:3000/queues) | Job queue dashboard |
| Valkey | Internal only | Queue backend |

## Local Development (without Docker services)

Start services individually after `make install`:

<Tabs>
<TabItem value="backend-api" label="Backend API">


```bash
cc-cortex start
```

</TabItem>
<TabItem value="workers" label="Workers">


```bash
cc-neuron
```

</TabItem>
<TabItem value="frontend" label="Frontend">


```bash
cd packages/interface
npm run dev
```

</TabItem>
</Tabs>


:::note[Valkey required]

The worker (Neuron) requires Valkey. Start it separately:
```bash
docker run -d -p 6379:6379 valkey/valkey:8-alpine
```

:::

## CLI Only

If you only need the command-line interface:

```bash
pip install chaoscypher-cli
```

Verify:

```bash
chaoscypher --help
```

## LLM Provider Setup

Chaos Cypher requires an LLM provider for chat and entity extraction. Embeddings are generated locally on the CPU and do not require an LLM provider.

<Tabs>
<TabItem value="ollama-local" label="Ollama (Local)">


Install [Ollama](https://ollama.com/) and pull the default models:

```bash
ollama pull qwen3:30b-instruct
```

No additional configuration needed — Ollama is the default provider.

:::warning[This pull is the longest part of setup]

`qwen3:30b-instruct` is a large model — roughly **18–20 GB** at the default quantization (exact size depends on the quant). This download is the single biggest time and disk cost of the install, so start it **in parallel** with the container build/startup to save time.

The pull must finish before entity extraction and chat will work. Because extraction runs automatically on your first upload (`auto_extract_entities` defaults to on), an upload will appear stuck in the **extracting** stage until the model has finished downloading.

For a faster first run, you can point the `ollama_chat_model` setting at a smaller model (e.g. a smaller `qwen3` variant) instead.

:::

</TabItem>
<TabItem value="openai" label="OpenAI">


Set your API key in [`settings.yaml`](configuration.md) or the web UI Settings page:

```yaml
llm:
  chat_provider: openai
  openai_api_key: sk-...
```

</TabItem>
<TabItem value="anthropic" label="Anthropic">


```yaml
llm:
  chat_provider: anthropic
  anthropic_api_key: sk-ant-...
```

</TabItem>
<TabItem value="gemini" label="Gemini">


```yaml
llm:
  chat_provider: gemini
  gemini_api_key: ...
```

</TabItem>
</Tabs>


:::note[Embeddings]

Vector embeddings are generated locally on the CPU using sentence-transformers. No LLM provider configuration is needed for embeddings — they work automatically and offline.

:::

## Next Steps

[Quick Start walkthrough](quickstart.md)

## Troubleshooting

### Port already in use

If port 80 is taken, change the host port in your environment:

```bash
HOST_PORT_HTTP=8080 make docker-up
```

### Volume permissions

The container runs as `appuser` (uid 1000). If you see permission errors on mounted volumes, ensure the host directory is writable by uid 1000.

### Switching between deployment modes

When switching between all-in-one and multi-container modes, stop all services first to avoid port conflicts:

```bash
make docker-down
```

:::warning Security defaults

By default, Cortex binds to `0.0.0.0`. Read the [self-hosted threat model](../security/self-hosted-threat-model.md) before exposing the service beyond loopback.

:::
