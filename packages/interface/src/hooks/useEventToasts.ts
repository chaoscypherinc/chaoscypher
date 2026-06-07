// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useEventToasts: Poll for new system events and fire toast callbacks.
 *
 * Tracks the latest seen event ID and, on each poll cycle, notifies the
 * caller about events that arrived since the previous check.  The first
 * poll is silent (baseline capture) so the user is never flooded with
 * historical toasts on page load.
 */

import { useCallback, useRef } from 'react';
import { eventsApi, type SystemEvent } from '../services/api/events';
import { usePolling } from './usePolling';
import { POLLING_INTERVALS } from '../constants/config';

/** Event types considered worth surfacing as toasts. */
const TOAST_EVENT_TYPES = new Set([
  'task_completed',
  'task_failed',
  'pause',
  'resume',
  'health_change',
  'recovery',
]);

interface UseEventToastsOptions {
  /** Called once per new significant event (newest-first order reversed to chronological). */
  onNewEvent: (event: SystemEvent) => void;
  /** Master switch — set to `false` to stop polling entirely. */
  enabled?: boolean;
}

/**
 * Poll system events and trigger toast callbacks for new arrivals.
 *
 * Only events whose `type` is in {@link TOAST_EVENT_TYPES} are forwarded.
 * The first poll silently records the latest event ID so pre-existing
 * events never produce toasts.
 */
export function useEventToasts({ onNewEvent, enabled = true }: UseEventToastsOptions): void {
  const lastSeenId = useRef<number | null>(null);
  const initialized = useRef(false);

  const poll = useCallback(async () => {
    try {
      const events = await eventsApi.listEvents({ limit: 5 });
      if (!events.length) return;

      // First poll — record baseline, no toasts
      if (!initialized.current) {
        lastSeenId.current = events[0].id;
        initialized.current = true;
        return;
      }

      // Find events newer than the last seen ID
      const newEvents = lastSeenId.current
        ? events.filter((e) => e.id > lastSeenId.current!)
        : [];

      // Advance the high-water mark
      if (events[0].id > (lastSeenId.current ?? 0)) {
        lastSeenId.current = events[0].id;
      }

      // Notify in chronological order (API returns newest-first)
      for (const event of newEvents.reverse()) {
        if (TOAST_EVENT_TYPES.has(event.type)) {
          onNewEvent(event);
        }
      }
    } catch {
      // Silent — toasts are non-critical
    }
  }, [onNewEvent]);

  usePolling({
    onPoll: poll,
    interval: POLLING_INTERVALS.EVENT_TOASTS,
    pauseWhenHidden: true,
    immediate: true,
    enabled,
  });
}
