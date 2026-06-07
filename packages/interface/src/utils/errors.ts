// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Error helpers for narrowing `unknown` caught values down to user-displayable
 * strings. Centralised here so every component doesn't have to re-invent the
 * `ApiClientError | Error | string | unknown` ladder.
 */

interface HttpLikeError {
  response?: {
    data?: {
      // Unified envelope (2026-04-18 backend change): {error, message, details}.
      // `message` is the human-readable string; `error` is a stable machine code.
      message?: string;
      error?: string;
      details?: unknown;
      // Legacy FastAPI shape (still surfaces in rare edge cases, e.g. before
      // the server-side http_exception_handler runs): {detail: "..."}.
      detail?: string | { message?: string; code?: string };
    };
    status?: number;
  };
  message?: string;
  code?: string;
  name?: string;
}

/**
 * Best-effort extraction of a user-facing message from a caught error.
 *
 * Order of preference:
 *   1. `error.response.data.message` (unified envelope, primary)
 *   2. `error.response.data.detail.message` (legacy nested ErrorDetail)
 *   3. `error.response.data.detail` when string (oldest FastAPI shape)
 *   4. `error.message` (standard Error fallback)
 *   5. String representation of the value
 */
export function getApiErrorMessage(err: unknown): string {
  if (!err) return 'Unknown error';

  if (typeof err === 'string') return err;

  const extractBody = (body: HttpLikeError['response']): string | undefined => {
    if (!body?.data) return undefined;
    const data = body.data;
    if (typeof data.message === 'string') return data.message;
    if (data.detail && typeof data.detail === 'object' && typeof data.detail.message === 'string') {
      return data.detail.message;
    }
    if (typeof data.detail === 'string') return data.detail;
    return undefined;
  };

  if (err instanceof Error) {
    const httpErr = err as HttpLikeError;
    return extractBody(httpErr.response) ?? err.message;
  }

  if (typeof err === 'object') {
    const httpErr = err as HttpLikeError;
    const fromBody = extractBody(httpErr.response);
    if (fromBody) return fromBody;
    if (httpErr.message) return httpErr.message;
  }

  try {
    return JSON.stringify(err);
  } catch {
    return String(err);
  }
}

/**
 * Returns true when the caught value looks like a "request was aborted by the
 * caller" error from fetch or the API client.
 */
export function isAbortError(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false;
  const e = err as HttpLikeError;
  return e.name === 'AbortError' || e.code === 'ERR_CANCELED';
}
