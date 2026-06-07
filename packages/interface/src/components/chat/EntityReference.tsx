// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback } from 'react';
import { useNavigate } from 'react-router';
import { Chip, Tooltip, Box, Typography, Divider, Stack } from '@mui/material';
import NodeIcon from '@mui/icons-material/AccountTree';
import EdgeIcon from '@mui/icons-material/Link';
import ArrowIcon from '@mui/icons-material/ArrowForward';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import TypeIcon from '@mui/icons-material/Category';
import type { EntityReferenceSummary } from '../../types';
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

/**
 * Tooltip content component
 */
function TooltipContent({ entity }: { entity: EntityReferenceSummary }) {
  const color = entity.type === 'node' ? CardColors.primary : CardColors.error;
  const Icon = entity.type === 'node' ? NodeIcon : EdgeIcon;

  const description = entity.description || (entity.properties?.description as string | undefined);
  const entityTypeLabel = entity.entity_type || entity.template_name || entity.template_id;

  // Get a few key properties
  const keyProps: Array<{ key: string; value: string }> = [];
  if (entity.properties) {
    const priorityKeys = ['type', 'status', 'category'];
    for (const key of priorityKeys) {
      if (entity.properties[key] && keyProps.length < 2) {
        keyProps.push({ key, value: String(entity.properties[key]) });
      }
    }
  }

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
 * Inline entity reference component.
 * Displays as a styled chip that shows entity details on hover
 * and navigates to the entity detail page on click.
 */
export default function EntityReference({
  entity,
  onFetchEntity: _onFetchEntity,
  isLoading: _isLoading = false,
}: EntityReferenceProps) {
  const navigate = useNavigate();
  const color = getEntityColor(entity);
  const Icon = entity.type === 'node' ? NodeIcon : EdgeIcon;

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
      title={<TooltipContent entity={entity} />}
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
