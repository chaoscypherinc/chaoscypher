// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Search mode for the omnibar — default mode with no prefix.
 * Searches entities, sources, and chunks in parallel with grouped results.
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Box, Typography } from '@mui/material';
import { useNavigate } from 'react-router';
import { searchApi } from '../../../services/api/search';
import { sourcesApi } from '../../../services/api/sources';
import { useAppConfig } from '../../../contexts/useAppConfig';
import { useRecentItems } from '../useRecentItems';
import { POLLING_INTERVALS } from '../../../constants/config';
import type { SearchResult, SourceSummary } from '../../../types';
import type { ModeResultsProps } from '../types';
import { ChaosCypherPalette, ChaosCypherNeutrals } from '../../../theme/palette';

interface GroupedResults {
  entities: SearchResult[];
  sources: SourceSummary[];
  chunks: SearchResult[];
}

const CATEGORY_CONFIG = [
  { key: 'entities' as const, label: 'Entities', color: ChaosCypherPalette.primary, icon: '◆' },
  { key: 'sources' as const, label: 'Sources', color: ChaosCypherPalette.secondary, icon: '◆' },
  { key: 'chunks' as const, label: 'Chunks', color: ChaosCypherPalette.purple, icon: '◆' },
];

function highlightMatch(text: string, query: string, color: string): React.ReactNode {
  if (!query || !text) return text;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return text;
  return (
    <>
      {text.slice(0, idx)}
      <Box
        component="mark"
        sx={{ bgcolor: `${color}33`, color, px: 0.25, borderRadius: '2px' }}
      >
        {text.slice(idx, idx + query.length)}
      </Box>
      {text.slice(idx + query.length)}
    </>
  );
}

export function SearchMode({ query, selectedIndex, onClose, onItemCount }: ModeResultsProps) {
  const navigate = useNavigate();
  const config = useAppConfig();
  const { addRecentItem } = useRecentItems();
  const [results, setResults] = useState<GroupedResults>({ entities: [], sources: [], chunks: [] });
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const entityLimit = config.search_omnibar_entity_limit;
  const sourceLimit = config.search_omnibar_source_limit;

  // Debounced search
  useEffect(() => {
    if (query.length < 2) {
      setResults({ entities: [], sources: [], chunks: [] });
      onItemCount(0);
      return;
    }

    setLoading(true);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const [searchResults, sourceResponse] = await Promise.all([
          searchApi.hybrid(query, entityLimit),
          sourcesApi.list({ search: query, page_size: sourceLimit }),
        ]);

        const entities = searchResults.filter((r) => r.result_type === 'node');
        const chunks = searchResults.filter((r) => r.result_type === 'chunk');
        const sources = sourceResponse.data ?? [];

        setResults({ entities, sources, chunks });
        onItemCount(entities.length + sources.length + chunks.length);
      } catch {
        setResults({ entities: [], sources: [], chunks: [] });
        onItemCount(0);
      } finally {
        setLoading(false);
      }
    }, POLLING_INTERVALS.SEARCH_DEBOUNCE);

    return () => clearTimeout(debounceRef.current);
  }, [query, onItemCount, entityLimit, sourceLimit]);

  // Build flat item list for keyboard navigation
  const flatItems = useMemo(
    () => [
      ...results.entities.map((r) => ({ type: 'entity' as const, data: r })),
      ...results.sources.map((s) => ({ type: 'source' as const, data: s })),
      ...results.chunks.map((r) => ({ type: 'chunk' as const, data: r })),
    ],
    [results.entities, results.sources, results.chunks],
  );

  const handleExecute = useCallback(
    (index: number) => {
      const item = flatItems[index];
      if (!item) return;

      if (item.type === 'entity') {
        const sr = item.data as SearchResult;
        if (sr.node) {
          addRecentItem({
            id: sr.node.id,
            type: 'entity',
            title: sr.node.label ?? 'Untitled',
            subtitle: 'Entity',
            icon: '🔵',
          });
          navigate(`/nodes/${sr.node.id}`);
        }
      } else if (item.type === 'source') {
        const source = item.data as SourceSummary;
        addRecentItem({
          id: source.id,
          type: 'source',
          title: source.title ?? source.filename,
          subtitle: source.source_type ?? 'Source',
          icon: '📄',
        });
        navigate(`/sources/${source.id}`);
      } else if (item.type === 'chunk') {
        const sr = item.data as SearchResult;
        if (sr.chunk) {
          navigate(
            `/sources/${sr.chunk.source_id}?highlight=${sr.chunk.chunk_id}`,
          );
        }
      }

      onClose();
    },
    [flatItems, navigate, onClose, addRecentItem],
  );

  // Handle Enter key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && flatItems.length > 0) {
        e.preventDefault();
        handleExecute(selectedIndex);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedIndex, flatItems.length, handleExecute]);

  const totalResults = flatItems.length;
  if (query.length < 2) return null;

  if (loading && totalResults === 0) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <Typography sx={{ color: ChaosCypherNeutrals.textMuted, fontSize: 13 }}>Searching...</Typography>
      </Box>
    );
  }

  if (!loading && totalResults === 0) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <Typography sx={{ color: ChaosCypherNeutrals.textMuted, fontSize: 13 }}>
          No results for &ldquo;{query}&rdquo;
        </Typography>
      </Box>
    );
  }

  let globalIndex = 0;

  return (
    <Box>
      {CATEGORY_CONFIG.map(({ key, label, color, icon }) => {
        const items: unknown[] = key === 'sources' ? results.sources : results[key];
        if (items.length === 0) return null;

        const startIndex = globalIndex;

        const rendered = (
          <Box key={key} sx={{ px: 2.5, pt: 1.5, pb: 0.5 }}>
            <Typography
              sx={{
                fontSize: 11,
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
                color,
                mb: 1,
                display: 'flex',
                alignItems: 'center',
                gap: 0.75,
              }}
            >
              <span>{icon}</span> {label}
              <Box component="span" sx={{ color: ChaosCypherNeutrals.borderDivider, fontSize: 10, ml: 0.5 }}>
                {items.length} result{items.length !== 1 ? 's' : ''}
              </Box>
            </Typography>

            {items.map((item, i) => {
              const itemIndex = startIndex + i;
              const isSelected = itemIndex === selectedIndex;

              let title = '';
              let subtitle = '';
              let itemIcon = '🔵';

              if (key === 'entities') {
                const sr = item as SearchResult;
                title = sr.node?.label ?? 'Untitled';
                const edges = sr.node?.edge_count ?? 0;
                subtitle = `Entity · ${edges} connection${edges === 1 ? '' : 's'}`;
                itemIcon = '🔵';
              } else if (key === 'sources') {
                const source = item as SourceSummary;
                title = source.title ?? source.filename;
                subtitle = `${source.source_type ?? 'File'} · ${source.chunk_count} chunks · ${source.status}`;
                itemIcon = '📄';
              } else if (key === 'chunks') {
                const sr = item as SearchResult;
                title = sr.chunk?.content?.slice(0, 100) ?? '';
                subtitle = `${sr.chunk?.filename ?? ''} · Page ${sr.chunk?.page_number ?? '?'}`;
                itemIcon = '📝';
              }

              return (
                <Box
                  key={`${key}-${i}`}
                  data-selected={isSelected || undefined}
                  onClick={() => handleExecute(itemIndex)}
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    p: '10px 12px',
                    borderRadius: '8px',
                    mb: 0.5,
                    gap: 1.5,
                    cursor: 'pointer',
                    bgcolor: isSelected ? `${color}14` : 'transparent',
                    border: isSelected ? `1px solid ${color}26` : '1px solid transparent',
                    '&:hover': { bgcolor: `${color}14` },
                  }}
                >
                  <Box
                    sx={{
                      width: 32,
                      height: 32,
                      borderRadius: '6px',
                      bgcolor: isSelected ? `${color}1F` : ChaosCypherNeutrals.surfaceRaised,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 14,
                    }}
                  >
                    {itemIcon}
                  </Box>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography
                      noWrap
                      sx={{
                        color: key === 'chunks' ? ChaosCypherNeutrals.textSecondary : ChaosCypherNeutrals.textPrimary,
                        fontSize: key === 'chunks' ? 13 : 14,
                        lineHeight: 1.4,
                      }}
                    >
                      {highlightMatch(title, query, color)}
                    </Typography>
                    <Typography noWrap sx={{ color: 'text.disabled', fontSize: 12 }}>
                      {subtitle}
                    </Typography>
                  </Box>
                  {isSelected && (
                    <Typography
                      sx={{
                        bgcolor: ChaosCypherNeutrals.surfaceRaised,
                        px: 0.6,
                        py: 0.1,
                        borderRadius: '3px',
                        color: ChaosCypherNeutrals.textMuted,
                        fontSize: 11,
                      }}
                    >
                      ↵
                    </Typography>
                  )}
                </Box>
              );
            })}
          </Box>
        );

        globalIndex += items.length;
        return rendered;
      })}
    </Box>
  );
}
