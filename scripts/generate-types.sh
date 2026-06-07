#!/usr/bin/env bash
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
#
# Generate packages/interface/src/types/generated/api.ts from Cortex's
# live OpenAPI schema.
#
# Usage: scripts/generate-types.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
BUILD_DIR="${REPO_ROOT}/build"
mkdir -p "${BUILD_DIR}"

# Resolve all relative paths below (build/openapi.json, build/_openapi_tmp) from
# the repo root regardless of the caller's cwd. CI invokes this from
# packages/interface (`../../scripts/generate-types.sh`), so without this the
# Python heredoc would write packages/interface/build/openapi.json while
# `npm run generate-types` reads ../../build/openapi.json (repo-root/build) -> ENOENT.
cd "${REPO_ROOT}"

# Dump openapi.json by instantiating the FastAPI app and calling app.openapi().
# The editable install may point at a different checkout — force sys.path
# to the current worktree's packages so we capture THIS tree's schema.
#
# NOTE: On Windows the PYTHONPATH separator is ';', but bash running under Git
# Bash / MSYS2 treats ';' correctly too.  Using ';' here is safe on all
# platforms because Python's site.py processes PYTHONPATH before any OS
# path-splitting logic.
PYTHONPATH="${REPO_ROOT}/packages/core/src;${REPO_ROOT}/packages/cortex/src${PYTHONPATH:+;${PYTHONPATH}}" \
uv run --project "${REPO_ROOT}" python - <<PY
import json
import os
import sys
from pathlib import Path

# Ensure data dir exists so settings loading doesn't fail.
data_dir = Path(os.environ.get("CHAOSCYPHER_DATA_DIR", "build/_openapi_tmp"))
data_dir.mkdir(parents=True, exist_ok=True)
os.environ["CHAOSCYPHER_DATA_DIR"] = str(data_dir)

from chaoscypher_cortex.main import app

schema = app.openapi()
out = Path("build/openapi.json")
out.write_text(json.dumps(schema, indent=2))
print(f"Wrote {out} ({len(schema.get('paths', {}))} paths, {len(schema.get('components', {}).get('schemas', {}))} schemas)")
PY

# Run openapi-typescript via the package.json script.
cd "${REPO_ROOT}/packages/interface"
npm run generate-types
echo "Wrote packages/interface/src/types/generated/api.ts"
