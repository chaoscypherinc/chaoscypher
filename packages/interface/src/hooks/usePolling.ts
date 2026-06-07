// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useRef, useCallback } from 'react';
import { logger } from '../utils/logger';

interface UsePollingOptions {
  /**
   * Callback function to execute on each poll interval
   */
  onPoll: () => void | Promise<void>;

  /**
   * Polling interval in milliseconds
   * @default 2000
   */
  interval?: number;

  /**
   * Whether polling is enabled
   * @default true
   */
  enabled?: boolean;

  /**
   * Whether to pause polling when tab is not visible
   * @default true
   */
  pauseWhenHidden?: boolean;

  /**
   * Whether to invoke onPoll immediately before starting the interval
   * @default false
   */
  immediate?: boolean;
}

/**
 * Custom hook for polling with configurable interval
 *
 * Features:
 * - Configurable polling interval
 * - Automatic cleanup on unmount
 * - Pause when tab hidden (optional)
 * - Type-safe callback
 *
 * @remarks
 * The `onPoll` callback MUST be wrapped in `useCallback` at the call site.
 * An unstable reference will restart the polling interval on every render.
 *
 * @example
 * ```tsx
 * usePolling({
 *   onPoll: async () => {
 *     const sources = await sourcesApi.listProcessing();
 *     setSources(sources);
 *   },
 *   interval: 3000,
 *   enabled: true
 * });
 * ```
 */
export function usePolling({
  onPoll,
  interval = 2000,
  enabled = true,
  pauseWhenHidden = true,
  immediate = false,
}: UsePollingOptions) {
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isVisibleRef = useRef(true);

  // Handle visibility change
  const handleVisibilityChange = useCallback(() => {
    isVisibleRef.current = document.visibilityState === 'visible';
  }, []);

  // Start polling
  const startPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
    }

    intervalRef.current = setInterval(async () => {
      // Skip if paused and tab is hidden
      if (pauseWhenHidden && !isVisibleRef.current) {
        return;
      }

      try {
        await onPoll();
      } catch (error) {
        logger.error('Polling error:', error);
      }
    }, interval);
  }, [onPoll, interval, pauseWhenHidden]);

  // Stop polling
  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  // Setup polling and visibility listener
  useEffect(() => {
    if (!enabled) {
      stopPolling();
      return;
    }

    // Add visibility listener if needed
    if (pauseWhenHidden) {
      document.addEventListener('visibilitychange', handleVisibilityChange);
    }

    // Fire immediately if requested (runs on mount and whenever onPoll changes,
    // e.g. when a filter parameter changes)
    if (immediate) {
      Promise.resolve(onPoll()).catch((err) => {
        logger.error('Initial poll failed:', err);
      });
    }

    // Start polling
    startPolling();

    // Cleanup
    return () => {
      stopPolling();
      if (pauseWhenHidden) {
        document.removeEventListener('visibilitychange', handleVisibilityChange);
      }
    };
  // onPoll is intentionally in deps — restarts polling when the callback changes
  // (e.g. when filter params change). Callers must memoize with useCallback.
  }, [enabled, startPolling, stopPolling, handleVisibilityChange, pauseWhenHidden, immediate, onPoll]);

  return {
    startPolling,
    stopPolling,
  };
}
