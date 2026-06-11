---
id: production
title: Production Deployment
description: TLS reverse proxy, scaling, log rotation, and backup pointers for self-hosted Chaos Cypher.
---

# Production Deployment

The all-in-one Docker image binds on port 80 by default. For anything beyond local development — a VPS, a home lab accessible from the internet, or a corporate LAN — you should front it with a reverse proxy that terminates TLS and provides a canonical hostname.

This guide uses **Caddy**, which handles HTTPS automatically via Let's Encrypt with zero certificate management.

## Why a reverse proxy?

- **TLS** — encrypts traffic between clients and the server. Without it, authentication cookies travel in plaintext.
- **Canonical hostname** — lets you reach Chaos Cypher at `https://cypher.example.com` instead of an IP:port.
- **`Secure` cookie flag** — Chaos Cypher auto-resolves `cookie_secure` at boot: it is enabled (`true`) when TLS certificate files are detected in `tls.cert_dir`, and disabled (`false`) on plain-HTTP deployments (so LAN/HTTP installs don't hit a logout loop). Browsers reject `Secure` cookies on non-HTTPS connections, so if you terminate TLS at a reverse proxy (like Caddy) that doesn't expose certs to the app container, set `local_auth.cookie_secure: true` explicitly in `settings.yaml` to avoid silent logout loops.

## Prerequisites

- A domain name pointing at your server's public IP (A record).
- Docker Compose installed.
- Port 80 and 443 open in your firewall (Caddy needs 80 for the ACME HTTP-01 challenge).

## Minimal Caddyfile

Create `/etc/caddy/Caddyfile` (or `/home/deploy/caddy/Caddyfile` if you prefer a user-owned path):

```caddyfile
cypher.example.com {
    reverse_proxy localhost:8080
}
```

That is the entire config. Caddy obtains and renews the certificate automatically. Replace `cypher.example.com` with your domain and `8080` with whatever host port the Chaos Cypher container exposes.

### Mapping the container port

Use the published image and map the container's internal port 80 onto a non-privileged host port so Caddy can reach it without running as root. In your `docker-compose.yml`:

```yaml
services:
  chaoscypher:
    image: ghcr.io/chaoscypherinc/chaoscypher:latest   # pin a vX.Y.Z tag for production
    ports:
      - "127.0.0.1:8080:80"   # bind on loopback only — Caddy is the public face
```

If you are building from source instead, build and tag the image locally (`docker build -f packages/docker/Dockerfile -t chaoscypher:local .`) and use `image: chaoscypher:local`.

Binding to `127.0.0.1` means the application port is not reachable directly from the internet; all traffic must flow through Caddy.

### Starting Caddy

```bash
# Debian/Ubuntu
sudo apt install -y caddy
sudo systemctl enable --now caddy

# Or run in Docker alongside the stack
docker run -d \
  --name caddy \
  --network host \
  -v /etc/caddy/Caddyfile:/etc/caddy/Caddyfile:ro \
  -v caddy_data:/data \
  caddy:latest
```

## Scaling considerations

Chaos Cypher is a **single-user self-hosted product**. The main performance levers are:

- **CPU and RAM for the container.** The extraction pipeline is CPU-bound during chunking and LLM calls. Give the container at least 4 GB of RAM; 8 GB is comfortable with a mid-size Ollama model loaded.
- **`mem_limit` in `docker-compose.yml`.** The default is set conservatively. Raise it to match the available host memory minus a ~2 GB headroom for the OS and Caddy:
  ```yaml
  services:
    chaoscypher:
      mem_limit: 12g   # example for a 16 GB host
  ```
- **Valkey AOF persistence.** The queue uses Valkey (Redis-compatible) with AOF enabled. On a write-heavy import workload, place the data directory on an SSD.
- **Mutation rate limit.** nginx throttles mutating requests (POST/PUT/PATCH/DELETE on `/api/*`) to 10 r/s per IP with a burst of 20. Scripted clients that submit rapidly (bulk imports, automation) can hit `503`s — raise `rate_limit.mutations_max_requests` / `rate_limit.mutations_burst` in `settings.yaml` and restart the container. See [rate limiting](./configuration.md#rate-limiting).
- **There is no horizontal scaling path** for this edition. All components (Cortex API, Neuron worker, Valkey, SQLite) run inside one container. If you hit hard CPU limits, the answer is a bigger host, not more replicas.

## Log rotation

Container logs are written under `/data/logs/`. The image includes a `logrotate` configuration at `/etc/logrotate.d/chaoscypher` that rotates daily and keeps seven days of compressed history. No operator action is required unless you want to tune the retention window:

```bash
# Inside the running container
docker exec -it chaoscypher cat /etc/logrotate.d/chaoscypher
# Edit to taste, then force a rotation to verify
docker exec -it chaoscypher logrotate -f /etc/logrotate.d/chaoscypher
```

If you mount `/data` as a named volume or bind-mount, the rotated `.gz` files accumulate there. Periodically prune files older than your retention window.

## Backups

Before upgrading or making structural changes, take a backup:

```bash
# Via the REST API (Cortex must be running)
# Authenticate with a Bearer API key — there is no HTTP Basic Auth.
# Mint one via the web UI under Settings → API Keys, or POST /api/v1/auth/keys.
curl -s -H "Authorization: Bearer <api_key>" \
  -X POST http://localhost:8080/api/v1/backup \
  | jq .
```

The backup lands in `<data_dir>/backups/<database_name>/app_YYYYMMDD_HHMMSS.db`. For the full backup and restore flow — including restore steps and a cron-based retention example — see the [Backup and Restore](./backup-restore.md) guide.

## See also

- [Upgrading](./upgrading.md) — tag-to-tag upgrade procedure and rollback
- [Backup and Restore](./backup-restore.md) — backup contract, restore flow, retention
- [Configuration reference](./configuration.md) — all settings including `local_auth.cookie_secure`, `CHAOSCYPHER_BIND`, and `MEM_LIMIT`
