// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router';
import { Chip, Tooltip, Box, Typography, Divider, Stack, CircularProgress } from '@mui/material';
import NodeIcon from '@mui/icons-material/AccountTree';
import EdgeIcon from '@mui/icons-material/Link';
import ArrowIcon from '@mui/icons-material/ArrowForward';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import TypeIcon from '@mui/icons-material/Category';
import type { EntityReferenceSummary } from '../../types';
import { nodeApi } from '../../services/api/nodes';
import { edgeApi } from '../../services/api/edges';
import { CardColors, hexToRgba } from '../../theme/cardStyles';

interface EntityReferenceProps {
  /** Entity reference data (from message metadata or API) */
  entity: EntityReferenceSummary;
  /** Callback to fetch entity data if not available */
  onFetchEntity?: (id: string, type: 'node' | 'edge') => Promise<EntityReferenceSummary | null>;
  /** Whether entity data is currently loading */
  isLoading?: boolean;
}

/**
 * Get color based on entity type and template
 */
function getEntityColor(entity: EntityReferenceSummary): string {
  if (entity.type === 'edge') {
    return CardColors.error;
  }
  return CardColors.primary;
}

/** Values that look like internal identifiers — never useful in a hover. */
const ID_LIKE_VALUE = /^(node_|edge_|tpl_|[0-9a-f]{8}-)/i;

/** Max property rows shown in the hover card. */
const MAX_HOVER_PROPS = 3;

/** Max characters of a property value before truncation. */
const MAX_PROP_VALUE_CHARS = 60;

/**
 * Pick the most useful property rows for the hover card: priority keys
 * first, then fill in declaration order; id-like keys/values, objects,
 * and the description (shown separately) are skipped; long values are
 * truncated.
 */
function topProperties(
  props: EntityReferenceSummary['properties'],
): Array<{ key: string; value: string }> {
  if (!props) return [];
  const out: Array<{ key: string; value: string }> = [];
  const seen = new Set<string>();
  const pick = (key: string) => {
    if (out.length >= MAX_HOVER_PROPS || seen.has(key)) return;
    const raw = props[key];
    if (raw == null || typeof raw === 'object') return;
    const lower = key.toLowerCase();
    if (lower === 'description' || lower === 'id' || lower.endsWith('_id')) return;
    const text = String(raw);
    if (!text || ID_LIKE_VALUE.test(text)) return;
    seen.add(key);
    out.push({
      key,
      value:
        text.length > MAX_PROP_VALUE_CHARS
          ? `${text.slice(0, MAX_PROP_VALUE_CHARS - 3)}...`
          : text,
    });
  };
  for (const key of ['type', 'status', 'category']) pick(key);
  for (const key of Object.keys(props)) pick(key);
  return out;
}

/**
 * Tooltip content component
 */
function TooltipContent({ entity, fetching = false }: { entity: EntityReferenceSummary; fetching?: boolean }) {
  const color = entity.type === 'node' ? CardColors.primary : CardColors.error;
  const Icon = entity.type === 'node' ? NodeIcon : EdgeIcon;

  const description = entity.description || (entity.properties?.description as string | undefined);
  // Raw IDs are never shown — entity_type / template_name or nothing.
  const entityTypeLabel = entity.entity_type || entity.template_name;

  const keyProps = topProperties(entity.properties);

  return (
    <Stack spacing={1} sx={{ maxWidth: 300 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1 }}>
        <Icon sx={{ color, fontSize: '1.2rem', mt: 0.2 }} />
        <Box>
          <Typography variant="subtitle2" sx={{
            fontWeight: 600
          }}>
            {entity.label}
          </Typography>
          {entityTypeLabel && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <TypeIcon sx={{ fontSize: '0.75rem', color: 'text.secondary' }} />
              <Typography variant="caption" sx={{
                color: "text.secondary"
              }}>
                {entityTypeLabel}
              </Typography>
            </Box>
          )}
        </Box>
      </Box>
      {/* Description */}
      {description && (
        <Typography
          variant="caption"
          sx={{
            color: "text.secondary",
            fontStyle: 'italic',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden'
          }}>
          {description}
        </Typography>
      )}
      {/* Relationship counts */}
      {entity.type === 'node' && (entity.incoming_count != null || entity.outgoing_count != null) && (
        <Box sx={{ display: 'flex', gap: 2 }}>
          {entity.incoming_count != null && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <ArrowBackIcon sx={{ fontSize: '0.8rem' }} />
              <Typography variant="caption">{entity.incoming_count} in</Typography>
            </Box>
          )}
          {entity.outgoing_count != null && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <ArrowIcon sx={{ fontSize: '0.8rem' }} />
              <Typography variant="caption">{entity.outgoing_count} out</Typography>
            </Box>
          )}
        </Box>
      )}
      {/* Key properties */}
      {keyProps.length > 0 && (
        <>
          <Divider sx={{ my: 0.5 }} />
          {keyProps.map(({ key, value }) => (
            <Box key={key} sx={{ display: 'flex', justifyContent: 'space-between', gap: 2 }}>
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  textTransform: 'capitalize'
                }}>
                {key}:
              </Typography>
              <Typography variant="caption" sx={{
                fontWeight: 500
              }}>
                {value}
              </Typography>
            </Box>
          ))}
        </>
      )}
      {/* Lazy-fetch progress */}
      {fetching && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
          <CircularProgress size={12} />
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            Loading details…
          </Typography>
        </Box>
      )}
      {/* Click hint */}
      <Typography
        variant="caption"
        sx={{
          color: "text.disabled",
          mt: 0.5
        }}>
        Click to view details
      </Typography>
    </Stack>
  );
}

/**
 * Module-level cache of lazily fetched entity summaries (keyed by entity
 * id) so repeated hovers across messages never refetch.
 */
const fetchedDetailCache = new Map<string, EntityReferenceSummary>();

/**
 * Default detail fetcher over the nodes/edges services. Used when no
 * `onFetchEntity` is injected, so hover details are universal regardless
 * of which tools ran this turn. Failures resolve to null (silent degrade).
 */
async function defaultFetchEntity(
  id: string,
  type: 'node' | 'edge',
): Promise<EntityReferenceSummary | null> {
  try {
    const raw = type === 'node' ? await nodeApi.getFull(id) : await edgeApi.get(id);
    const data = (raw && typeof raw === 'object' && 'data' in (raw as object)
      ? (raw as { data: unknown }).data
      : raw) as Record<string, unknown> | null;
    if (!data || typeof data !== 'object') return null;
    return {
      id,
      type,
      label: (data.label as string) || (data.name as string) || '',
      description: (data.description as string) || undefined,
      entity_type: (data.entity_type as string) || undefined,
      template_name: (data.template_name as string) || undefined,
      properties: (data.properties as Record<string, unknown>) || undefined,
      incoming_count: data.incoming_count as number | undefined,
      outgoing_count: data.outgoing_count as number | undefined,
    };
  } catch {
    return null;
  }
}

/** True when the entity already carries something worth hovering for. */
function hasDetails(entity: EntityReferenceSummary): boolean {
  return Boolean(
    entity.description || (entity.properties && Object.keys(entity.properties).length > 0),
  );
}

/**
 * Inline entity reference component.
 * Displays as a styled chip that shows entity details on hover
 * and navigates to the entity detail page on click.
 */
export default function EntityReference({
  entity,
  onFetchEntity,
  isLoading = false,
}: EntityReferenceProps) {
  const navigate = useNavigate();
  const color = getEntityColor(entity);
  const Icon = entity.type === 'node' ? NodeIcon : EdgeIcon;

  // Lazily fetched details for chips the turn's tools didn't describe.
  const [fetched, setFetched] = useState<EntityReferenceSummary | null>(
    () => fetchedDetailCache.get(entity.id) ?? null,
  );
  const [fetching, setFetching] = useState(false);
  const displayEntity = fetched ? { ...entity, ...fetched, label: entity.label } : entity;

  const handleTooltipOpen = useCallback(() => {
    if (fetched || fetching || hasDetails(entity)) return;
    const cached = fetchedDetailCache.get(entity.id);
    if (cached) {
      setFetched(cached);
      return;
    }
    const fetcher = onFetchEntity ?? defaultFetchEntity;
    setFetching(true);
    fetcher(entity.id, entity.type)
      .then((summary) => {
        if (summary) {
          fetchedDetailCache.set(entity.id, summary);
          setFetched(summary);
        }
      })
      .catch(() => {
        // Silent degrade: the basic card is still useful.
      })
      .finally(() => setFetching(false));
  }, [entity, fetched, fetching, onFetchEntity]);

  const handleClick = useCallback((event: React.MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    // Normalize ID: add prefix if missing (IDs should be like "node_uuid" or "edge_uuid")
    let normalizedId = entity.id;
    const prefix = entity.type === 'node' ? 'node_' : 'edge_';
    if (!entity.id.startsWith(prefix)) {
      normalizedId = `${prefix}${entity.id}`;
    }
    const path = entity.type === 'node' ? `/nodes/${normalizedId}` : `/edges/${normalizedId}`;
    navigate(path);
  }, [entity.id, entity.type, navigate]);

  return (
    <Tooltip
      title={<TooltipContent entity={displayEntity} fetching={fetching || isLoading} />}
      onOpen={handleTooltipOpen}
      arrow
      enterDelay={300}
      leaveDelay={100}
      placement="top"
      slotProps={{
        tooltip: {
          sx: {
            bgcolor: 'background.paper',
            color: 'text.primary',
            boxShadow: 3,
            border: `1px solid ${hexToRgba(color, 0.3)}`,
            p: 1.5,
            '& .MuiTooltip-arrow': {
              color: 'background.paper',
              '&::before': {
                border: `1px solid ${hexToRgba(color, 0.3)}`,
              },
            },
          },
        },
      }}
    >
      <Chip
        component="span"
        size="small"
        icon={<Icon sx={{ fontSize: '0.9rem !important' }} />}
        label={entity.label}
        onClick={handleClick}
        sx={{
          height: 'auto',
          py: 0.25,
          px: 0.5,
          mx: 0.25,
          my: 0.5,  // Vertical margin for spacing between lines with chips
          fontSize: '0.85rem',
          fontWeight: 500,
          cursor: 'pointer',
          backgroundColor: hexToRgba(color, 0.15),
          border: `1px solid ${hexToRgba(color, 0.4)}`,
          color: 'text.primary',
          transition: 'all 0.2s ease-in-out',
          '&:hover': {
            backgroundColor: hexToRgba(color, 0.25),
            transform: 'translateY(-1px)',
          },
          '& .MuiChip-icon': {
            color: color,
            marginLeft: '4px',
            marginRight: '-2px',
          },
          '& .MuiChip-label': {
            padding: '2px 6px',
          },
        }}
      />
    </Tooltip>
  );
}
