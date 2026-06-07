// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Templates sub-tab of ExtractionTab.
 * Renders summary stats and a card grid of source templates with property
 * chips and pagination.
 */

import { Box, Typography, Chip } from '@mui/material';
import { alpha } from '@mui/material/styles';
import { ChartPalette } from '../../../../theme/charts';
import { glassPanelSx, surfaceSx, surfaceHoverSx } from '../../../../theme/cardStyles';
import { ContentTypeColors } from '../../../../theme/colors';
import { cleanTypeName } from '../../../../utils/formatters';
import TemplateIcon from '../../../../components/TemplateIcon';
import GhostPagination from '../../../../components/GhostPagination';
import type { SourceTemplate } from './types';

interface TemplatesViewProps {
  templates: SourceTemplate[];
  templatesCount: number;
  templatesPage: number;
  setTemplatesPage: (page: number) => void;
  pageSize: number;
}

/** Card grid of source templates with summary stats and property display. */
export function TemplatesView({
  templates,
  templatesCount,
  templatesPage,
  setTemplatesPage,
  pageSize,
}: TemplatesViewProps) {
  return (
    <Box>
      {/* Summary Stats */}
      {templates.length > 0 && (
        <Box sx={{ ...glassPanelSx, p: 1.5, mb: 2, display: 'flex', gap: 3, justifyContent: 'center' }}>
          <Box sx={{ textAlign: 'center' }}>
            <Typography variant="h6" sx={{ color: ContentTypeColors.templates }}>
              {templates.filter((t) => t.template_type === 'node').length}
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Node Templates
            </Typography>
          </Box>
          <Box sx={{ textAlign: 'center' }}>
            <Typography variant="h6" sx={{ color: ContentTypeColors.templates }}>
              {templates.filter((t) => t.template_type === 'edge').length}
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Edge Templates
            </Typography>
          </Box>
          <Box sx={{ textAlign: 'center' }}>
            <Typography variant="h6" sx={{ color: ContentTypeColors.entities }}>
              {templates.reduce((sum, t) => sum + t.node_count, 0).toLocaleString()}
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Total Nodes
            </Typography>
          </Box>
          <Box sx={{ textAlign: 'center' }}>
            <Typography variant="h6" sx={{ color: ContentTypeColors.relationships }}>
              {templates.reduce((sum, t) => sum + t.edge_count, 0).toLocaleString()}
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Total Edges
            </Typography>
          </Box>
        </Box>
      )}

      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1.5 }}>
        {templates.map((template) => (
          <Box key={template.id} sx={{ flex: '1 1 calc(25% - 12px)', minWidth: 250 }}>
            <Box
              sx={{
                p: 2,
                height: '100%',
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
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
                  <TemplateIcon
                    template={template}
                    variant={template.template_type === 'edge' ? 'edge' : 'node'}
                    size={14}
                    containerSize={24}
                    filled
                  />
                  <Typography variant="subtitle2" noWrap sx={{ fontWeight: 600 }}>
                    {cleanTypeName(template.name)}
                  </Typography>
                </Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexShrink: 0 }}>
                  <Chip
                    label={
                      template.template_type === 'node'
                        ? template.node_count.toLocaleString()
                        : template.edge_count.toLocaleString()
                    }
                    size="small"
                    variant="outlined"
                    sx={{
                      height: 20,
                      fontSize: '0.6rem',
                      fontWeight: 500,
                      borderColor:
                        template.template_type === 'node' ? ChartPalette[0] : ChartPalette[1],
                      color:
                        template.template_type === 'node' ? ChartPalette[0] : ChartPalette[1],
                    }}
                  />
                  <Chip
                    label={template.template_type}
                    size="small"
                    variant="outlined"
                    sx={{
                      height: 20,
                      fontSize: '0.65rem',
                      bgcolor: 'transparent',
                      borderColor: alpha(
                        template.template_type === 'node' ? ChartPalette[0] : ChartPalette[1],
                        0.5,
                      ),
                      color:
                        template.template_type === 'node' ? ChartPalette[0] : ChartPalette[1],
                    }}
                  />
                </Box>
              </Box>
              {template.description && (
                <Typography
                  variant="body2"
                  sx={{
                    color: 'text.secondary',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    display: '-webkit-box',
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: 'vertical',
                    mb: 1,
                  }}
                >
                  {template.description}
                </Typography>
              )}
              {template.properties && template.properties.length > 0 && (
                <Box>
                  <Typography
                    variant="caption"
                    sx={{ color: 'text.secondary', display: 'block', mb: 0.5 }}
                  >
                    Properties ({template.properties.length}):
                  </Typography>
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                    {template.properties.slice(0, 5).map((prop, idx) => (
                      <Chip
                        key={prop.name || prop.display_name || `prop-${idx}`}
                        label={prop.display_name || prop.name}
                        size="small"
                        variant="outlined"
                        sx={{ height: 18, fontSize: '0.6rem' }}
                      />
                    ))}
                    {template.properties.length > 5 && (
                      <Chip
                        label={`+${template.properties.length - 5}`}
                        size="small"
                        variant="outlined"
                        sx={{ height: 18, fontSize: '0.6rem' }}
                      />
                    )}
                  </Box>
                </Box>
              )}
            </Box>
          </Box>
        ))}
      </Box>

      {templatesCount > pageSize && (
        <GhostPagination
          page={templatesPage}
          totalPages={Math.ceil(templatesCount / pageSize)}
          total={templatesCount}
          pageSize={pageSize}
          onPageChange={setTemplatesPage}
        />
      )}
    </Box>
  );
}
