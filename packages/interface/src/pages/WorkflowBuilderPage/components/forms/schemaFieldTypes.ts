// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Schema field types and JSON Schema converters for SchemaFieldBuilder.
 *
 * Lives in its own file so SchemaFieldBuilder.tsx is Fast-Refresh-clean.
 */
import type { FieldType } from '../../types/dataflow';

/**
 * Field definition for the builder
 */
export interface SchemaField {
  id: string;
  name: string;
  type: FieldType;
  description: string;
  required: boolean;
  defaultValue?: string;
  enumValues?: string[];
}

function generateId(): string {
  return `field-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Convert SchemaFields to JSON Schema format
 */
export function fieldsToJsonSchema(fields: SchemaField[]): Record<string, unknown> {
  if (fields.length === 0) return {};

  const properties: Record<string, unknown> = {};
  const required: string[] = [];

  for (const field of fields) {
    if (!field.name) continue;

    const prop: Record<string, unknown> = {
      type: field.type === 'any' ? 'string' : field.type,
    };

    if (field.description) {
      prop.description = field.description;
    }

    if (field.defaultValue !== undefined && field.defaultValue !== '') {
      // Parse default value based on type
      if (field.type === 'number') {
        prop.default = parseFloat(field.defaultValue);
      } else if (field.type === 'boolean') {
        prop.default = field.defaultValue.toLowerCase() === 'true';
      } else {
        prop.default = field.defaultValue;
      }
    }

    if (field.enumValues && field.enumValues.length > 0) {
      prop.enum = field.enumValues;
    }

    properties[field.name] = prop;

    if (field.required) {
      required.push(field.name);
    }
  }

  return {
    type: 'object',
    properties,
    ...(required.length > 0 && { required }),
  };
}

/**
 * Convert JSON Schema to SchemaFields
 */
export function jsonSchemaToFields(schema: Record<string, unknown> | null): SchemaField[] {
  if (!schema || typeof schema !== 'object') return [];

  const properties = (schema.properties as Record<string, Record<string, unknown>>) || {};
  const requiredFields = (schema.required as string[]) || [];

  return Object.entries(properties).map(([name, prop]) => ({
    id: generateId(),
    name,
    type: (prop.type as FieldType) || 'string',
    description: (prop.description as string) || '',
    required: requiredFields.includes(name),
    defaultValue: prop.default !== undefined ? String(prop.default) : undefined,
    enumValues: prop.enum as string[] | undefined,
  }));
}
