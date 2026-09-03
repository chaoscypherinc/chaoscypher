// SPDX-License-Identifier: AGPL-3.0-only
// SPDX-FileCopyrightText: 2026 Denis MacPherson

import { describe, expect, it } from "vitest";

import { queryClient, shouldRetryQuery } from "../queryClient";

/** Minimal ApiClientError-shaped object (matches the isApiError guard). */
function apiError(status: number | null) {
  return { isApiError: true as const, status, code: "http_error", message: "boom" };
}

describe("shouldRetryQuery", () => {
  it("never retries past the first failure", () => {
    expect(shouldRetryQuery(1, apiError(503))).toBe(false);
    expect(shouldRetryQuery(2, new TypeError("network"))).toBe(false);
  });

  it("does not retry deterministic 4xx client errors", () => {
    for (const status of [400, 401, 403, 404, 409, 422]) {
      expect(shouldRetryQuery(0, apiError(status))).toBe(false);
    }
  });

  it("retries 5xx and network-level failures once", () => {
    expect(shouldRetryQuery(0, apiError(500))).toBe(true);
    expect(shouldRetryQuery(0, apiError(503))).toBe(true);
    expect(shouldRetryQuery(0, apiError(null))).toBe(true);
    expect(shouldRetryQuery(0, new TypeError("Failed to fetch"))).toBe(true);
  });

  it("is wired as the global query retry option", () => {
    expect(queryClient.getDefaultOptions().queries?.retry).toBe(shouldRetryQuery);
  });
});
