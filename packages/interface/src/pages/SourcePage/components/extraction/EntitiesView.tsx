// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Entities sub-tab of ExtractionTab.
 * Renders an entity card grid with sorting controls, collapsible details,
 * quality score badges, and pagination.
 */

import { useState } from 'react';
import {
  Box,
  Typography,
  Chip,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Tooltip,
  Collapse,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import type { ExtractedEntity } from '../../../../types';
import { StatusColors } from '../../../../theme/colors';
import { surfaceSx, surfaceHoverSx } from '../../../../theme/cardStyles';
import { ghostInputSx } from '../../../../theme/ghostStyles';
import { DEFAULT_NODE_ICON } from '../../../../utils/iconSprites';
import { getMuiIcon } from '../../../../utils/icons';
import { cleanTypeName } from '../../../../utils/formatters';
import GhostPagination from '../../../../components/GhostPagination';
import type { SourceTemplate } from './types';
import { getTypeColor } from './types';

interface EntitiesViewProps {
  entities: ExtractedEntity[];
  entitiesCount: number;
  entitiesPage: number;
  setEntitiesPage: (page: number) => void;
  sortBy: string;
  setSortBy: (sort: string) => void;
  sortOrder: string;
  setSortOrder: (order: string) => void;
  pageSize: number;
  templateNameMap: Map<string, SourceTemplate>;
}

/** Card grid of extracted entities with sorting and expandable details. */
export function EntitiesView({
  entities,
  entitiesCount,
  entitiesPage,
  setEntitiesPage,
  sortBy,
  setSortBy,
  sortOrder,
  setSortOrder,
  pageSize,
  templateNameMap,
}: EntitiesViewProps) {
  const [expandedEntities, setExpandedEntities] = useState<Set<number>>(new Set());

  return (
    <Box>
      {/* Sort Controls */}
      <Box sx={{ display: 'flex', gap: 1.5, mb: 2, alignItems: 'center' }}>
        <FormControl size="small" sx={{ minWidth: 180, ...ghostInputSx }}>
          <InputLabel>Sort by</InputLabel>
          <Select value={sortBy} label="Sort by" onChange={(e) => setSortBy(e.target.value)}>
            <MenuItem value="default">Extraction Order</MenuItem>
            <MenuItem value="quality">Quality Score</MenuItem>
            <MenuItem value="confidence">Confidence</MenuItem>
            <MenuItem value="name">Name</MenuItem>
            <MenuItem value="type">Type</MenuItem>
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 120, ...ghostInputSx }}>
          <InputLabel>Order</InputLabel>
          <Select value={sortOrder} label="Order" onChange={(e) => setSortOrder(e.target.value)}>
            <MenuItem value="desc">Descending</MenuItem>
            <MenuItem value="asc">Ascending</MenuItem>
          </Select>
        </FormControl>
      </Box>

      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1.5 }}>
        {entities.map((entity, idx) => (
          <Box
            key={entity.id || entity.name || `entity-${idx}`}
            sx={{ flex: '1 1 calc(25% - 12px)', minWidth: 250 }}
          >
            <Box
              onClick={() => {
                setExpandedEntities((prev) => {
                  const next = new Set(prev);
                  if (next.has(idx)) {
                    next.delete(idx);
                  } else {
                    next.add(idx);
                  }
                  return next;
                });
              }}
              sx={{
                p: 2,
                height: '100%',
                cursor: 'pointer',
                userSelect: 'none',
                display: 'flex',
                flexDirection: 'column',
                ...surfaceSx,
                transition: 'border-color 0.15s, background 0.15s',
                '&:hover': surfaceHoverSx,
              }}
            >
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'flex-start',
                  mb: 1,
                }}
              >
                <Typography variant="subtitle2" sx={{ fontWeight: 600, mr: 1, color: 'common.white' }}>
                  {entity.name}
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexShrink: 0 }}>
                  {expandedEntities.has(idx) && entity.confidence !== undefined && (
                    <Tooltip
                      title="Confidence: How certain the AI is about this entity extraction (0-100%)"
                      arrow
                      placement="top"
                    >
                      <Chip
                        label={`${Math.round(entity.confidence * 100)}%`}
                        size="small"
                        variant="outlined"
                        sx={{ height: 20, fontSize: '0.6rem', fontWeight: 500 }}
                      />
                    </Tooltip>
                  )}
                  {expandedEntities.has(idx) && entity.quality_score !== undefined && (
                    <Tooltip
                      title="Quality Score: Measures entity completeness, property richness, and description quality (0-100)"
                      arrow
                      placement="top"
                    >
                      <Chip
                        label={`Q: ${entity.quality_score}`}
                        size="small"
                        variant="outlined"
                        sx={{
                          height: 20,
                          fontSize: '0.6rem',
                          fontWeight: 600,
                          bgcolor: 'transparent',
                          borderColor: alpha(
                            entity.quality_score >= 70
                              ? StatusColors.healthy
                              : entity.quality_score >= 40
                                ? StatusColors.active
                                : StatusColors.failed,
                            0.5,
                          ),
                          color:
                            entity.quality_score >= 70
                              ? StatusColors.healthy
                              : entity.quality_score >= 40
                                ? StatusColors.active
                                : StatusColors.failed,
                        }}
                      />
                    </Tooltip>
                  )}
                  {(() => {
                    const tpl = templateNameMap.get(entity.type?.toLowerCase() ?? '');
                    const chipColor = tpl?.color || getTypeColor(entity.type);
                    const IconComp = getMuiIcon(tpl?.icon || DEFAULT_NODE_ICON);
                    return (
                      <Chip
                        icon={
                          IconComp ? (
                            <IconComp sx={{ color: `${chipColor} !important`, fontSize: 14 }} />
                          ) : undefined
                        }
                        label={cleanTypeName(entity.type)}
                        size="small"
                        variant="outlined"
                        sx={{
                          height: 20,
                          fontSize: '0.65rem',
                          bgcolor: 'transparent',
                          borderColor: alpha(chipColor, 0.5),
                          color: chipColor,
                          '& .MuiChip-icon': { ml: '4px', mr: '-2px' },
                        }}
                      />
                    );
                  })()}
                </Box>
              </Box>
              {entity.description && (
                <Typography
                  variant="body2"
                  sx={{
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    display: '-webkit-box',
                    WebkitLineClamp: 3,
                    WebkitBoxOrient: 'vertical',
                    color: 'rgba(255, 255, 255, 0.55)',
                    fontSize: '0.8rem',
                    lineHeight: 1.5,
                  }}
                >
                  {entity.description}
                </Typography>
              )}
              <Collapse in={expandedEntities.has(idx)}>
                {entity.aliases && entity.aliases.length > 0 && (
                  <Box sx={{ mt: 1 }}>
                    <Typography variant="caption" component="span" sx={{ color: 'text.secondary' }}>
                      Also known as:{' '}
                    </Typography>
                    <Typography variant="caption" component="span" sx={{ color: 'text.primary' }}>
                      {entity.aliases.join(', ')}
                    </Typography>
                  </Box>
                )}
                {entity.properties && Object.keys(entity.properties).length > 0 && (
                  <Box sx={{ mt: 1.5, pt: 1.5, borderTop: '1px solid', borderColor: 'divider' }}>
                    <Typography
                      variant="caption"
                      sx={{ color: 'text.secondary', display: 'block', mb: 0.5 }}
                    >
                      Properties ({Object.keys(entity.properties).length}):
                    </Typography>
                    <Box sx={{ maxHeight: 120, overflow: 'auto' }}>
                      {Object.entries(entity.properties).map(([key, value]) => (
                        <Box key={key} sx={{ display: 'flex', alignItems: 'flex-start', mb: 0.5 }}>
                          <Typography
                            variant="caption"
                            sx={{
                              fontWeight: 500,
                              color: 'text.secondary',
                              minWidth: 80,
                              flexShrink: 0,
                            }}
                          >
                            {key}:
                          </Typography>
                          <Typography
                            variant="caption"
                            sx={{ color: 'text.primary', wordBreak: 'break-word', ml: 0.5 }}
                          >
                            {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                          </Typography>
                        </Box>
                      ))}
                    </Box>
                  </Box>
                )}
              </Collapse>
              <Box sx={{ display: 'flex', justifyContent: 'center', mt: 'auto', pt: 0.5 }}>
                <ExpandMoreIcon
                  sx={{
                    fontSize: '1rem',
                    color: 'text.disabled',
                    transition: 'transform 0.2s',
                    transform: expandedEntities.has(idx) ? 'rotate(180deg)' : 'rotate(0deg)',
                  }}
                />
              </Box>
            </Box>
          </Box>
        ))}
      </Box>

      {entitiesCount > pageSize && (
        <GhostPagination
          page={entitiesPage}
          totalPages={Math.ceil(entitiesCount / pageSize)}
          total={entitiesCount}
          pageSize={pageSize}
          onPageChange={setEntitiesPage}
        />
      )}
    </Box>
  );
}
