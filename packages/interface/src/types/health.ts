// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Health check types for system status monitoring.
 */

export interface HealthCheckItem {
  status: 'ok' | 'warning' | 'error';
  message: string;
  details?: Record<string, unknown>;
  category?: 'resource' | 'service' | 'operational';
  auto_recoverable?: boolean;
}

export interface HealthCheckResponse {
  healthy: boolean;
  /** "ok" when healthy, "degraded" otherwise. Always present. */
  status: string;
  /**
   * Per-subsystem check details. Present only for authenticated callers.
   * Unauthenticated callers (e.g. Docker HEALTHCHECK) receive only
   * `{healthy, status}` to avoid fingerprinting the LLM stack from the LAN.
   */
  checks?: Record<string, HealthCheckItem>;
}
