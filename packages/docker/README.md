# Chaos Cypher Docker

Docker deployment for the Chaos Cypher knowledge graph platform.

## Quick Start (All-in-One)

The recommended way to run Chaos Cypher — a single container with all services bundled.

```bash
make docker-up
```

Or directly:

```bash
cd packages/docker
docker compose up
```

Open [http://localhost](http://localhost) when the container is healthy.

### What's inside

A single container running Nginx, Valkey, Cortex (API), and Neuron (workers) via supervisord.

| Port | Service |
|------|---------|
| 80 | HTTP (Nginx → Cortex API + static frontend) |
| 443 | HTTPS (optional, add certs to `/data/secrets/tls/`) |

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST_PORT_HTTP` | `80` | Host port for HTTP |
| `HOST_PORT_HTTPS` | `443` | Host port for HTTPS |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MEM_LIMIT` | `4g` | Container memory limit. Default is sized for 8 GB Docker Desktop hosts; raise to `8g`–`16g` for heavy ingest workloads. |
| `CHAOSCYPHER_ALLOWED_HOSTS` | `localhost,127.0.0.1,::1` | Host header allow-list for LAN/proxy exposure |
| `CHAOSCYPHER_EDGE_AUTH_TOKEN_FILE` | managed by Docker entrypoints | Internal nginx-to-Cortex trust token path; normally leave unset |

### Optional media dependencies (OCR / audio / video ingest)

The default image omits `tesseract`, `ffmpeg`, and language packs — saving roughly 400 MB. These are only needed for the OCR, audio transcription, and video ingest paths. To enable them, build with:

```bash
docker build --build-arg INCLUDE_MEDIA=1 -f packages/docker/Dockerfile -t chaoscypher/chaoscypher:full .
```

Or via compose:

```bash
INCLUDE_MEDIA=1 docker compose build
```

The `INCLUDE_MEDIA` build-arg defaults to `0` (lean image). Set it to `1` to get a `chaoscypher:full`-equivalent image with all media processing tools included.

### TLS

Place `server.crt` and `server.key` in `/data/secrets/tls/` (inside the data volume). The entrypoint detects them automatically and switches to HTTPS mode with HTTP→HTTPS redirect.

### LAN exposure

The default compose file binds to `0.0.0.0` — accessible from your LAN (matches the self-hosted convention used by Vaultwarden, Jellyfin, and Home Assistant). When the bind is non-loopback, also set `CHAOSCYPHER_ALLOWED_HOSTS` to include the LAN hostname or IP users will browse to. For loopback-only deployments set `CHAOSCYPHER_BIND=127.0.0.1` in your `.env`.

### Generated edge-auth token

Chaos Cypher generates the internal edge-auth token automatically. Operators do not need to set `CHAOSCYPHER_EDGE_AUTH_TOKEN`. Nginx adds the token after it verifies the user session, and Cortex only trusts `X-Auth-User` when the matching token is present.

The token is generated once per deployment data volume and reused across normal container restarts:

| Deployment | Token location |
|------------|----------------|
| All-in-one | `/data/secrets/edge_auth_token` |
| Multi-container production | `edge-auth-data` Docker volume mounted at `/run/chaoscypher-edge/.edge_auth_token` |

To rotate the token, stop the stack, remove the token file or `edge-auth-data` volume, and start the stack again. The next startup creates a fresh token and shares it with nginx and Cortex.

### Rebuild

```bash
make docker-rebuild
```

## Multi-Container (Development)

For contributors who need hot-reload and per-service logs. Runs Cortex, Neuron, Interface, and Valkey as separate containers.

```bash
make docker-dev
```

Or directly:

```bash
cd packages/docker/multi-container
docker compose -f docker-compose.dev.yml up
```

| Service | URL | Description |
|---------|-----|-------------|
| Interface | http://localhost:3000 | Vite dev server with HMR |
| Cortex API | http://localhost:8080 | FastAPI with watchdog auto-restart |
| Valkey | Internal only | Queue backend |

### Production (multi-container)

```bash
make docker-prod
```

Multi-container production auto-generates the edge-auth token in the `edge-auth-data` Docker volume. Operators still need to provide a Valkey queue password:

```bash
export QUEUE_PASSWORD="$(openssl rand -base64 32)"
make docker-prod
```

### Stop all services

```bash
make docker-down
```

## Directory Structure

```
packages/docker/
├── Dockerfile              # All-in-one image (default)
├── docker-compose.yml      # All-in-one compose (default)
├── .env.example            # Environment template
│
├── config/                 # All-in-one supporting configs
│   ├── supervisord.conf    # Build-time stub; replaced at runtime by renderer
│   ├── edge-auth-proxy.conf # Protected proxy auth_request headers
│   ├── edge-unauthorized.conf # Auth failure handling
│   ├── proxy-public.conf   # Public proxy header stripping
│   ├── proxy-common.conf   # Shared proxy headers (multi-container interface only)
│   ├── multi-interface-nginx.conf # Static nginx template for multi-container interface
│   ├── entrypoint.sh       # Shared startup script (invokes renderer)
│   └── log-prefix.sh       # Log line prefixer
│
# nginx-http.conf, nginx-https.conf, supervisord.conf, and valkey-args.txt are
# rendered at container start by `python -m chaoscypher_core.services.orchestration`
# from Pydantic settings (templates live in
# packages/core/src/chaoscypher_core/services/orchestration/templates/). The
# multi-container interface image consumes proxy-common.conf and
# multi-interface-nginx.conf as static files because it doesn't ship the
# chaoscypher CLI.
│
├── multi-container/        # Multi-service deployment
│   ├── docker-compose.dev.yml
│   ├── docker-compose.prod.yml
│   ├── cortex/             # API server
│   │   ├── Dockerfile
│   │   ├── Dockerfile.dev
│   │   └── healthcheck.py
│   ├── neuron/             # Background workers
│   │   ├── Dockerfile
│   │   ├── Dockerfile.dev
│   │   └── healthcheck.py
│   └── interface/          # Web UI (nginx template: config/multi-interface-nginx.conf)
│       ├── Dockerfile
│       ├── Dockerfile.dev
│       └── edge-auth-entrypoint.sh
│
└── test/                   # Testing infrastructure
    ├── Dockerfile
    └── docker-compose.yml
```

## Data Persistence

All data is stored in a Docker volume mounted at `/data`:

```
/data/
├── databases/          # SQLite databases (includes all search indices in app.db)
│   └── default/
│       └── app.db
├── mcp/                # MCP tool upload sandbox (prompt-injection boundary)
├── sources/            # Per-source canonical content (original.txt)
├── models/             # Cached embedding models
├── plugins/            # Operator-supplied Python plugins
├── logs/               # Application logs
├── backups/            # Database backups
├── queue/              # Valkey persistence (AOF + RDB snapshots)
├── credentials.json    # Local-auth password hash + API keys (operator-managed via UI)
├── settings.yaml       # User configuration
├── workers.yaml        # Worker overrides (optional)
└── secrets/            # Deployment secrets (root-managed)
    ├── queue_password         # Auto-generated Valkey password
    ├── session_secret         # Auto-generated session HMAC key
    ├── edge_auth_token        # Auto-generated nginx-to-Cortex trust token
    ├── supervisor_password    # Auto-generated supervisord password (root-only)
    └── tls/                   # TLS certificates (optional, operator-supplied)
        ├── server.crt
        ├── server.key
        └── dhparam.pem        # Auto-generated 2048-bit DH params
```

Ephemeral runtime state (nginx active-config symlink, Valkey ACL, edge-auth nginx
snippet, rendered `valkey-args.txt`) lives under `/run/chaoscypher/` and is
regenerated on every container boot — never persisted.

## Troubleshooting

### View logs

```bash
# All-in-one
docker logs -f chaoscypher

# Multi-container
cd packages/docker/multi-container
docker compose -f docker-compose.dev.yml logs -f cortex
```

### Rebuild images

```bash
# All-in-one
make docker-rebuild

# Multi-container
cd packages/docker/multi-container
docker compose -f docker-compose.dev.yml build --no-cache
```

### Port conflicts

Change the host port via environment variable:

```bash
HOST_PORT_HTTP=8080 make docker-up
```

### Valkey "memory overcommit must be enabled" warning

If Valkey logs `WARNING Memory overcommit must be enabled!`, the host kernel's `vm.overcommit_memory` is at its default. Valkey can still run, but background saves may fail under low-memory conditions. Fix once on the host (requires root):

```bash
# One-shot (until next reboot):
sudo sysctl vm.overcommit_memory=1

# Persist across reboots:
echo 'vm.overcommit_memory = 1' | sudo tee /etc/sysctl.d/99-valkey.conf
sudo sysctl --system
```

This is a host setting — it cannot be changed from inside the container without `--privileged`, which we intentionally avoid.
