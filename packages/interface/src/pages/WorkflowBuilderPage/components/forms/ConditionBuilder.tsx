// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ConditionBuilder: Visual builder for workflow conditional logic
 *
 * Allows users to create if/else conditions using dropdowns and inputs
 * instead of writing raw JSON. Supports field references from upstream steps.
 */

import React, { useCallback, useMemo } from 'react';
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
  ToggleButtonGroup,
  ToggleButton,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import type { FieldSchema } from '../../types/dataflow';
import type { ConditionGroup, ConditionRule, ConditionOperator } from './conditionTypes';

/**
 * Operator definitions with labels and descriptions
 */
const OPERATORS: { value: ConditionOperator; label: string; needsValue: boolean }[] = [
  { value: 'equals', label: 'equals', needsValue: true },
  { value: 'not_equals', label: 'does not equal', needsValue: true },
  { value: 'contains', label: 'contains', needsValue: true },
  { value: 'not_contains', label: 'does not contain', needsValue: true },
  { value: 'starts_with', label: 'starts with', needsValue: true },
  { value: 'ends_with', label: 'ends with', needsValue: true },
  { value: 'greater_than', label: 'is greater than', needsValue: true },
  { value: 'less_than', label: 'is less than', needsValue: true },
  { value: 'greater_or_equal', label: 'is at least', needsValue: true },
  { value: 'less_or_equal', label: 'is at most', needsValue: true },
  { value: 'is_empty', label: 'is empty', needsValue: false },
  { value: 'is_not_empty', label: 'is not empty', needsValue: false },
  { value: 'matches_regex', label: 'matches pattern', needsValue: true },
];

/**
 * Available field source from upstream nodes
 */
interface FieldSource {
  nodeId: string;
  nodeName: string;
  field: FieldSchema;
  reference: string; // {{ steps.nodeId.fieldName }}
}

interface ConditionBuilderProps {
  /** Current condition */
  condition: ConditionGroup;
  /** Callback when condition changes */
  onChange: (condition: ConditionGroup) => void;
  /** Available fields from upstream nodes */
  availableFields: FieldSource[];
  /** Label for the builder */
  label?: string;
}

/**
 * Generate unique ID
 */
function generateId(): string {
  return `rule-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Single condition rule component
 */
interface RuleRowProps {
  rule: ConditionRule;
  availableFields: FieldSource[];
  onUpdate: (rule: ConditionRule) => void;
  onDelete: () => void;
}

const RuleRow: React.FC<RuleRowProps> = ({
  rule,
  availableFields,
  onUpdate,
  onDelete,
}) => {
  const operator = OPERATORS.find((op) => op.value === rule.operator);
  const needsValue = operator?.needsValue ?? true;

  // Group fields by node
  const fieldOptions = useMemo(() => {
    const grouped: Record<string, FieldSource[]> = {};
    for (const field of availableFields) {
      if (!grouped[field.nodeName]) {
        grouped[field.nodeName] = [];
      }
      grouped[field.nodeName].push(field);
    }
    return grouped;
  }, [availableFields]);

  const handleChange = (key: keyof ConditionRule, value: unknown) => {
    onUpdate({ ...rule, [key]: value });
  };

  return (
    <Paper
      variant="outlined"
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        p: 1.5,
        mb: 1,
        flexWrap: 'wrap',
      }}
    >
      {/* Field selector */}
      <FormControl size="small" sx={{ minWidth: 180, flex: 1 }}>
        <InputLabel>Field</InputLabel>
        <Select
          value={rule.field}
          label="Field"
          onChange={(e) => handleChange('field', e.target.value)}
        >
          {Object.entries(fieldOptions).map(([nodeName, fields]) => [
            <MenuItem key={`header-${nodeName}`} disabled sx={{ opacity: 0.7 }}>
              <Typography variant="caption" sx={{
                fontWeight: 600
              }}>
                {nodeName}
              </Typography>
            </MenuItem>,
            ...fields.map((field) => (
              <MenuItem key={field.reference} value={field.reference}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                    {field.field.name}
                  </Typography>
                  <Chip
                    label={field.field.type}
                    size="small"
                    sx={{ height: 16, fontSize: '0.6rem' }}
                  />
                </Box>
              </MenuItem>
            )),
          ])}
          {availableFields.length === 0 && (
            <MenuItem disabled>No fields available</MenuItem>
          )}
        </Select>
      </FormControl>
      {/* Operator selector */}
      <FormControl size="small" sx={{ minWidth: 150 }}>
        <InputLabel>Condition</InputLabel>
        <Select
          value={rule.operator}
          label="Condition"
          onChange={(e) => handleChange('operator', e.target.value)}
        >
          {OPERATORS.map((op) => (
            <MenuItem key={op.value} value={op.value}>
              {op.label}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      {/* Value input */}
      {needsValue && (
        <>
          {/* Value type toggle */}
          <ToggleButtonGroup
            size="small"
            value={rule.valueType}
            exclusive
            onChange={(_, v) => v && handleChange('valueType', v)}
          >
            <ToggleButton value="static" sx={{ px: 1.5, py: 0.5 }}>
              Value
            </ToggleButton>
            <ToggleButton value="reference" sx={{ px: 1.5, py: 0.5 }}>
              Field
            </ToggleButton>
          </ToggleButtonGroup>

          {rule.valueType === 'static' ? (
            <TextField
              size="small"
              placeholder="Enter value"
              value={rule.value}
              onChange={(e) => handleChange('value', e.target.value)}
              sx={{ flex: 1, minWidth: 120 }}
            />
          ) : (
            <FormControl size="small" sx={{ minWidth: 150, flex: 1 }}>
              <InputLabel>Compare to</InputLabel>
              <Select
                value={rule.value}
                label="Compare to"
                onChange={(e) => handleChange('value', e.target.value)}
              >
                {availableFields.map((field) => (
                  <MenuItem key={field.reference} value={field.reference}>
                    <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                      {field.nodeName}.{field.field.name}
                    </Typography>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
        </>
      )}
      {/* Delete button */}
      <IconButton aria-label="Delete condition" size="small" onClick={onDelete} color="error">
        <DeleteIcon fontSize="small" />
      </IconButton>
    </Paper>
  );
};

/**
 * ConditionBuilder component
 */
export const ConditionBuilder: React.FC<ConditionBuilderProps> = ({
  condition,
  onChange,
  availableFields,
  label = 'When',
}) => {
  // Add new rule
  const addRule = useCallback(() => {
    const newRule: ConditionRule = {
      id: generateId(),
      field: '',
      operator: 'equals',
      value: '',
      valueType: 'static',
    };
    onChange({
      ...condition,
      rules: [...condition.rules, newRule],
    });
  }, [condition, onChange]);

  // Update rule
  const updateRule = useCallback(
    (index: number, updatedRule: ConditionRule) => {
      const newRules = [...condition.rules];
      newRules[index] = updatedRule;
      onChange({ ...condition, rules: newRules });
    },
    [condition, onChange]
  );

  // Delete rule
  const deleteRule = useCallback(
    (index: number) => {
      onChange({
        ...condition,
        rules: condition.rules.filter((_, i) => i !== index),
      });
    },
    [condition, onChange]
  );

  // Toggle logic
  const toggleLogic = useCallback(() => {
    onChange({
      ...condition,
      logic: condition.logic === 'AND' ? 'OR' : 'AND',
    });
  }, [condition, onChange]);

  return (
    <Box>
      {/* Header */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          mb: 2,
        }}
      >
        <Typography variant="body2" sx={{
          fontWeight: 500
        }}>
          {label}
        </Typography>
        {condition.rules.length > 1 && (
          <Chip
            label={condition.logic === 'AND' ? 'ALL must match' : 'ANY must match'}
            size="small"
            onClick={toggleLogic}
            color={condition.logic === 'AND' ? 'primary' : 'secondary'}
            sx={{ cursor: 'pointer' }}
          />
        )}
      </Box>
      {/* Rules */}
      {condition.rules.length === 0 ? (
        <Paper
          variant="outlined"
          sx={{
            p: 3,
            textAlign: 'center',
            bgcolor: 'action.hover',
          }}
        >
          <Typography variant="body2" gutterBottom sx={{
            color: "text.secondary"
          }}>
            No conditions defined. This branch will always execute.
          </Typography>
          <Button size="small" startIcon={<AddIcon />} onClick={addRule}>
            Add Condition
          </Button>
        </Paper>
      ) : (
        <Box>
          {condition.rules.map((rule, index) => (
            <React.Fragment key={rule.id}>
              <RuleRow
                rule={rule}
                availableFields={availableFields}
                onUpdate={(updated) => updateRule(index, updated)}
                onDelete={() => deleteRule(index)}
              />
              {index < condition.rules.length - 1 && (
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    my: 0.5,
                  }}
                >
                  <Chip
                    label={condition.logic}
                    size="small"
                    variant="outlined"
                    onClick={toggleLogic}
                    sx={{ cursor: 'pointer', fontSize: '0.7rem' }}
                  />
                </Box>
              )}
            </React.Fragment>
          ))}

          {/* Add rule button */}
          <Button
            size="small"
            startIcon={<AddIcon />}
            onClick={addRule}
            sx={{ mt: 1 }}
          >
            Add Condition
          </Button>
        </Box>
      )}
    </Box>
  );
};
