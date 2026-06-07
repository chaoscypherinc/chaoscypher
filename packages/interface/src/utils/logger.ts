// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Environment-aware logging utility.
 *
 * `error` and `warn` always emit so production devtools can surface real
 * problems (ErrorBoundary catches, upload failures, graph load failures).
 * `info` and `debug` are gated on dev builds to keep the prod console quiet.
 */
const isDev = import.meta.env.DEV;

export const logger = {
  error: (...args: unknown[]): void => {
    console.error(...args);
  },
  warn: (...args: unknown[]): void => {
    console.warn(...args);
  },
  info: (...args: unknown[]): void => {
    if (isDev) console.log(...args);
  },
  debug: (...args: unknown[]): void => {
    if (isDev) console.debug(...args);
  },
};
