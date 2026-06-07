// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useEffect } from 'react';
import { Chip, Tooltip, Typography, Box } from '@mui/material';
import FilterIcon from '@mui/icons-material/FilterList';
import { sourcesApi } from '../../services/api/sources';
import type { Source } from '../../types';

interface ScopeBadgeProps {
  sourceIds: string[];
  onClick?: () => void;
}

/**
 * Compact badge showing the source scope of a chat.
 *
 * - 1 source: shows source title
 * - 2-3 sources: shows all titles in tooltip, count in chip
 * - 4+: shows count with tooltip listing all
 */
export default function ScopeBadge({ sourceIds, onClick }: ScopeBadgeProps) {
  const [sources, setSources] = useState<Source[]>([]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const results: Source[] = [];
      for (const id of sourceIds) {
        try {
          const source = await sourcesApi.get(id);
          if (!cancelled) results.push(source);
        } catch {
          // Skip sources that can't be loaded
        }
      }
      if (!cancelled) setSources(results);
    };
    load();
    return () => { cancelled = true; };
  }, [sourceIds]);

  const names = sources.map((s) => s.title || s.filename);
  const count = sourceIds.length;

  const label = count === 1 && names[0]
    ? names[0]
    : `${count} source${count !== 1 ? 's' : ''}`;

  const tooltipContent = (
    <Box sx={{ p: 0.5 }}>
      <Typography
        variant="caption"
        sx={{
          fontWeight: 600,
          display: "block",
          mb: 0.5
        }}>
        Scoped Sources
      </Typography>
      {names.map((name, i) => (
        <Typography key={i} variant="caption" sx={{
          display: "block"
        }}>
          {name}
        </Typography>
      ))}
      {names.length < count && (
        <Typography variant="caption" sx={{
          color: "text.secondary"
        }}>
          + {count - names.length} more
        </Typography>
      )}
    </Box>
  );

  return (
    <Tooltip title={tooltipContent} arrow>
      <Chip
        icon={<FilterIcon sx={{ fontSize: 14 }} />}
        label={label}
        size="small"
        variant="outlined"
        color="primary"
        onClick={onClick}
        sx={{
          maxWidth: 180,
          '& .MuiChip-label': { overflow: 'hidden', textOverflow: 'ellipsis' },
          cursor: onClick ? 'pointer' : 'default',
        }}
      />
    </Tooltip>
  );
}
