// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TriggerNodePanel: Properties content for trigger and event trigger nodes
 *
 * Covers triggerNode, unifiedEntryNode, and eventTriggerNode types.
 * Renders trigger settings (label/name, event source, enabled/priority),
 * filter builder, and event data schema display.
 */

import React from 'react';
import {
  Box,
  Typography,
  TextField,
  Switch,
  FormControlLabel,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Chip,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import type { TriggerNodeData, EventTriggerNodeData } from '../../types';
import { EVENT_SOURCE_INFO, EVENT_SCHEMAS, type EventSource } from '../../constants/eventSchemas';
import { FilterBuilder } from '../forms/FilterBuilder';
import type { jsonToFilters } from '../forms/filterTypes';
import { SchemaDisplay } from '../forms/SchemaDisplay';

interface TriggerNodePanelProps {
  /** The node type — triggerNode, unifiedEntryNode, or eventTriggerNode. */
  nodeType: string;
  /** Local copy of the node data. */
  nodeData: Record<string, unknown>;
  /** Local filter rules (separate state for in-progress edits). */
  localFilterRules: ReturnType<typeof jsonToFilters>;
  /** Update a single field in local data. */
  onChange: (field: string, value: unknown) => void;
  /** Update the filter rules. */
  onFilterChange: (rules: ReturnType<typeof jsonToFilters>) => void;
}

/**
 * Renders the appropriate trigger node settings based on node type.
 *
 * For triggerNode/unifiedEntryNode: label, event source, filters, event data.
 * For eventTriggerNode: name, event source, enabled, priority, filters,
 * event data, and trigger ID display.
 */
export const TriggerNodePanel: React.FC<TriggerNodePanelProps> = ({
  nodeType,
  nodeData,
  localFilterRules,
  onChange,
  onFilterChange,
}) => {
  // Start/Trigger node (triggerNode or unifiedEntryNode)
  if (nodeType === 'triggerNode' || nodeType === 'unifiedEntryNode') {
    const data = nodeData as unknown as TriggerNodeData;
    return (
      <>
        <Accordion defaultExpanded disableGutters elevation={0}>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="body2" sx={{
              fontWeight: 500
            }}>
              Trigger Settings
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            <TextField
              label="Label"
              value={data.label || ''}
              onChange={(e) => onChange('label', e.target.value)}
              fullWidth
              size="small"
              margin="dense"
            />
            <FormControl fullWidth size="small" margin="dense">
              <InputLabel>Event Source</InputLabel>
              <Select
                value={data.eventSource || 'manual'}
                label="Event Source"
                onChange={(e) => onChange('eventSource', e.target.value)}
              >
                {Object.entries(EVENT_SOURCE_INFO).map(([key, info]) => (
                  <MenuItem key={key} value={key}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Typography variant="body2">{info.label}</Typography>
                      <Chip
                        label={info.category}
                        size="small"
                        sx={{ height: 16, fontSize: '0.55rem' }}
                      />
                    </Box>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </AccordionDetails>
        </Accordion>

        {/* Filters - Visual Builder */}
        <Accordion disableGutters elevation={0}>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="body2" sx={{
              fontWeight: 500
            }}>
              Event Filters
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            <FilterBuilder
              filters={localFilterRules}
              onChange={onFilterChange}
              eventSource={data.eventSource as EventSource}
            />
          </AccordionDetails>
        </Accordion>

        {/* Event Data Schema */}
        {data.eventSource &&
          EVENT_SCHEMAS[data.eventSource as EventSource]?.length > 0 && (
            <Accordion disableGutters elevation={0}>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography variant="body2" sx={{
                  fontWeight: 500
                }}>
                  Event Data
                </Typography>
              </AccordionSummary>
              <AccordionDetails>
                <SchemaDisplay
                  fields={EVENT_SCHEMAS[data.eventSource as EventSource]}
                  direction="output"
                  title="Available in event payload"
                  emptyMessage="This event type has no payload data."
                />
              </AccordionDetails>
            </Accordion>
          )}
      </>
    );
  }

  // Event trigger node (eventTriggerNode)
  const data = nodeData as unknown as EventTriggerNodeData;
  return (
    <>
      {/* Basic Info */}
      <Accordion defaultExpanded disableGutters elevation={0}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="body2" sx={{
            fontWeight: 500
          }}>
            Trigger Settings
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <TextField
            label="Name"
            value={data.name || ''}
            onChange={(e) => onChange('name', e.target.value)}
            fullWidth
            size="small"
            margin="dense"
          />
          <FormControl fullWidth size="small" margin="dense">
            <InputLabel>Event Source</InputLabel>
            <Select
              value={data.eventSource || ''}
              label="Event Source"
              onChange={(e) => onChange('eventSource', e.target.value)}
            >
              {Object.entries(EVENT_SOURCE_INFO).map(([key, info]) => (
                <MenuItem key={key} value={key}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Typography variant="body2">{info.label}</Typography>
                    <Chip
                      label={info.category}
                      size="small"
                      sx={{ height: 16, fontSize: '0.55rem' }}
                    />
                  </Box>
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControlLabel
            control={
              <Switch
                checked={data.enabled ?? true}
                onChange={(e) => onChange('enabled', e.target.checked)}
                size="small"
              />
            }
            label={
              <Typography variant="body2">Enabled</Typography>
            }
            sx={{ mt: 1 }}
          />
          <TextField
            label="Priority"
            type="number"
            value={data.priority ?? 0}
            onChange={(e) => onChange('priority', parseInt(e.target.value, 10) || 0)}
            fullWidth
            size="small"
            margin="dense"
            helperText="Lower values = higher priority"
          />
        </AccordionDetails>
      </Accordion>

      {/* Filters - Visual Builder */}
      <Accordion disableGutters elevation={0}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="body2" sx={{
            fontWeight: 500
          }}>
            Event Filters
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <FilterBuilder
            filters={localFilterRules}
            onChange={onFilterChange}
            eventSource={data.eventSource as EventSource}
          />
        </AccordionDetails>
      </Accordion>

      {/* Event Data Schema */}
      {data.eventSource &&
        EVENT_SCHEMAS[data.eventSource as EventSource]?.length > 0 && (
          <Accordion disableGutters elevation={0}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography variant="body2" sx={{
                fontWeight: 500
              }}>
                Event Data
              </Typography>
            </AccordionSummary>
            <AccordionDetails>
              <SchemaDisplay
                fields={EVENT_SCHEMAS[data.eventSource as EventSource]}
                direction="output"
                title="Available in event payload"
                emptyMessage="This event type has no payload data."
              />
            </AccordionDetails>
          </Accordion>
        )}

      {/* Trigger ID display (for existing triggers) */}
      {data.triggerId && (
        <Box sx={{ px: 2, py: 1, bgcolor: 'action.hover' }}>
          <Typography variant="caption" sx={{
            color: "text.secondary"
          }}>
            Trigger ID: {data.triggerId}
          </Typography>
        </Box>
      )}
    </>
  );
};
