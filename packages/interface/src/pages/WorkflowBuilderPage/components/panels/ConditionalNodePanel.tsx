// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ConditionalNodePanel: Properties content for conditionalNode type
 *
 * Renders basic info (name) and the condition rules editor, which can be
 * toggled between a visual ConditionBuilder and a raw JSON fallback.
 */

import React from 'react';
import {
  Box,
  Typography,
  TextField,
  IconButton,
  Tooltip,
  Accordion,
  AccordionSummary,
  AccordionDetails,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import CodeIcon from '@mui/icons-material/Code';
import type { ConditionalNodeData } from '../../types';
import type { FieldSource } from '../../utils/fieldClassification';
import { ConditionBuilder } from '../forms/ConditionBuilder';
import { conditionToJson, type ConditionGroup } from '../forms/conditionTypes';

interface ConditionalNodePanelProps {
  /** Local copy of the node data, cast to ConditionalNodeData. */
  nodeData: ConditionalNodeData;
  /** Whether the JSON editor is shown instead of the visual builder. */
  showJsonEditor: boolean;
  /** Toggle between JSON and visual editor. */
  onToggleJsonEditor: () => void;
  /** Update a single field in local data. */
  onChange: (field: string, value: unknown) => void;
  /** Parsed condition group for the visual builder. */
  conditionGroup: ConditionGroup;
  /** Fields available from upstream nodes for variable picking. */
  upstreamFields: FieldSource[];
}

/**
 * Accordions for conditional node: basic info and condition rules
 * (visual builder or JSON fallback).
 */
export const ConditionalNodePanel: React.FC<ConditionalNodePanelProps> = ({
  nodeData,
  showJsonEditor,
  onToggleJsonEditor,
  onChange,
  conditionGroup,
  upstreamFields,
}) => {
  return (
    <>
      <Accordion defaultExpanded disableGutters elevation={0}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="body2" sx={{
            fontWeight: 500
          }}>
            Basic Info
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <TextField
            label="Name"
            value={nodeData.name || ''}
            onChange={(e) => onChange('name', e.target.value)}
            fullWidth
            size="small"
            margin="dense"
          />
        </AccordionDetails>
      </Accordion>

      {/* Condition Builder */}
      <Accordion defaultExpanded disableGutters elevation={0}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1 }}>
            <Typography variant="body2" sx={{
              fontWeight: 500
            }}>
              Condition Rules
            </Typography>
            <Tooltip title={showJsonEditor ? 'Use visual builder' : 'Use JSON editor'}>
              <IconButton
                aria-label={showJsonEditor ? 'Use visual builder' : 'Use JSON editor'}
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleJsonEditor();
                }}
                sx={{ ml: 'auto', mr: 1 }}
              >
                <CodeIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          {showJsonEditor ? (
            <TextField
              label="Condition (JSON)"
              value={JSON.stringify(nodeData.condition || {}, null, 2)}
              onChange={(e) => {
                try {
                  onChange('condition', JSON.parse(e.target.value));
                } catch {
                  // Invalid JSON, ignore
                }
              }}
              fullWidth
              size="small"
              margin="dense"
              multiline
              rows={4}
              sx={{ fontFamily: 'monospace' }}
            />
          ) : (
            <ConditionBuilder
              condition={conditionGroup}
              onChange={(cond) => onChange('condition', conditionToJson(cond))}
              availableFields={upstreamFields}
            />
          )}
        </AccordionDetails>
      </Accordion>
    </>
  );
};
