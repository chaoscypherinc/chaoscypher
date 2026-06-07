---
title: Authentication
description: Connect to Lexicon Hub to publish packages or access private content — login via CLI, web UI, or API token. Browsing public packages requires no account.
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';
import PreviewBanner from "@site/src/components/PreviewBanner";

<PreviewBanner service="Lexicon Hub" />

# Authentication

Connect to Lexicon Hub to search, download, and publish knowledge packages.

:::tip

Browsing and downloading public packages does not require authentication. You only need to log in to publish packages or access private content.

:::

## Login

<Tabs>
<TabItem value="cli" label="CLI">


```bash
chaoscypher lexicon login
```

This starts the **Device Authorization** flow (recommended) — a browser window opens for secure authentication.

</TabItem>
<TabItem value="api" label="API">


```bash
# Start device authorization flow
curl -X POST "http://localhost:8080/api/v1/lexicon/auth/device" \
  -H "Content-Type: application/json" \
  -d '{"lexicon_url": "https://lexicon.example.com"}'
```

</TabItem>
</Tabs>


## Authentication Methods

### Device Authorization (Recommended)

The default and most secure method. Uses OAuth 2.0 Device Authorization Grant (RFC 8628).

```bash
chaoscypher lexicon login
```

``` { .text .no-copy }
Lexicon Login

╭──────────── Browser Authentication ────────────╮
│                                                 │
│ To authenticate, visit:                         │
│                                                 │
│   https://lexicon.example.com/device?code=ABCD  │
│                                                 │
│ Code expires in 15 minutes.                     │
╰─────────────────────────────────────────────────╯

Open browser automatically? [Y/n]: y
Browser opened. Complete authentication there.

⠋ Waiting for browser authentication...

✓ Logged in as jane
  Lexicon: https://lexicon.example.com
  Credentials saved to: ~/.config/chaoscypher/auth.json
```

If you can't open a browser (e.g., SSH session), use `--no-browser` and copy the URL manually:

```bash
chaoscypher lexicon login --no-browser
```

### Token Authentication (CI/CD)

For automated pipelines, provide an API token directly:

<Tabs>
<TabItem value="cli" label="CLI">


```bash
chaoscypher lexicon login --token ghp_xxxxx
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl -X POST "http://localhost:8080/api/v1/lexicon/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"token": "eyJhbGciOiJIUzI1NiIs...", "username": "ci-bot"}'
```

</TabItem>
</Tabs>


## Check Status

Verify your current authentication state:

<Tabs>
<TabItem value="cli" label="CLI">


```bash
chaoscypher lexicon whoami
```

``` { .text .no-copy }
Logged in as: jane
  Lexicon: https://lexicon.example.com
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl "http://localhost:8080/api/v1/lexicon/auth/status"
```

```json
{
  "authenticated": true,
  "username": "jane",
  "lexicon_url": "https://lexicon.example.com",
  "token_present": true
}
```

</TabItem>
</Tabs>


## Logout

<Tabs>
<TabItem value="cli" label="CLI">


```bash
chaoscypher lexicon logout
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl -X POST "http://localhost:8080/api/v1/lexicon/auth/logout"
```

</TabItem>
</Tabs>


## Credential Storage

Lexicon login state is stored locally in `auth.json` in the config directory:

| Platform | Path |
|----------|------|
| Linux | `~/.config/chaoscypher/auth.json` |
| macOS | `~/Library/Application Support/chaoscypher/auth.json` |
| Windows | `%APPDATA%\chaoscypher\auth.json` |

The file contains the access token and Hub URL (permissions `0600` on Unix). It is created on login and removed on logout.

:::note[Upgrading]

Earlier releases stored this in `credentials.json`. There is no migration — run `chaoscypher lexicon login` once to create `auth.json`. A leftover `credentials.json` is never read; it triggers a one-line re-login notice and can be deleted.

:::

## Hub URL Configuration

By default, the CLI connects to `https://lexicon.chaoscypher.com`. To use a different Hub instance:

**Per-command:**

```bash
chaoscypher lexicon login --url https://lexicon.example.com
```

**Environment variable:**

```bash
export LEXICON_URL=https://lexicon.example.com
```

**Settings file** ([`settings.yaml`](../getting-started/configuration.md)):

```yaml
lexicon:
  url: https://lexicon.example.com
  api_path: /api/v1
  timeout: 60
```
