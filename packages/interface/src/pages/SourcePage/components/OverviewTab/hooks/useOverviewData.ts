// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { sourcesApi } from '../../../../../services/api/sources';
import { cleanTypeName } from '../formatters';

export interface TemplateInfo {
  id: string;
  name: string;
  color?: string | null;
  icon?: string | null;
}

interface UseOverviewDataReturn {
  templateList: TemplateInfo[];
  typeToTemplate: Map<string, TemplateInfo>;
}

/**
 * Fetches the template list and derives the ``typeToTemplate`` lookup
 * the distribution donuts use for chart colour + icon.
 *
 * Migrated 2026-05-25 from raw `useEffect` + `useState` to TanStack Query.
 * The template list is source-scoped, so it lives under the shared
 * ``['source', sourceId, …]`` key family; any source-scoped mutation that
 * invalidates the parent ``['source', sourceId]`` key cascades here too.
 *
 * Slimmed 2026-05-11 — the quality-score fetch + recalculate logic
 * that used to live here moved to ``pipeline/hooks/useQualityScore.ts``
 * (the shared ``pipeline/`` directory). The Overview tab uses that hook
 * for the Quality tile, which opens the QualityBreakdownDialog in place.
 *
 * (Until 2026-05-11 this hook also fetched the per-page rendered-image
 * list for an Overview-tab gallery. That gallery was folded into the
 * Chunks tab — each chunk now shows its source page thumbnail beside
 * the text — so the image fetch moved with it to the ``useSourceImages``
 * TanStack Query hook called from ``ChunksTab``.)
 */
export function useOverviewData(sourceId: string): UseOverviewDataReturn {
  const { data } = useQuery({
    queryKey: ['source', sourceId, 'overview-templates'] as const,
    queryFn: () => sourcesApi.getTemplates(sourceId, 1, 1000),
    enabled: sourceId != null,
  });

  const templateList = useMemo<TemplateInfo[]>(() => data?.templates ?? [], [data]);

  const typeToTemplate = useMemo(() => {
    const map = new Map<string, TemplateInfo>();
    for (const t of templateList) {
      const cleaned = cleanTypeName(t.name).toLowerCase();
      map.set(cleaned, t);
    }
    return map;
  }, [templateList]);

  return { templateList, typeToTemplate };
}
