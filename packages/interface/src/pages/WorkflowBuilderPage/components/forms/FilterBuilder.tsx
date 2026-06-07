// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * FilterBuilder: Visual builder for event trigger filters
 *
 * Allows users to define filters for event-triggered workflows using
 * a simple key-value interface with operators, instead of raw JSON.
 */

import React, { useCallback } from 'react';
import {
  Box,
  Typography,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  IconButton,
  Button,
  Paper,
  Chip,
  Autocomplete,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import FilterListIcon from '@mui/icons-material/FilterList';
import type { FieldSchema } from '../../types/dataflow';
import { EVENT_SCHEMAS, type EventSource } from '../../constants/eventSchemas';

import type { FilterRule, FilterOperator } from './filterTypes';

/**
 * Operator definitions
 */
const OPERATORS: { value: FilterOperator; label: string }[] = [
  { value: 'equals', label: 'equals' },
  { value: 'not_equals', label: 'does not equal' },
  { value: 'contains', label: 'contains' },
  { value: 'starts_with', label: 'starts with' },
  { value: 'ends_with', label: 'ends with' },
  { value: 'regex', label: 'matches pattern' },
];

interface FilterBuilderProps {
  /** Current filter rules */
  filters: FilterRule[];
  /** Callback when filters change */
  onChange: (filters: FilterRule[]) => void;
  /** Event source to get available fields */
  eventSource?: EventSource;
  /** Label for the builder */
  label?: string;
}

/**
 * Generate unique ID
 */
function generateId(): string {
  return `filter-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Single filter row component
 */
interface FilterRowProps {
  filter: FilterRule;
  availableFields: FieldSchema[];
  onUpdate: (filter: FilterRule) => void;
  onDelete: () => void;
}

const FilterRow: React.FC<FilterRowProps> = ({
  filter,
  availableFields,
  onUpdate,
  onDelete,
}) => {
  const handleChange = (key: keyof FilterRule, value: string) => {
    onUpdate({ ...filter, [key]: value });
  };

  return (
    <Paper
      variant="outlined"
      sx={{
        p: 1.5,
        mb: 1,
      }}
    >
      {/* Top row: Field and Delete button */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <Autocomplete
          size="small"
          freeSolo
          options={availableFields.map((f) => f.name)}
          value={filter.field}
          onChange={(_, v) => handleChange('field', v || '')}
          onInputChange={(_, v) => handleChange('field', v)}
          renderInput={(params) => (
            <TextField {...params} label="Field" placeholder="e.g. node_type" />
          )}
          renderOption={({ key, ...props }, option) => {
            const field = availableFields.find((f) => f.name === option);
            return (
              <Box component="li" key={key} {...props}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                    {option}
                  </Typography>
                  {field && (
                    <Chip
                      label={field.type}
                      size="small"
                      sx={{ height: 16, fontSize: '0.55rem' }}
                    />
                  )}
                </Box>
              </Box>
            );
          }}
          sx={{ flex: 1 }}
        />
        <IconButton aria-label="Delete filter" size="small" onClick={onDelete} color="error">
          <DeleteIcon fontSize="small" />
        </IconButton>
      </Box>

      {/* Bottom row: Operator and Value */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>Operator</InputLabel>
          <Select
            value={filter.operator}
            label="Operator"
            onChange={(e) => handleChange('operator', e.target.value)}
          >
            {OPERATORS.map((op) => (
              <MenuItem key={op.value} value={op.value}>
                {op.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <TextField
          size="small"
          label="Value"
          placeholder="Value"
          value={filter.value}
          onChange={(e) => handleChange('value', e.target.value)}
          sx={{ flex: 1 }}
        />
      </Box>
    </Paper>
  );
};

/**
 * FilterBuilder component
 */
export const FilterBuilder: React.FC<FilterBuilderProps> = ({
  filters,
  onChange,
  eventSource,
  label = 'Filters',
}) => {
  // Get available fields from event schema
  const availableFields = eventSource ? EVENT_SCHEMAS[eventSource] || [] : [];

  // Add new filter
  const addFilter = useCallback(() => {
    const newFilter: FilterRule = {
      id: generateId(),
      field: '',
      operator: 'equals',
      value: '',
    };
    onChange([...filters, newFilter]);
  }, [filters, onChange]);

  // Update filter
  const updateFilter = useCallback(
    (index: number, updatedFilter: FilterRule) => {
      const newFilters = [...filters];
      newFilters[index] = updatedFilter;
      onChange(newFilters);
    },
    [filters, onChange]
  );

  // Delete filter
  const deleteFilter = useCallback(
    (index: number) => {
      onChange(filters.filter((_, i) => i !== index));
    },
    [filters, onChange]
  );

  return (
    <Box>
      {/* Header */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          mb: 1,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <FilterListIcon fontSize="small" color="action" />
          <Typography variant="body2" sx={{
            fontWeight: 500
          }}>
            {label}
          </Typography>
        </Box>
        <Button size="small" startIcon={<AddIcon />} onClick={addFilter}>
          Add Filter
        </Button>
      </Box>
      {/* Description */}
      <Typography
        variant="caption"
        sx={{
          color: "text.secondary",
          display: 'block',
          mb: 1.5
        }}>
        {filters.length === 0
          ? 'No filters - this trigger will fire for all events of this type.'
          : 'Only events matching ALL filters will trigger this workflow.'}
      </Typography>
      {/* Filter list */}
      {filters.length === 0 ? (
        <Paper
          variant="outlined"
          sx={{
            p: 2,
            textAlign: 'center',
            bgcolor: 'action.hover',
          }}
        >
          <Typography variant="body2" sx={{
            color: "text.secondary"
          }}>
            Click "Add Filter" to restrict which events trigger this workflow.
          </Typography>
        </Paper>
      ) : (
        <Box>
          {filters.map((filter, index) => (
            <FilterRow
              key={filter.id}
              filter={filter}
              availableFields={availableFields}
              onUpdate={(updated) => updateFilter(index, updated)}
              onDelete={() => deleteFilter(index)}
            />
          ))}
        </Box>
      )}
      {/* Available fields hint */}
      {eventSource && availableFields.length > 0 && filters.length > 0 && (
        <Box sx={{ mt: 1 }}>
          <Typography variant="caption" sx={{
            color: "text.secondary"
          }}>
            Available fields:{' '}
            {availableFields.map((f) => (
              <Chip
                key={f.name}
                label={f.name}
                size="small"
                sx={{ height: 16, fontSize: '0.55rem', mr: 0.5, mb: 0.5 }}
              />
            ))}
          </Typography>
        </Box>
      )}
    </Box>
  );
};
