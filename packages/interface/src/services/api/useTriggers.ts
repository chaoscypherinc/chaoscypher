// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for triggers.
 *
 * The toggle mutation (`useUpdateTrigger`) is optimistic: the list
 * updates immediately via `setQueryData`, rolls back on error, and
 * re-syncs from the server on settle. Replaces the previous local-state
 * update + manual error-on-fail-with-no-rollback pattern in
 * `TriggersPage/index.tsx`.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { triggersApi, type Trigger, type TriggerUpdate } from './triggers';

const TRIGGERS_QUERY_KEY = ['triggers'] as const;

function triggerStatsQueryKey(triggerId: string) {
  return ['triggers', triggerId, 'stats'] as const;
}

export function useTriggers() {
  return useQuery({
    queryKey: TRIGGERS_QUERY_KEY,
    queryFn: () => triggersApi.list(),
  });
}

export function useTriggerStats(triggerId: string | null) {
  return useQuery({
    queryKey: triggerId ? triggerStatsQueryKey(triggerId) : ['triggers', 'stats', 'none'],
    queryFn: () => triggersApi.getStats(triggerId as string),
    enabled: triggerId != null,
  });
}

interface UpdateTriggerVars {
  id: string;
  patch: TriggerUpdate;
}

interface UpdateTriggerContext {
  previous: Trigger[] | undefined;
}

export function useUpdateTrigger() {
  const qc = useQueryClient();
  return useMutation<Trigger, Error, UpdateTriggerVars, UpdateTriggerContext>({
    mutationFn: ({ id, patch }) => triggersApi.update(id, patch),
    onMutate: async ({ id, patch }) => {
      await qc.cancelQueries({ queryKey: TRIGGERS_QUERY_KEY });
      const previous = qc.getQueryData<Trigger[]>(TRIGGERS_QUERY_KEY);
      qc.setQueryData<Trigger[]>(TRIGGERS_QUERY_KEY, (old) =>
        old?.map((t) => (t.id === id ? { ...t, ...patch } : t)) ?? old,
      );
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) {
        qc.setQueryData(TRIGGERS_QUERY_KEY, ctx.previous);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: TRIGGERS_QUERY_KEY });
    },
  });
}
