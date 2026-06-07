---
id: uninstalling
title: Uninstalling
description: Stop containers, remove volumes, and wipe local data.
---

# Uninstalling Chaos Cypher

## All-in-one Docker

```bash
# stop the running container
docker compose down            # from packages/docker/ for the from-source path
# or: docker stop chaoscypher && docker rm chaoscypher   # for `docker run` installs

# find the persistent data volume (name varies by install path)
docker volume ls | grep chaoscypher

# then remove the one you found, e.g.:
#   chaoscypher_app-data            (make docker-up / compose from packages/docker)
#   chaoscypher-data                (published-image `docker run -v chaoscypher-data:/data`)
#   chaoscypher_chaoscypher-data    (published-image Compose snippet)
docker volume rm <name>

# remove the image (name varies by install path)
docker image ls | grep chaoscypher

# then remove the one you found, e.g.:
#   ghcr.io/chaoscypherinc/chaoscypher    (published-image installs)
#   chaoscypher-chaoscypher               (make docker-up / compose from packages/docker)
#   chaoscypher:local                     (manual `docker build` from the production guide)
docker rmi <name>
```

That removes everything: the SQLite database, uploaded sources, embeddings, chats, API keys, and login credentials.

## Multi-container dev

```bash
make docker-down
docker compose -f packages/docker/multi-container/docker-compose.dev.yml down --volumes
rm -rf packages/docker/data/        # local data dir
```

## CLI install (`pip install chaoscypher-cli`)

The CLI stores its files in platform-standard (XDG / platformdirs) locations — not `~/.chaoscypher/`. Engine config (`settings.yaml`, including API keys) lives in the data dir; auth/login state (`auth.json`) lives in the config dir.

```bash
pip uninstall chaoscypher-cli

# Data dir (settings.yaml with API keys, databases, downloaded models)
rm -rf ~/.local/share/chaoscypher/                   # Linux
rm -rf "~/Library/Application Support/chaoscypher/"  # macOS
# Windows: rmdir /s "%LOCALAPPDATA%\chaoscypher"

# Config dir (auth.json login state)
rm -rf ~/.config/chaoscypher/                        # Linux
rm -rf "~/Library/Application Support/chaoscypher/"  # macOS (same as data)
# Windows: rmdir /s "%APPDATA%\chaoscypher"

# Cache dir
rm -rf ~/.cache/chaoscypher/                         # Linux
rm -rf "~/Library/Caches/chaoscypher/"               # macOS
# Windows: rmdir /s "%LOCALAPPDATA%\chaoscypher\Cache"
```

`CHAOSCYPHER_CONFIG_DIR` can override the config location, so check `chaoscypher doctor` if you're unsure where your install resolved its paths.

## Verify nothing left running

```bash
docker ps | grep chaoscypher    # should print nothing
```

## Data deletion checklist

- [ ] Persistent volume removed (`docker volume ls | grep chaoscypher`)
- [ ] Local data dir removed (`packages/docker/data/` for dev; the platform data/config/cache dirs above for CLI — e.g. `~/.local/share/chaoscypher` on Linux, `%LOCALAPPDATA%\chaoscypher` on Windows)
- [ ] Backups deleted if you no longer need them (`backups/*.ccx`)
- [ ] LLM provider API keys revoked at the source (OpenAI / Anthropic / Gemini consoles) if you'll never reinstall

See the [self-hosted threat model](../security/self-hosted-threat-model.md) for an inventory of what's stored locally.
