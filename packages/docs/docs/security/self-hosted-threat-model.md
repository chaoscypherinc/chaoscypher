---
title: Self-Hosted Threat Model
description: What Chaos Cypher defends against — and what it explicitly doesn't, in single-user self-hosted mode.
---

# Self-Hosted Threat Model

Chaos Cypher's all-in-one container is designed for **single-user self-hosted deployment** — typically a homelab, personal LAN, or a single VM behind a reverse proxy you control.

This page lists the threats Chaos Cypher defends against, the threats accepted by design, and how to harden further if your deployment shape diverges from the defaults.

## What Chaos Cypher Defends Against

- **Auth bypass via direct Cortex hits.** Cortex refuses any `X-Auth-User` header that doesn't come paired with the deployment-local edge-auth token. Direct port-8080 access (in dev compose, behind a misconfigured proxy, etc.) returns 401.
- **Session theft via stale cookies after logout.** Logout bumps `session_epoch`, invalidating every outstanding cookie for that user.
- **Prompt-injection-driven secret exfiltration.** [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) file ingest is sandboxed to `/data/mcp`; the LLM cannot read anything under `/data/secrets/` (queue password, session secret, edge auth token, supervisor password, TLS material). Hidden files (dotfiles) are explicitly rejected even inside the sandbox.
- **Container blast radius.** Cortex and Neuron run as `appuser` with `no-new-privileges`. The supervisor password is stored root-only at `/data/secrets/supervisor_password` and never propagated into the Cortex/Neuron environment. Other deployment secrets under `/data/secrets/` are owned `root:appuser 0640` — readable by services that need them, but a compromised appuser process cannot tamper with them. Static nginx config lives in root-owned `/etc/nginx/conf.d`; only ephemeral runtime state lives in the appuser-writable `/run/chaoscypher/` dir.
- **Common archive attacks.** Backup, package, and source-loader archive extraction enforces path containment (`is_relative_to`), per-file + total decompressed size caps, member-count caps, and rejects symlinks and devices. Both the source-loader path and the `.ccx` package import path use the same `ArchiveExtractor` helper.
- **Credential leaks via diagnostic exports.** Log files are scrubbed for `Authorization`, `api_key=`, `Bearer`, and `token=` patterns before zipping. Secret-bearing settings render as `"configured"` rather than partial reveals.
- **`dev_mode` shipping to production by accident.** Cortex refuses to start when `settings.dev_mode=True` unless the operator has explicitly set `CHAOSCYPHER_ALLOW_DEV_MODE=1` to acknowledge.

## What Chaos Cypher Explicitly Doesn't Defend Against

These are pragmatic deferrals for the single-user self-hosted posture. The enterprise build addresses each.

| Threat | Why it is accepted | If you need protection |
|---|---|---|
| **First-arrival admin race.** | Open-by-default bind matches the self-hosted convention (Vaultwarden, Jellyfin, Home Assistant, Gitea). Adding a setup-token UX or loopback gate would push the "everyone disables the security" failure mode. | Set `CHAOSCYPHER_BIND=127.0.0.1` for first boot, complete `/setup`, then flip back. Or run the first boot on a network you fully control (laptop on home Wi-Fi, not a public VPS) before exposing it. |
| **Online password brute-force beyond bcrypt + nginx rate-limit.** | Account lockout would lock the owner out. Self-hosted single-user attacker doesn't get many guesses through `5r/s` + bcrypt. | Set a 16+ char password; put the box behind a VPN or Tailscale; don't expose `0.0.0.0` without TLS. |
| **DNS rebinding on operator-supplied URLs.** | The operator triggers `POST /api/v1/sources/url` themselves; an attacker needs DNS control + the operator fetching their URL. | Don't fetch URLs from untrusted sources. |
| **Tampered backup restores.** | The operator only restores backups they made themselves. | Verify backup file integrity out-of-band (e.g., compare `sha256sum` to a copy you trust). |
| **Disguised file uploads** (e.g. SVG-with-script labeled as PDF). | The operator uploads their own files. | Don't upload files you didn't create. |
| **Username enumeration via login timing.** | The operator knows the username. | N/A. |
| **Per-session cookie revocation list.** | Logout invalidates all sessions; the single-user model treats "log out" as "log out everywhere." | Use a different account for short-lived shared access. |
| **Windows file ACLs on `.session_secret`.** | Windows is a development target only. | Run on Linux for any non-dev deployment. |
| **HTTP/2 / HSTS preload.** | Both only matter once TLS is on; the operator opts into TLS. | Enable TLS in Settings, then run a Mozilla Observatory check and tune the headers. |

## Hardening checklist for LAN / public exposure

If you set `CHAOSCYPHER_BIND=0.0.0.0` (the default) and expose the box beyond your local machine:

1. **Set TLS first.** Generate / install certs and switch to `nginx-https.conf` before opening the port.
2. **Set a 16+ char password** at `/setup` (the minimum is 8 — go higher for any non-loopback exposure).
3. **Set `CHAOSCYPHER_ALLOWED_HOSTS`** to your hostname/IP — blocks DNS-rebinding probes and Host-header spoofing. You can also flip this on later from the UI (see *Allowing external access from the UI* below).
4. **Put the box behind a VPN, SSH tunnel, or Tailscale** if possible. The box is designed to be safe on a small trusted network, not on the open internet.
5. **First-boot on a private network.** Either set `CHAOSCYPHER_BIND=127.0.0.1` for the initial `/setup` (then flip to `0.0.0.0`), or do the first boot on a trusted network where you'll be the first to reach the API.
6. **Restrict `/api/v1/health` to your monitoring source** with a dedicated nginx `allow … ; deny all;` block if you don't want LAN scanners to fingerprint your stack. (Note: unauthenticated `/health` returns only `{healthy, status}` — the detailed per-subsystem payload requires auth.)
7. **Rotate the API keys** you've minted (`/api/v1/auth/keys`) on a cadence that matches your threat model.

## Allowing external access from the UI

Self-hosters can flip the host-header check off without editing `settings.yaml` or restarting the container:

- **Pre-setup:** the host-header check is bypassed entirely while `setup_completed=false`. This is required so a user installing on a headless server (no local browser) can reach `/setup` from any device on their LAN to complete first-run. There are no credentials or data to compromise yet — same posture as the *First-arrival admin race* trade-off above.
- **At setup:** the wizard's Account step has an *Allow access from other devices* checkbox. It defaults to ON when the wizard is opened over a non-loopback address. The selection is persisted immediately when the user clicks Create Account, so post-setup policy is in place before the wizard advances to step 2.
- **After setup:** *Settings → Network access* exposes the same toggle and a manual allow-list editor. Changes take effect on the next request — no restart.

Turning the toggle on is functionally equivalent to setting `CHAOSCYPHER_ALLOWED_HOSTS=*` — DNS-rebinding protection is disabled. The manual allow-list is retained but bypassed while the toggle is on, so flipping it off again restores your prior policy.

When a request is rejected (toggle off, host not on the list), browsers see a branded HTTP 421 page explaining what to do; API clients receive the canonical `{error, message, details}` JSON envelope.

## Diagnosing auth misconfiguration

If nginx's `auth_request` is misconfigured, `X-Auth-User` may not arrive at Cortex, producing silent 401 storms. Hit `GET /api/v1/health/auth` (no auth required) for diagnostics:

```json
{
  "x_auth_user_present": false,
  "recent_failed_attempts": 142,
  "last_failure_at": "2026-05-06T18:32:11.000000+00:00"
}
```

When `recent_failed_attempts` is non-zero on requests that should be authenticated, the nginx `auth_request` forward isn't reaching Cortex. Check your nginx logs and verify the `auth_request` directive points to the correct upstream and that the edge-auth token is set.

Note: `x_auth_user_present` reflects the **current** request only — hit the endpoint while also making an authenticated API call to compare the two states.

## Reporting issues

Security issues: see `SECURITY.md` in the repo root.

## See also

- [API reference: Authentication](../reference/api/auth.md) — login, logout, API key management, and the nginx auth_request flow
- [Security: Plugin trust model](./plugin-trust.md) — what user-installed plugins can do and how to restrict them
