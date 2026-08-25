#!/bin/bash
set -euo pipefail

# Only relevant in remote (cloud) sessions — local clones don't exhibit the
# stale-geometry problem this hook exists for.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Fresh cloud clones can arrive with local `main` and the `origin/main`
# remote-tracking ref lagging the true tip while HEAD sits on the current
# commit — the #356 stale-base class (operating-cadence.md guardrail 5),
# which produced PRs touching 66 files where one was expected (#351/#360)
# and a false red security gate. Re-pin both refs at session start so
# nothing in the session trusts a stale base. Fail open: a network hiccup
# must not block the session (guardrail 5 still guards routine PRs).
git fetch origin main --quiet || { echo "session-start: fetch failed; guardrail 5 still applies"; exit 0; }

current=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ "$current" = "main" ]; then
  # Fast-forward only — never move a main that carries local work.
  git merge --ff-only origin/main --quiet 2>/dev/null || true
elif git merge-base --is-ancestor main origin/main 2>/dev/null; then
  # Safe fast-forward of the ref we're not sitting on.
  git branch -f main origin/main
fi

echo "session-start: main pinned to origin/main @ $(git rev-parse --short origin/main)"
