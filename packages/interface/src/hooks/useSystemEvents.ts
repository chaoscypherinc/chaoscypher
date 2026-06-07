// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useSystemEvents: Hook for polling system events.
 *
 * Polls GET /api/v1/system/processing/events at a 30-second interval
 * and provides the event list for the Settings Events tab.
 */

import { useState, useCallback } from 'react';
import { eventsApi, type SystemEvent } from '../services/api/events';
import { usePolling } from './usePolling';
import { POLLING_INTERVALS } from '../constants/config';

interface UseSystemEventsResult {
  /** Latest system events list */
  events: SystemEvent[];
  /** Whether the initial load is in progress */
  loading: boolean;
  /** Delete all events and refresh the list */
  clearEvents: () => Promise<void>;
}

export function useSystemEvents(limit: number = 50, type?: string): UseSystemEventsResult {
  const [events, setEvents] = useState<SystemEvent[]>([]);
  const [loading, setLoading] = useState(true);

  const poll = useCallback(async () => {
    try {
      const data = await eventsApi.listEvents({ type, limit });
      setEvents(data);
      setLoading(false);
    } catch {
      setLoading(false);
    }
  }, [type, limit]);

  usePolling({
    onPoll: poll,
    interval: POLLING_INTERVALS.HEALTH_CHECK,
    pauseWhenHidden: true,
    immediate: true,
  });

  const clearEvents = useCallback(async () => {
    await eventsApi.clearEvents();
    setEvents([]);
  }, []);

  return { events, loading, clearEvents };
}
