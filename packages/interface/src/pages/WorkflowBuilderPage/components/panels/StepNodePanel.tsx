// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * StepNodePanel: Properties content for stepNode and multiPortStepNode types
 *
 * Renders basic info (name, description, tool), tool configuration via a
 * dynamic form or JSON editor fallback, execution options (continue on error,
 * thinking mode), and the output data schema.
 */

import React from 'react';
import {
  Box,
  Typography,
  TextField,
  IconButton,
  Tooltip,
  Switch,
  FormControlLabel,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import CodeIcon from '@mui/icons-material/Code';
import type { WorkflowStepNodeData, FieldSchema } from '../../types';
import type { FieldSource } from '../../utils/fieldClassification';
import { DynamicFormRenderer } from '../forms/DynamicFormRenderer';
import { SchemaDisplay } from '../forms/SchemaDisplay';

interface StepNodePanelProps {
  /** Local copy of the node data, cast to WorkflowStepNodeData. */
  nodeData: WorkflowStepNodeData;
  /** Whether the JSON editor is shown instead of the visual form. */
  showJsonEditor: boolean;
  /** Toggle between JSON and visual editor. */
  onToggleJsonEditor: () => void;
  /** Update a single field in local data. */
  onChange: (field: string, value: unknown) => void;
  /** JSON Schema describing the tool's input fields. */
  toolSchema: Record<string, unknown> | null;
  /** Output schema fields for the selected tool. */
  toolOutputSchema: FieldSchema[];
  /** Fields available from upstream nodes for variable picking. */
  upstreamFields: FieldSource[];
}

/**
 * Accordions for step node configuration: basic info, tool config form,
 * execution options, and output data display.
 */
export const StepNodePanel: React.FC<StepNodePanelProps> = ({
  nodeData,
  showJsonEditor,
  onToggleJsonEditor,
  onChange,
  toolSchema,
  toolOutputSchema,
  upstreamFields,
}) => {
  return (
    <>
      {/* Basic Info */}
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
          <TextField
            label="Description"
            value={nodeData.description || ''}
            onChange={(e) => onChange('description', e.target.value)}
            fullWidth
            size="small"
            margin="dense"
            multiline
            rows={2}
          />
          <TextField
            label="Tool"
            value={nodeData.toolName || ''}
            fullWidth
            size="small"
            margin="dense"
            disabled
            helperText={nodeData.toolId}
          />
        </AccordionDetails>
      </Accordion>

      {/* Tool Configuration - Dynamic Form */}
      <Accordion defaultExpanded disableGutters elevation={0}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1 }}>
            <Typography variant="body2" sx={{
              fontWeight: 500
            }}>
              Configuration
            </Typography>
            <Tooltip title={showJsonEditor ? 'Use form editor' : 'Use JSON editor'}>
              <IconButton
                aria-label={showJsonEditor ? 'Use form editor' : 'Use JSON editor'}
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
            // Fallback JSON editor
            (<TextField
              label="Configuration (JSON)"
              value={JSON.stringify(nodeData.configuration || {}, null, 2)}
              onChange={(e) => {
                try {
                  onChange('configuration', JSON.parse(e.target.value));
                } catch {
                  // Invalid JSON, ignore
                }
              }}
              fullWidth
              size="small"
              margin="dense"
              multiline
              rows={6}
              sx={{ fontFamily: 'monospace' }}
            />)
          ) : (
            // Dynamic form based on schema
            (<DynamicFormRenderer
              schema={toolSchema || null}
              values={nodeData.configuration || {}}
              onChange={(config) => onChange('configuration', config)}
              availableFields={upstreamFields}
              allowReferences={true}
            />)
          )}
        </AccordionDetails>
      </Accordion>

      {/* Execution Options */}
      <Accordion disableGutters elevation={0}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="body2" sx={{
            fontWeight: 500
          }}>
            Execution Options
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <FormControlLabel
            control={
              <Switch
                checked={nodeData.continueOnError || false}
                onChange={(e) => onChange('continueOnError', e.target.checked)}
                size="small"
              />
            }
            label={
              <Typography variant="body2">Continue on error</Typography>
            }
          />
          <FormControl fullWidth size="small" margin="dense">
            <InputLabel>Thinking Mode</InputLabel>
            <Select
              value={nodeData.thinkingMode || 'auto'}
              label="Thinking Mode"
              onChange={(e) => onChange('thinkingMode', e.target.value)}
            >
              <MenuItem value="auto">Auto</MenuItem>
              <MenuItem value="enabled">Enabled</MenuItem>
              <MenuItem value="disabled">Disabled</MenuItem>
            </Select>
          </FormControl>
        </AccordionDetails>
      </Accordion>

      {/* Output Schema Display */}
      {toolOutputSchema.length > 0 && (
        <Accordion disableGutters elevation={0}>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="body2" sx={{
              fontWeight: 500
            }}>
              Output Data
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            <SchemaDisplay
              fields={toolOutputSchema}
              direction="output"
              title="This step produces"
              emptyMessage="This tool has no defined outputs."
            />
          </AccordionDetails>
        </Accordion>
      )}
    </>
  );
};
