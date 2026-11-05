// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The login page's `next=` target: only a relative in-app path, never an open
 * redirect.
 *
 * `next` is attacker-controlled. A path must start with one `/`; `//host` is
 * protocol-relative, and any backslash is refused because URL parsing treats
 * `\` as `/` in http(s) URLs, so `/\evil.com` resolves to `//evil.com`.
 */
export function safeNextPath(raw: string | null): string {
  if (!raw) return '/';
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.includes('\\')) return '/';
  return raw;
}
