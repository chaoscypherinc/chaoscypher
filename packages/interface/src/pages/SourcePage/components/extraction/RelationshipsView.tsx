// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Relationships sub-tab of ExtractionTab.
 * Renders a table of inferred relationships with type chips, confidence
 * badges, and pagination.
 */

import {
  Box,
  Typography,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import type { InferredRelationship } from '../../../../types';
import { DEFAULT_EDGE_ICON } from '../../../../utils/iconSprites';
import { getMuiIcon } from '../../../../utils/icons';
import GhostPagination from '../../../../components/GhostPagination';
import type { SourceTemplate } from './types';
import { stringToColor } from './types';

interface RelationshipsViewProps {
  relationships: InferredRelationship[];
  relationshipsCount: number;
  relationshipsPage: number;
  setRelationshipsPage: (page: number) => void;
  pageSize: number;
  templateNameMap: Map<string, SourceTemplate>;
}

/** Table of inferred relationships with type chips and confidence indicators. */
export function RelationshipsView({
  relationships,
  relationshipsCount,
  relationshipsPage,
  setRelationshipsPage,
  pageSize,
  templateNameMap,
}: RelationshipsViewProps) {
  return (
    <Box>
      <TableContainer>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>From</TableCell>
              <TableCell width={40} align="center"></TableCell>
              <TableCell>Relationship</TableCell>
              <TableCell width={40} align="center"></TableCell>
              <TableCell>To</TableCell>
              <TableCell width={100}>Confidence</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {relationships.map((rel, idx) => (
              <TableRow key={`rel-${idx}`}>
                <TableCell>
                  <Typography variant="body2">{rel.from || `Entity ${rel.source}`}</Typography>
                </TableCell>
                <TableCell align="center">
                  <Typography sx={{ color: 'text.secondary' }}>→</Typography>
                </TableCell>
                <TableCell>
                  {(() => {
                    const tpl = templateNameMap.get(rel.type?.toLowerCase() ?? '');
                    const chipColor = tpl?.color || stringToColor(rel.type || 'unknown');
                    const IconComp = getMuiIcon(tpl?.icon || DEFAULT_EDGE_ICON);
                    return (
                      <Chip
                        icon={
                          IconComp ? (
                            <IconComp sx={{ color: `${chipColor} !important`, fontSize: 14 }} />
                          ) : undefined
                        }
                        label={rel.type}
                        size="small"
                        variant="outlined"
                        sx={{
                          bgcolor: 'transparent',
                          borderColor: alpha(chipColor, 0.5),
                          color: chipColor,
                          fontWeight: 500,
                          '& .MuiChip-icon': { ml: '4px', mr: '-2px' },
                        }}
                      />
                    );
                  })()}
                </TableCell>
                <TableCell align="center">
                  <Typography sx={{ color: 'text.secondary' }}>→</Typography>
                </TableCell>
                <TableCell>
                  <Typography variant="body2">{rel.to || `Entity ${rel.target}`}</Typography>
                </TableCell>
                <TableCell>
                  {rel.confidence !== undefined && (
                    <Tooltip
                      title="Confidence: How certain the AI is about this relationship (0-100%)"
                      arrow
                      placement="top"
                    >
                      <Chip
                        label={`${Math.round(rel.confidence * 100)}%`}
                        size="small"
                        variant="outlined"
                        sx={{
                          height: 22,
                          fontSize: '0.7rem',
                          fontWeight: 500,
                          cursor: 'default',
                        }}
                      />
                    </Tooltip>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>

      {relationshipsCount > pageSize && (
        <GhostPagination
          page={relationshipsPage}
          totalPages={Math.ceil(relationshipsCount / pageSize)}
          total={relationshipsCount}
          pageSize={pageSize}
          onPageChange={setRelationshipsPage}
        />
      )}
    </Box>
  );
}
