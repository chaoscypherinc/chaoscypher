// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Entity Table
 *
 * Collapsible table displaying extracted entities with name, type,
 * description, aliases, and confidence scores.
 */

import { useState } from 'react';
import {
  Box,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  Tooltip,
  IconButton,
  Collapse,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import type { Entity } from './parseLLMContent';
import { getConfidenceColor, formatConfidence } from './parseLLMContent';

interface EntityTableProps {
  entities: Entity[];
}

/** Collapsible section displaying extracted entities in tabular form. */
export function EntityTable({ entities }: EntityTableProps) {
  const [expanded, setExpanded] = useState(true);

  return (
    <Paper variant="outlined" sx={{ mb: 2 }}>
      <Box
        onClick={() => setExpanded(!expanded)}
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 2,
          py: 1,
          bgcolor: 'action.hover',
          cursor: 'pointer'
        }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1
          }}>
          <Typography variant="subtitle2">Entities</Typography>
          <Chip label={entities.length} size="small" />
        </Box>
        <IconButton aria-label={expanded ? "Collapse" : "Expand"} size="small">
          {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
        </IconButton>
      </Box>
      <Collapse in={expanded}>
        {entities.length === 0 ? (
          <Typography
            sx={{
              color: "text.secondary",
              p: 2
            }}>
            No entities extracted
          </Typography>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell width={40}>#</TableCell>
                  <TableCell>Name</TableCell>
                  <TableCell width={120}>Type</TableCell>
                  <TableCell>Description</TableCell>
                  <TableCell width={80} align="center">Confidence</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {entities.map((entity, idx) => (
                  <TableRow key={entity.name || `entity-${idx}`} hover>
                    <TableCell>
                      <Typography variant="caption" sx={{
                        color: "text.secondary"
                      }}>
                        {idx}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" sx={{
                        fontWeight: 500
                      }}>
                        {entity.name}
                      </Typography>
                      {entity.aliases && entity.aliases.length > 0 && (
                        <Typography variant="caption" sx={{
                          color: "text.secondary"
                        }}>
                          aka: {entity.aliases.slice(0, 3).join(', ')}
                          {entity.aliases.length > 3 ? '...' : ''}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={entity.entity_type || entity.type || 'unknown'}
                        size="small"
                        variant="outlined"
                        sx={{ fontSize: '0.75rem' }}
                      />
                    </TableCell>
                    <TableCell>
                      <Tooltip title={entity.description || '-'} arrow>
                        <Typography
                          variant="body2"
                          sx={{
                            maxWidth: 300,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {entity.description || '-'}
                        </Typography>
                      </Tooltip>
                    </TableCell>
                    <TableCell align="center">
                      <Chip
                        label={formatConfidence(entity.confidence)}
                        size="small"
                        color={getConfidenceColor(entity.confidence)}
                        sx={{ fontSize: '0.7rem', height: 20 }}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Collapse>
    </Paper>
  );
}
