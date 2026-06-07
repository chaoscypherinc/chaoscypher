// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Relationship Table
 *
 * Collapsible table displaying extracted relationships between entities,
 * with source/target resolution, type labels, and confidence scores.
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
  IconButton,
  Collapse,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import type { Entity, Relationship } from './parseLLMContent';
import { getConfidenceColor, formatConfidence } from './parseLLMContent';

interface RelationshipTableProps {
  relationships: Relationship[];
  entities: Entity[];
  invalidCount: number;
}

/** Collapsible section displaying extracted relationships in tabular form. */
export function RelationshipTable({ relationships, entities, invalidCount }: RelationshipTableProps) {
  const [expanded, setExpanded] = useState(true);

  const getSourceName = (rel: Relationship): string => {
    if (rel.source_name) return rel.source_name;
    if (typeof rel.source === 'number') {
      if (rel.source >= 0 && rel.source < entities.length) {
        return entities[rel.source].name || `Entity ${rel.source}`;
      }
      return `[Index ${rel.source}]`;
    }
    return `[Invalid]`;
  };

  const getTargetName = (rel: Relationship): string => {
    if (rel.target_name) return rel.target_name;
    if (typeof rel.target === 'number') {
      if (rel.target >= 0 && rel.target < entities.length) {
        return entities[rel.target].name || `Entity ${rel.target}`;
      }
      return `[Index ${rel.target}]`;
    }
    return `[Invalid]`;
  };

  const getRelType = (rel: Relationship): string => {
    return rel.relationship_type || rel.type || 'unknown';
  };

  return (
    <Paper variant="outlined">
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
          <Typography variant="subtitle2">Relationships</Typography>
          <Chip label={relationships.length} size="small" />
          {invalidCount > 0 && (
            <Chip
              label={`${invalidCount} invalid`}
              size="small"
              color="warning"
              variant="outlined"
              sx={{ fontSize: '0.7rem' }}
            />
          )}
        </Box>
        <IconButton aria-label={expanded ? "Collapse" : "Expand"} size="small">
          {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
        </IconButton>
      </Box>
      <Collapse in={expanded}>
        {relationships.length === 0 ? (
          <Typography
            sx={{
              color: "text.secondary",
              p: 2
            }}>
            {invalidCount > 0
              ? `No valid relationships (${invalidCount} had invalid entity references)`
              : 'No relationships extracted'}
          </Typography>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Source</TableCell>
                  <TableCell width={150} align="center">Relationship</TableCell>
                  <TableCell>Target</TableCell>
                  <TableCell width={80} align="center">Confidence</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {relationships.map((rel, idx) => (
                  <TableRow key={`rel-${idx}`} hover>
                    <TableCell>
                      <Typography variant="body2">
                        {getSourceName(rel)}
                      </Typography>
                    </TableCell>
                    <TableCell align="center">
                      <Chip
                        label={getRelType(rel)}
                        size="small"
                        color="primary"
                        variant="outlined"
                        sx={{ fontSize: '0.75rem' }}
                      />
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">
                        {getTargetName(rel)}
                      </Typography>
                    </TableCell>
                    <TableCell align="center">
                      <Chip
                        label={formatConfidence(rel.confidence)}
                        size="small"
                        color={getConfidenceColor(rel.confidence)}
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
