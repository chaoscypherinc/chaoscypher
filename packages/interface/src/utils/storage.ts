// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Safe per-viewer browser storage (localStorage).
 *
 * The one place the interface touches ``localStorage``. Every accessor can
 * throw (private windows, blocked site data, quota, sandboxed previews), and
 * a stored value can be missing or garbled, so each read here falls back to
 * a default and each write reports success instead of throwing. Storage is
 * for per-viewer conveniences only (a remembered toggle, a recent list): the
 * page must render correctly when every call here fails.
 */

/** Read a raw string; ``null`` when absent or when storage is unavailable. */
export function readStoredString(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Write a raw string. Returns ``false`` when storage is unavailable. */
export function writeStoredString(key: string, value: string): boolean {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

/** Remove a key. Returns ``false`` when storage is unavailable. */
export function removeStoredItem(key: string): boolean {
  try {
    localStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

/**
 * Read a JSON value. Returns ``fallback`` when the key is absent, storage is
 * unavailable, the value does not parse, or ``isValid`` rejects it.
 */
export function readStoredJson<T>(
  key: string,
  fallback: T,
  isValid?: (value: unknown) => value is T,
): T {
  const raw = readStoredString(key);
  if (raw === null) return fallback;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (isValid && !isValid(parsed)) return fallback;
    return parsed as T;
  } catch {
    return fallback;
  }
}

/** Write a JSON value. Returns ``false`` when storage is unavailable. */
export function writeStoredJson(key: string, value: unknown): boolean {
  let raw: string;
  try {
    raw = JSON.stringify(value);
  } catch {
    return false;
  }
  return writeStoredString(key, raw);
}
