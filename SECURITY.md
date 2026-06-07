# Security Policy

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email security reports to: **denis@chaoscypher.com**

Include:
- A description of the issue and its impact.
- Steps to reproduce.
- The version / commit SHA affected.
- Any relevant logs, configs, or PoC code.

We aim to acknowledge within 72 hours and issue a fix within 30 days for critical issues. Credit will be given in the fix's release notes unless you prefer otherwise.

## Supported versions

Only the `main` branch is currently supported. This is a self-hosted, single-user platform; there is no LTS or extended-support channel yet.

## Scope

In scope:
- The Cortex API (`packages/cortex/`)
- The Neuron worker (`packages/neuron/`)
- Core domain logic (`packages/core/`)
- The Interface web UI (`packages/interface/`)
- Docker orchestration and default configs (`packages/docker/`)
- The plugin system's trust boundary

Out of scope (known properties, not bugs):
- User-dropped plugins in `data/plugins/` execute with server privileges. This is documented in `packages/core/src/chaoscypher_core/plugins/TRUST_BOUNDARY.md`. Use `CHAOSCYPHER_ALLOW_USER_PLUGINS=0` to disable.
- Entry-point plugins (e.g. `chaoscypher.providers`, `chaoscypher.cleaners`, `chaoscypher.archive_handlers`) execute with server privileges and are not signature-verified — trust derives from what's installed in the venv. Same `TRUST_BOUNDARY.md` documents the posture.
- Self-hosted deployments that expose the Cortex API over the public internet without HTTPS and a strong password. The default bind address is `CHAOSCYPHER_BIND=0.0.0.0` (matches self-hosted convention: Vaultwarden, Jellyfin, Home Assistant); operators who require loopback-only access should set `CHAOSCYPHER_BIND=127.0.0.1`. See `packages/docs/docs/security/self-hosted-threat-model.md` for the full security rationale.

## Known security posture (for auditors)

- Nginx `auth_request` gates every `/api/` request. The app never sees unauthenticated traffic.
- Sessions are HMAC-signed cookies.
- Rate limiting is enabled by default (`RATE_LIMIT.enabled` in `settings.yaml`, default `true`).
- Rate-limit violation logs include client IPs at WARNING level (GDPR-relevant — configure retention per deployment).
- Secrets (API keys, Valkey password, session secret) are environment-variable-driven. Auto-generated in the all-in-one container if empty.

## If a secret is compromised

Rotate the credential first; removing it from disk, logs, or git history alone is not enough.

- **Your password** — change it in Settings → Account. A password change bumps the session epoch, which immediately invalidates every active session.
- **Session signing secret** — delete `<data-dir>/secrets/session_secret` and restart the container. A fresh secret is auto-generated on startup and all existing session cookies become invalid.
- **LLM provider API keys** — revoke and re-issue the key at the provider (OpenAI, Anthropic, etc.), then update it in Settings → LLM Provider.
- **Valkey password** — set a new `QUEUE_PASSWORD` and restart the stack so Cortex, Neuron, and Valkey pick it up together.
