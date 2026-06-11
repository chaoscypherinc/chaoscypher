---
title: Lexicon Hub CLI
description: Authenticate with Lexicon Hub and manage knowledge packages from the CLI — login, search, download, and publish CCX packages using chaoscypher lexicon.
---

# Lexicon Hub

The `lexicon` command group manages authentication and packages on the Lexicon Hub registry.

```bash
chaoscypher lexicon --help
```

---

## Authentication

### Login

```bash
chaoscypher lexicon login
```

Authenticates with Lexicon Hub. The default flow uses **OAuth Device Authorization (RFC 8628)**, which opens a browser for secure authentication without entering credentials in the terminal.

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--url` | `-u` | Lexicon Hub URL (default: `$LEXICON_URL` or `https://lexicon.chaoscypher.com`) |
| `--token` | `-t` | API token for CI/automation (skips interactive auth) |
| `--no-browser` | | Don't auto-open the browser; copy URL manually |

#### Device Authorization Flow (Default)

The recommended authentication method. A device code is generated and the user completes authentication in the browser.

```bash
chaoscypher lexicon login
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
  Credentials saved to: /home/jane/.config/chaoscypher/auth.json

You can now use:
  chaoscypher pull <package>            - Download packages
  chaoscypher push                      - Upload packages
  chaoscypher lexicon search <query>    - Search packages
```

#### Token Auth (CI/Automation)

Use `--token` to provide an API token directly, bypassing the interactive flow.

```bash
chaoscypher lexicon login --token ghp_xxxxx
Username (for display): ci-bot
✓ Logged in as ci-bot
  Lexicon: https://lexicon.chaoscypher.com
  Credentials saved to: /home/jane/.config/chaoscypher/auth.json
```

---

### Logout

```bash
chaoscypher lexicon logout
```

Removes stored credentials from the local system.

```bash
chaoscypher lexicon logout
✓ Logged out (jane)
  Credentials removed from: /home/jane/.config/chaoscypher/auth.json
```

If no credentials are stored:

```bash
chaoscypher lexicon logout
Not logged in.
```

---

### Check Identity

```bash
chaoscypher lexicon whoami
```

Shows the currently authenticated user and the Lexicon Hub URL.

```bash
chaoscypher lexicon whoami
Logged in as: jane
  Lexicon: https://lexicon.example.com
```

If not authenticated:

```bash
chaoscypher lexicon whoami
Not logged in.

Use 'chaoscypher lexicon login' to authenticate.
```

---

## Package Management

### Search Packages

```bash
chaoscypher lexicon search <QUERY>
```

Searches the Lexicon Hub for packages matching the query. The search covers package names, descriptions, and tags.

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--limit` | `-n` | Maximum results to show (default: `20`) |
| `--tag` | `-t` | Filter by tag (can be repeated) |
| `--author` | `-a` | Filter by author username |
| `--sort` | `-s` | Sort results: `relevance` (default), `downloads`, `updated`, `name` |

#### Examples

```bash
chaoscypher lexicon search "medical ontology"
Searching Lexicon Hub: medical ontology

            Found 3 package(s)
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Package             ┃ Version ┃ Owner   ┃ Description                        ┃ Downloads ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━┩
│ medical-ontology    │ 2.1.0   │ john    │ Comprehensive medical terminology  │      3.2k │
│ clinical-terms      │ 1.0.3   │ sarah   │ Clinical terminology knowledge...  │       890 │
│ pharma-entities     │ 0.9.1   │ john    │ Pharmaceutical entity definitions  │       412 │
└─────────────────────┴─────────┴─────────┴────────────────────────────────────┴───────────┘

Install with: chaoscypher pull john/medical-ontology
```

Search with filters:

```bash
chaoscypher lexicon search "nlp" --author john --sort downloads
chaoscypher lexicon search "research" --tag biomedical --limit 10
```

---

### List Installed Packages

```bash
chaoscypher lexicon list
```

Lists locally installed and cached packages (`.ccx` files in the packages directory).

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--all` | | Show all cached versions (includes relative paths) |
| `--format` | `-f` | Output format: `table` (default), `json`, `simple` |

#### Table Format (Default)

```bash
chaoscypher lexicon list
Installed Packages

Packages directory: /home/jane/.local/share/chaoscypher/packages

┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━┓
┃ Package             ┃    Size ┃ Path ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━┩
│ medical-ontology    │  245.3KB│      │
│ research-corpus     │  1.2MB  │      │
│ nlp-toolkit         │   89.7KB│      │
└─────────────────────┴─────────┴──────┘

Total: 3 package(s)
```

#### JSON Format

```bash
chaoscypher lexicon list --format json
[
  {
    "name": "medical-ontology",
    "path": "/home/jane/.local/share/chaoscypher/packages/medical-ontology.ccx",
    "size": 251187
  },
  {
    "name": "research-corpus",
    "path": "/home/jane/.local/share/chaoscypher/packages/research-corpus.ccx",
    "size": 1258291
  }
]
```

#### Simple Format

```bash
chaoscypher lexicon list --format simple
medical-ontology
research-corpus
nlp-toolkit
```

When no packages are installed:

```bash
chaoscypher lexicon list
Installed Packages

Packages directory: /home/jane/.local/share/chaoscypher/packages

No packages installed yet.

To install packages:
  chaoscypher pull <package>
  chaoscypher graph package load <file.ccx>
```

---

### Package Info

```bash
chaoscypher lexicon info <PACKAGE>
```

Shows detailed information about a package from the Lexicon Hub or a local `.ccx` file.

`PACKAGE` can be:

- A hub package in `owner/name` format (e.g., `john/medical-ontology`)
- A local file path with the `--local` flag (e.g., `./my-package.ccx`)

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--version` | `-v` | Show info for a specific version |
| `--local` | `-l` | Show info for a local `.ccx` file instead of a hub package |

#### Hub Package Info

```bash
chaoscypher lexicon info john/medical-ontology
Package: john/medical-ontology

╭───────────────── Package Info ─────────────────╮
│ medical-ontology                               │
│ Version: 2.1.0                                 │
│ Owner: john                                    │
│ Comprehensive medical terminology ontology     │
╰────────────────────────────────────────────────╯

Details:
  Package Type: ontology
  Downloads: 3,200
  Stars: 48
  Versions: 5
  Created: 2025-06-15T10:30:00Z
  Updated: 2026-01-20T14:22:00Z

To install:
  chaoscypher pull john/medical-ontology
```

Specific version:

```bash
chaoscypher lexicon info john/medical-ontology --version 1.2.0
```

#### Local File Info

```bash
chaoscypher lexicon info ./my-package.ccx --local
Package: my-package.ccx

╭───────────── Package Info ─────────────╮
│ my-package.ccx                         │
│ Compressed: 245.3 KB                   │
│ Uncompressed: 1.2 MB                   │
╰────────────────────────────────────────╯

Files: (12 total)
  - manifest.json
  - graph/entities.jsonld
  - graph/relationships.jsonld
  - templates/person.json
  - templates/organization.json
  ... and 7 more

Archive size: 245.3 KB
```

---

### Download a Package

```bash
chaoscypher lexicon pull <PACKAGE>
```

Downloads a package from the Lexicon Hub. `PACKAGE` should be in `owner/name` format or just `name` for official packages.

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--version` | `-v` | Specific version to pull (default: latest) |
| `--force` | `-f` | Overwrite existing files |
| `--output` | `-o` | Output directory (default: `.`) |
| `--extract` | `-x` | Extract package after download |

#### Examples

```bash
chaoscypher lexicon pull john/medical-ontology
Pulling package: john/medical-ontology
  Version: latest
  Output: .

Downloading john/medical-ontology... ━━━━━━━━━━━━━━━━━━━━━ 245.3KB

✓ Downloaded john/medical-ontology v2.1.0
  File: john-medical-ontology.ccx
  Size: 245.3 KB

Next steps:
  chaoscypher graph package load john-medical-ontology.ccx
```

Download a specific version and extract:

```bash
chaoscypher lexicon pull john/medical-ontology --version 1.2.0 --extract --output ./packages/
Pulling package: john/medical-ontology
  Version: 1.2.0
  Output: ./packages/

Downloading john/medical-ontology... ━━━━━━━━━━━━━━━━━━━━━ 198.1KB

✓ Downloaded john/medical-ontology v1.2.0
  File: packages/john-medical-ontology-1.2.0.ccx
  Size: 198.1 KB

Extracting to packages/john-medical-ontology...
✓ Extracted to packages/john-medical-ontology

Next steps:
  chaoscypher graph package load packages/john-medical-ontology
```

If the file already exists:

```bash
chaoscypher lexicon pull john/medical-ontology
✗ File already exists: john-medical-ontology.ccx
Use --force to overwrite
```

---

### Upload a Package

```bash
chaoscypher lexicon push <PATH>
```

Uploads a package to the Lexicon Hub. `PATH` should be a `.ccx` archive file or a directory containing a `manifest.json`.

Requires authentication. Run `chaoscypher lexicon login` first.

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--message` | `-m` | Release message |
| `--public` / `--private` | | Package visibility (default: `--public`) |
| `--force` | `-f` | Skip confirmation prompt |

#### Examples

Upload a `.ccx` file:

```bash
chaoscypher lexicon push ./my-package.ccx
Pushing package: my-package
  File: my-package.ccx
  Size: 245.3 KB
  Visibility: Public

Proceed with upload? [Y/n]: y

Uploading my-package... ━━━━━━━━━━━━━━━━━━━━━ 245.3 KB

✓ Published my-package v1.0.0
  URL: https://lexicon.example.com/packages/my-package

Share with:
  chaoscypher pull my-package
```

Upload a directory with a release message:

```bash
chaoscypher lexicon push ./my-package --message "Major update" --private
Building package archive...
✓ Built my-package-1.0.0.ccx

Pushing package: my-package
  File: my-package-1.0.0.ccx
  Size: 312.7 KB
  Visibility: Private
  Message: Major update

Proceed with upload? [Y/n]: y

Uploading my-package... ━━━━━━━━━━━━━━━━━━━━━ 312.7 KB

✓ Published my-package v1.0.0
  URL: https://lexicon.example.com/packages/my-package

Share with:
  chaoscypher pull my-package
```

---

### Remove a Package

```bash
chaoscypher lexicon remove <PACKAGE>
```

Removes a locally installed package from the packages cache. This does not remove it from the Lexicon Hub.

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--version` | `-v` | Remove a specific version only |
| `--all` | | Remove all cached versions |
| `--force` | `-f` | Skip confirmation prompt |

#### Examples

```bash
chaoscypher lexicon remove john/medical-ontology
Package to remove: john/medical-ontology
  → /home/jane/.local/share/chaoscypher/packages/john/medical-ontology

Are you sure you want to remove? [y/N]: y
✓ Package removed successfully
```

Remove a specific version:

```bash
chaoscypher lexicon remove john/medical-ontology --version 1.2.0
```

Remove without confirmation:

```bash
chaoscypher lexicon remove my-package --force
```

---

## Quick Commands

The top-level `pull` and `push` commands are shortcuts that map directly to their `lexicon` counterparts:

```bash
# These are equivalent:
chaoscypher pull john/medical-ontology
chaoscypher lexicon pull john/medical-ontology

# These are equivalent:
chaoscypher push ./my-package.ccx
chaoscypher lexicon push ./my-package.ccx
```

All options and flags work identically with both forms.
