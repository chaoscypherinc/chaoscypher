// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Data-loading hook for the ExtractionTab.
 * Manages state for entities, relationships, templates, pagination, sorting, and loading.
 *
 * Migrated 2026-05-25 from raw `useEffect` + `useState` to TanStack Query.
 * Each sub-tab's list is a source-scoped read under the shared
 * ``['source', sourceId, …]`` key family (so source-scoped mutations that
 * invalidate the parent key cascade here). Only the active sub-tab's query
 * is ``enabled``; switching tabs flips which query runs. The eager
 * all-templates fetch (for the icon/colour lookup map) always runs.
 */

import { useCallback, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { sourcesApi } from '../../../../services/api/sources';
import type { ExtractedEntity, InferredRelationship } from '../../../../types';
import type { SourceTemplate } from './types';
import { PAGE_SIZE } from './types';

/** Return type of the useExtractionData hook. */
interface ExtractionData {
  subTab: number;
  setSubTab: (tab: number) => void;
  entities: ExtractedEntity[];
  relationships: InferredRelationship[];
  templates: SourceTemplate[];
  entitiesPage: number;
  setEntitiesPage: (page: number) => void;
  relationshipsPage: number;
  setRelationshipsPage: (page: number) => void;
  templatesPage: number;
  setTemplatesPage: (page: number) => void;
  sortBy: string;
  setSortBy: (sort: string) => void;
  sortOrder: string;
  setSortOrder: (order: string) => void;
  loading: boolean;
  templateNameMap: Map<string, SourceTemplate>;
  pageSize: number;
}

/** Hook that loads extraction data for all three sub-tabs. */
export function useExtractionData(sourceId: string): ExtractionData {
  const [subTab, setSubTab] = useState(0);
  const [entitiesPage, setEntitiesPage] = useState(1);
  const [relationshipsPage, setRelationshipsPage] = useState(1);
  const [templatesPage, setTemplatesPage] = useState(1);
  const [sortBy, setSortByState] = useState('default');
  const [sortOrder, setSortOrderState] = useState('desc');

  // Changing the sort resets the entities list to page 1. Done in the setter
  // (rather than a sort-watching effect) to avoid a cascading-render lint
  // warning — the page reset and the sort change land in the same render.
  const setSortBy = useCallback((sort: string) => {
    setSortByState(sort);
    setEntitiesPage(1);
  }, []);
  const setSortOrder = useCallback((order: string) => {
    setSortOrderState(order);
    setEntitiesPage(1);
  }, []);

  // Eagerly load all templates for icon/color lookup (independent of sub-tab).
  const allTemplatesQuery = useQuery({
    queryKey: ['source', sourceId, 'extraction-all-templates'] as const,
    queryFn: () => sourcesApi.getTemplates(sourceId, 1, 1000),
    enabled: sourceId != null,
  });

  // Build a name-based lookup map for templates (lowercase name -> template)
  const templateNameMap = useMemo(() => {
    const map = new Map<string, SourceTemplate>();
    for (const t of allTemplatesQuery.data?.templates ?? []) {
      map.set(t.name.toLowerCase(), t);
    }
    return map;
  }, [allTemplatesQuery.data]);

  const entitiesQuery = useQuery({
    queryKey: ['source', sourceId, 'extraction-entities', entitiesPage, sortBy, sortOrder] as const,
    queryFn: () => sourcesApi.getEntities(sourceId, entitiesPage, PAGE_SIZE, sortBy, sortOrder),
    enabled: sourceId != null && subTab === 0,
  });

  const relationshipsQuery = useQuery({
    queryKey: ['source', sourceId, 'extraction-relationships', relationshipsPage] as const,
    queryFn: () => sourcesApi.getRelationships(sourceId, relationshipsPage, PAGE_SIZE),
    enabled: sourceId != null && subTab === 1,
  });

  const templatesQuery = useQuery({
    queryKey: ['source', sourceId, 'extraction-templates', templatesPage] as const,
    queryFn: () => sourcesApi.getTemplates(sourceId, templatesPage, PAGE_SIZE),
    enabled: sourceId != null && subTab === 2,
  });

  // The active sub-tab's query drives the loading spinner. `isLoading` is only
  // true while a query is enabled and has no cached data yet, mirroring the
  // legacy behaviour where `loading` flipped per active fetch.
  const loading =
    subTab === 0
      ? entitiesQuery.isLoading
      : subTab === 1
        ? relationshipsQuery.isLoading
        : templatesQuery.isLoading;

  return {
    subTab,
    setSubTab,
    entities: entitiesQuery.data?.entities ?? [],
    relationships: relationshipsQuery.data?.relationships ?? [],
    templates: templatesQuery.data?.templates ?? [],
    entitiesPage,
    setEntitiesPage,
    relationshipsPage,
    setRelationshipsPage,
    templatesPage,
    setTemplatesPage,
    sortBy,
    setSortBy,
    sortOrder,
    setSortOrder,
    loading,
    templateNameMap,
    pageSize: PAGE_SIZE,
  };
}
