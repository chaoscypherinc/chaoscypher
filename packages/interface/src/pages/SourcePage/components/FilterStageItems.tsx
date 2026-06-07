// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Chip, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material';
import type { FilteringLog } from '../../../types';
import { getStageMeta } from './filterStageMeta';

export interface FilterStageItemsProps {
  filteringLog: FilteringLog | null;
}

export function FilterStageItems({ filteringLog }: FilterStageItemsProps) {
  if (!filteringLog || filteringLog.stages.length === 0) return null;
  return (
    <Box>
      {filteringLog.stages.map((s) => {
        const meta = getStageMeta(s.stage);
        return (
          <Box key={s.stage} sx={{ mb: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.75 }}>
              <Chip
                label={meta.label}
                color={meta.color}
                size="small"
                variant="outlined"
              />
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                {`· ${s.removed_count} dropped`}
              </Typography>
            </Box>
            {s.items.length > 0 && (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ fontSize: '0.65rem' }}>TYPE</TableCell>
                      <TableCell sx={{ fontSize: '0.65rem' }}>NAME</TableCell>
                      <TableCell sx={{ fontSize: '0.65rem' }}>REASON</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {s.items.map((item, idx) => (
                      <TableRow key={idx}>
                        <TableCell sx={{ fontSize: '0.7rem' }}>{item.item_type}</TableCell>
                        <TableCell sx={{ fontSize: '0.7rem', fontFamily: 'ui-monospace, monospace' }}>
                          {item.name}
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.7rem', color: 'text.secondary' }}>
                          {item.reason}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Box>
        );
      })}
    </Box>
  );
}
