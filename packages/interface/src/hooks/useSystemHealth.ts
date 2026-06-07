// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useSystemHealth: Hook for polling system health status.
 *
 * Polls GET /api/v1/health at a 30-second interval and provides
 * the current health state for the MiniSystemStatus component.
 */

import { useState, useCallback } from 'react';
import { healthApi } from '../services/api/health';
import { usePolling } from './usePolling';
import { POLLING_INTERVALS } from '../constants/config';
import type { HealthCheckResponse } from '../types/health';
import { logger } from '../utils/logger';

interface UseSystemHealthResult {
  /** Latest health check response */
  health: HealthCheckResponse | null;
  /** Whether the initial load is in progress */
  loading: boolean;
  /** Whether any check has error status */
  hasErrors: boolean;
  /** Whether any check has warning status */
  hasWarnings: boolean;
}

export function useSystemHealth(): UseSystemHealthResult {
  const [health, setHealth] = useState<HealthCheckResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const pollHealth = useCallback(async () => {
    try {
      const data = await healthApi.getHealth();
      setHealth(data);
      setLoading(false);
    } catch (error) {
      logger.error('Health check failed:', error);
      setLoading(false);
    }
  }, []);

  usePolling({
    onPoll: pollHealth,
    interval: POLLING_INTERVALS.HEALTH_CHECK,
    pauseWhenHidden: true,
    immediate: true,
  });

  const hasErrors = health?.checks
    ? Object.values(health.checks).some(c => c.status === 'error')
    : false;

  const hasWarnings = health?.checks
    ? Object.values(health.checks).some(c => c.status === 'warning')
    : false;

  return { health, loading, hasErrors, hasWarnings };
}
