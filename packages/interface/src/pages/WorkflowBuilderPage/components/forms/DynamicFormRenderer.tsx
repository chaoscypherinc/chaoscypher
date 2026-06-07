// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * DynamicFormRenderer: Renders form fields dynamically from JSON Schema
 *
 * Generates appropriate form controls (text, number, select, switch, etc.)
 * based on the tool's input_schema. Supports field references from upstream
 * nodes via a "reference mode" toggle.
 */

import React, { useState, useMemo } from 'react';
import {
  Box,
  Typography,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Switch,
  FormControlLabel,
  Slider,
  Chip,
  IconButton,
  Tooltip,
  Alert,
} from '@mui/material';
import LinkIcon from '@mui/icons-material/Link';
import LinkOffIcon from '@mui/icons-material/LinkOff';
import { getFieldTypeColor } from '../../types/dataflow';
import {
  isConfigField,
  filterAndSortUpstreamFields,
  type FieldSource,
} from '../../utils/fieldClassification';
import { DataTypeColors } from '../../../../theme/colors';

interface DynamicFormRendererProps {
  /** JSON Schema defining the form fields */
  schema: Record<string, unknown> | null;
  /** Current configuration values */
  values: Record<string, unknown>;
  /** Callback when values change */
  onChange: (values: Record<string, unknown>) => void;
  /** Available fields from upstream nodes for references */
  availableFields?: FieldSource[];
  /** Whether to show field reference option */
  allowReferences?: boolean;
  /** Label for the form section */
  label?: string;
}

/**
 * Check if a value is a field reference
 */
function isFieldReference(value: unknown): boolean {
  return typeof value === 'string' && /\{\{\s*steps\.[^}]+\}\}/.test(value);
}

/**
 * Single form field component
 */
interface FormFieldProps {
  name: string;
  schema: Record<string, unknown>;
  value: unknown;
  onChange: (value: unknown) => void;
  availableFields?: FieldSource[];
  allowReferences?: boolean;
  required?: boolean;
}

const FormField: React.FC<FormFieldProps> = ({
  name,
  schema,
  value,
  onChange,
  availableFields = [],
  allowReferences = true,
  required = false,
}) => {
  const fieldType = schema.type as string;
  const description = schema.description as string | undefined;
  const defaultValue = schema.default;
  const enumValues = schema.enum as unknown[] | undefined;
  const minimum = schema.minimum as number | undefined;
  const maximum = schema.maximum as number | undefined;

  const typeColor = DataTypeColors[fieldType] || DataTypeColors.string;

  // Check if this is a configuration field (should not show reference picker)
  const isConfig = isConfigField(name, schema);

  // Filter and sort available fields by type compatibility and semantic relevance
  const filteredFields = useMemo(() => {
    if (!availableFields || availableFields.length === 0) return [];
    return filterAndSortUpstreamFields(name, fieldType, availableFields);
  }, [availableFields, name, fieldType]);

  // Determine if field can use references:
  // - Must have allowReferences enabled
  // - Must have compatible upstream fields available
  // - Must NOT be a config field (config fields should be static)
  const canUseReferences = allowReferences &&
    filteredFields.length > 0 &&
    !isConfig;

  // Default to reference mode when:
  // 1. References are allowed and available
  // 2. Value is empty or already a reference
  // 3. Field is a data flow field (not config)
  const hasExistingStaticValue = value !== undefined &&
    value !== null &&
    value !== '' &&
    !isFieldReference(value);

  const [isStaticMode, setIsStaticMode] = useState(
    hasExistingStaticValue || !canUseReferences
  );

  // Handle value change
  const handleChange = (newValue: unknown) => {
    onChange(newValue);
  };

  // Toggle between static and reference mode
  const toggleMode = () => {
    if (isStaticMode) {
      // Switch to reference mode - clear the static value
      setIsStaticMode(false);
      onChange('');
    } else {
      // Switch to static mode - clear the reference
      setIsStaticMode(true);
      onChange(defaultValue ?? '');
    }
  };

  // Select field reference
  const selectReference = (ref: string) => {
    onChange(ref);
  };

  // Reference mode selector - uses filtered and sorted fields
  const renderReferenceSelector = () => (
    <FormControl fullWidth size="small">
      <InputLabel>Select Field Reference</InputLabel>
      <Select
        value={typeof value === 'string' ? value : ''}
        label="Select Field Reference"
        onChange={(e) => selectReference(e.target.value)}
      >
        {filteredFields.length === 0 ? (
          <MenuItem disabled>No compatible upstream fields available</MenuItem>
        ) : (
          filteredFields.map((field) => (
            <MenuItem key={field.reference} value={field.reference}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                  {field.nodeName}.{field.field.name}
                </Typography>
                <Chip
                  label={field.field.type}
                  size="small"
                  sx={{
                    height: 16,
                    fontSize: '0.55rem',
                    bgcolor: `${getFieldTypeColor(field.field.type)}15`,
                    color: getFieldTypeColor(field.field.type),
                  }}
                />
              </Box>
            </MenuItem>
          ))
        )}
      </Select>
    </FormControl>
  );

  // Render static input based on type
  const renderStaticInput = () => {
    // Enum values - render as select
    if (enumValues && enumValues.length > 0) {
      return (
        <FormControl fullWidth size="small">
          <InputLabel>{name}</InputLabel>
          <Select
            value={value ?? ''}
            label={name}
            onChange={(e) => handleChange(e.target.value)}
          >
            {enumValues.map((enumVal) => (
              <MenuItem key={String(enumVal)} value={enumVal as string}>
                {String(enumVal)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      );
    }

    // Boolean - render as switch
    if (fieldType === 'boolean') {
      return (
        <FormControlLabel
          control={
            <Switch
              checked={Boolean(value)}
              onChange={(e) => handleChange(e.target.checked)}
              size="small"
            />
          }
          label={
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Typography variant="body2">{name}</Typography>
              {required && (
                <Typography color="error" component="span">
                  *
                </Typography>
              )}
            </Box>
          }
        />
      );
    }

    // Number with range - render as slider
    if ((fieldType === 'number' || fieldType === 'integer') && minimum !== undefined && maximum !== undefined) {
      const displayValue = value !== undefined && value !== null ? String(value) : String(minimum);
      return (
        <Box sx={{ px: 1 }}>
          <Typography variant="body2" gutterBottom>
            {name}: {displayValue}
            {required && (
              <Typography color="error" component="span">
                *
              </Typography>
            )}
          </Typography>
          <Slider
            value={Number(value ?? minimum)}
            onChange={(_, v) => handleChange(v)}
            min={minimum}
            max={maximum}
            step={fieldType === 'integer' ? 1 : 0.1}
            valueLabelDisplay="auto"
            size="small"
          />
        </Box>
      );
    }

    // Number - render as number input
    if (fieldType === 'number' || fieldType === 'integer') {
      return (
        <TextField
          fullWidth
          size="small"
          label={name}
          type="number"
          value={value ?? ''}
          onChange={(e) => handleChange(e.target.value === '' ? '' : Number(e.target.value))}
          required={required}
          slotProps={{
            htmlInput: {
              step: fieldType === 'integer' ? 1 : 0.1,
              min: minimum,
              max: maximum,
            }
          }}
        />
      );
    }

    // Multiline string (based on name hints)
    const isMultiline =
      name.toLowerCase().includes('prompt') ||
      name.toLowerCase().includes('content') ||
      name.toLowerCase().includes('text') ||
      name.toLowerCase().includes('body') ||
      name.toLowerCase().includes('message');

    // String - render as text input
    return (
      <TextField
        fullWidth
        size="small"
        label={name}
        value={value ?? ''}
        onChange={(e) => handleChange(e.target.value)}
        required={required}
        multiline={isMultiline}
        rows={isMultiline ? 3 : 1}
        placeholder={defaultValue ? `Default: ${defaultValue}` : undefined}
      />
    );
  };

  return (
    <Box sx={{ mb: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1 }}>
        {/* Field label with type indicator */}
        <Box
          sx={{
            width: 4,
            height: 36,
            borderRadius: 1,
            bgcolor: typeColor,
            flexShrink: 0,
            mt: 0.5,
          }}
        />

        {/* Main input area */}
        <Box sx={{ flex: 1 }}>
          {isStaticMode ? renderStaticInput() : renderReferenceSelector()}

          {/* Description */}
          {description && (
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                display: 'block',
                mt: 0.5,
                ml: 0.5
              }}>
              {description}
            </Typography>
          )}
        </Box>

        {/* Mode toggle - only show if field can use references */}
        {canUseReferences && (
          <Tooltip title={isStaticMode ? 'Link to upstream field' : 'Enter static value'}>
            <IconButton
              aria-label={isStaticMode ? 'Link to upstream field' : 'Enter static value'}
              size="small"
              onClick={toggleMode}
              color={isStaticMode ? 'default' : 'primary'}
              sx={{ mt: 0.5 }}
            >
              {isStaticMode ? <LinkOffIcon /> : <LinkIcon />}
            </IconButton>
          </Tooltip>
        )}
      </Box>
    </Box>
  );
};

/**
 * DynamicFormRenderer component
 */
export const DynamicFormRenderer: React.FC<DynamicFormRendererProps> = ({
  schema,
  values,
  onChange,
  availableFields = [],
  allowReferences = true,
  label = 'Configuration',
}) => {
  // Parse schema properties
  const { properties, required } = useMemo(() => {
    if (!schema || typeof schema !== 'object') {
      return { properties: {}, required: [] };
    }

    return {
      properties: (schema.properties as Record<string, Record<string, unknown>>) || {},
      required: (schema.required as string[]) || [],
    };
  }, [schema]);

  const fieldNames = Object.keys(properties);

  // Handle field value change
  const handleFieldChange = (fieldName: string, value: unknown) => {
    onChange({
      ...values,
      [fieldName]: value,
    });
  };

  // No schema - show message
  if (!schema || fieldNames.length === 0) {
    return (
      <Alert severity="info" sx={{ mb: 2 }}>
        This tool has no configurable options.
      </Alert>
    );
  }

  return (
    <Box>
      {/* Header */}
      <Typography
        variant="body2"
        sx={{
          fontWeight: 500,
          mb: 2
        }}>
        {label}
      </Typography>
      {/* Required fields */}
      {fieldNames.filter((name) => required.includes(name)).length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              fontWeight: 500,
              display: 'block',
              mb: 1
            }}>
            REQUIRED
          </Typography>
          {fieldNames
            .filter((name) => required.includes(name))
            .map((name) => (
              <FormField
                key={name}
                name={name}
                schema={properties[name]}
                value={values[name]}
                onChange={(v) => handleFieldChange(name, v)}
                availableFields={availableFields}
                allowReferences={allowReferences}
                required
              />
            ))}
        </Box>
      )}
      {/* Optional fields */}
      {fieldNames.filter((name) => !required.includes(name)).length > 0 && (
        <Box>
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              fontWeight: 500,
              display: 'block',
              mb: 1
            }}>
            OPTIONAL
          </Typography>
          {fieldNames
            .filter((name) => !required.includes(name))
            .map((name) => (
              <FormField
                key={name}
                name={name}
                schema={properties[name]}
                value={values[name]}
                onChange={(v) => handleFieldChange(name, v)}
                availableFields={availableFields}
                allowReferences={allowReferences}
              />
            ))}
        </Box>
      )}
      {/* Reference hint */}
      {allowReferences && availableFields.length > 0 && (
        <Typography
          variant="caption"
          sx={{
            color: "text.secondary",
            mt: 2,
            display: 'block'
          }}>
          Click the link icon to use data from previous steps instead of static values.
        </Typography>
      )}
    </Box>
  );
};
