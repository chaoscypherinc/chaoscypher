// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Parsed LLM Response
 *
 * Renders parsed LLM extraction output as structured entity and
 * relationship tables with a toggle to view raw output.
 */

import { useState, useMemo } from 'react';
import {
  Box,
  Chip,
  Tooltip,
  IconButton,
  Alert,
} from '@mui/material';
import CodeIcon from '@mui/icons-material/Code';
import TableIcon from '@mui/icons-material/TableChart';
import { SyntaxHighlighter, vscDarkPlus } from '../../../utils/syntaxHighlighter';
import { parseContent } from './parseLLMContent';
import { EntityTable } from './EntityTable';
import { RelationshipTable } from './RelationshipTable';

interface ParsedLLMResponseProps {
  jsonString: string; // Can be JSON or pipe-delimited format
  maxHeight?: number;
}

export function ParsedLLMResponse({ jsonString, maxHeight = 400 }: ParsedLLMResponseProps) {
  const [viewMode, setViewMode] = useState<'table' | 'raw'>('table');

  const { data, error, rawFormat } = useMemo(() => parseContent(jsonString), [jsonString]);

  // If parsing failed, show raw with error message
  if (error || !data) {
    return (
      <Box>
        <Alert severity="warning" sx={{ mb: 1 }}>
          {error}
        </Alert>
        <Box sx={{ maxHeight, overflow: 'auto' }}>
          <SyntaxHighlighter
            language={rawFormat === 'json' ? 'json' : 'text'}
            style={vscDarkPlus}
            customStyle={{ margin: 0, fontSize: '0.85rem' }}
          >
            {jsonString}
          </SyntaxHighlighter>
        </Box>
      </Box>
    );
  }

  const { entities, relationships, format } = data;

  // Build entity name lookup for validating JSON-format relationships,
  // which still reference entities by name.
  const entityNames = new Set(entities.map((e) => e.name?.toLowerCase()).filter(Boolean));
  entities.forEach((e) => {
    e.aliases?.forEach((alias) => {
      if (alias) entityNames.add(alias.toLowerCase());
    });
  });

  // Filter out relationships with invalid entity references
  const validRelationships = relationships.filter((rel) => {
    // JSON format references entities by name
    if (rel.source_name && rel.target_name) {
      return (
        entityNames.has(rel.source_name.toLowerCase()) &&
        entityNames.has(rel.target_name.toLowerCase())
      );
    }

    // Pipe format references entities by 0-based integer index
    if (typeof rel.source === 'number' && typeof rel.target === 'number') {
      return (
        rel.source >= 0 &&
        rel.source < entities.length &&
        rel.target >= 0 &&
        rel.target < entities.length
      );
    }

    return false;
  });

  const invalidCount = relationships.length - validRelationships.length;

  return (
    <Box>
      {/* View Toggle & Format Indicator */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          mb: 1
        }}>
        <Chip
          label={format === 'pipe' ? 'Pipe Format' : 'JSON Format'}
          size="small"
          variant="outlined"
          sx={{ fontSize: '0.7rem', height: 20 }}
        />
        <Tooltip title={viewMode === 'table' ? 'Show raw output' : 'Show parsed tables'}>
          <IconButton
            aria-label={viewMode === 'table' ? 'Show raw output' : 'Show parsed tables'}
            size="small"
            onClick={() => setViewMode(viewMode === 'table' ? 'raw' : 'table')}
          >
            {viewMode === 'table' ? <CodeIcon /> : <TableIcon />}
          </IconButton>
        </Tooltip>
      </Box>
      {viewMode === 'raw' ? (
        <Box sx={{ maxHeight, overflow: 'auto' }}>
          <SyntaxHighlighter
            language={format === 'json' ? 'json' : 'text'}
            style={vscDarkPlus}
            customStyle={{ margin: 0, fontSize: '0.85rem' }}
          >
            {jsonString}
          </SyntaxHighlighter>
        </Box>
      ) : (
        <Box sx={{ maxHeight, overflow: 'auto' }}>
          <EntityTable entities={entities} />
          <RelationshipTable
            relationships={validRelationships}
            entities={entities}
            invalidCount={invalidCount}
          />
        </Box>
      )}
    </Box>
  );
}
