// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Schema Parser Utilities
 *
 * Functions for parsing JSON Schema into our internal FieldSchema format
 * and validating field compatibility.
 */

import type { FieldSchema, FieldType, DataPort } from '../types/dataflow';

/**
 * Map JSON Schema type to our FieldType
 */
function mapJsonSchemaType(type: string | string[] | undefined): FieldType {
  if (!type) return 'any';

  // Handle array of types (e.g., ["string", "null"])
  const primaryType = Array.isArray(type) ? type.find((t) => t !== 'null') || type[0] : type;

  switch (primaryType) {
    case 'string':
      return 'string';
    case 'number':
    case 'integer':
      return 'number';
    case 'boolean':
      return 'boolean';
    case 'object':
      return 'object';
    case 'array':
      return 'array';
    default:
      return 'any';
  }
}

/**
 * Parse a JSON Schema property into a FieldSchema
 */
function parseProperty(
  name: string,
  schema: Record<string, unknown>,
  required: boolean
): FieldSchema {
  const type = mapJsonSchemaType(schema.type as string | string[] | undefined);

  const fieldSchema: FieldSchema = {
    name,
    type,
    required,
    description: schema.description as string | undefined,
    defaultValue: schema.default,
  };

  // Handle enum values
  if (Array.isArray(schema.enum)) {
    fieldSchema.enum = schema.enum;
  }

  // Handle array items
  if (type === 'array' && schema.items) {
    const items = schema.items as Record<string, unknown>;
    fieldSchema.itemType = mapJsonSchemaType(items.type as string | undefined);
  }

  // Handle nested object properties
  if (type === 'object' && schema.properties) {
    const properties = schema.properties as Record<string, Record<string, unknown>>;
    const requiredFields = (schema.required as string[]) || [];
    fieldSchema.properties = Object.entries(properties).map(([propName, propSchema]) =>
      parseProperty(propName, propSchema, requiredFields.includes(propName))
    );
  }

  return fieldSchema;
}

/**
 * Parse a JSON Schema object into an array of FieldSchema
 *
 * @param schema - JSON Schema object with properties
 * @returns Array of FieldSchema
 */
export function parseJsonSchema(schema: Record<string, unknown> | null | undefined): FieldSchema[] {
  if (!schema) return [];

  // Handle empty schema
  if (Object.keys(schema).length === 0) return [];

  // Get properties and required fields
  const properties = (schema.properties as Record<string, Record<string, unknown>>) || {};
  const requiredFields = (schema.required as string[]) || [];

  // If no properties, check if the schema itself defines a type
  if (Object.keys(properties).length === 0) {
    // Check for root-level type definition
    if (schema.type) {
      return [
        {
          name: 'value',
          type: mapJsonSchemaType(schema.type as string),
          required: true,
          description: schema.description as string | undefined,
        },
      ];
    }
    return [];
  }

  // Parse each property
  return Object.entries(properties).map(([name, propSchema]) =>
    parseProperty(name, propSchema, requiredFields.includes(name))
  );
}

/**
 * Parse tool input_schema into FieldSchema array
 */
export function parseToolInputSchema(inputSchema: Record<string, unknown> | null | undefined): FieldSchema[] {
  return parseJsonSchema(inputSchema);
}

/**
 * Parse tool output_schema into FieldSchema array
 */
export function parseToolOutputSchema(outputSchema: Record<string, unknown> | null | undefined): FieldSchema[] {
  return parseJsonSchema(outputSchema);
}

/**
 * Convert FieldSchema array to DataPort array
 */
export function fieldsToDataPorts(
  nodeId: string,
  fields: FieldSchema[],
  direction: 'input' | 'output'
): DataPort[] {
  return fields.map((field) => ({
    id: `${nodeId}.${field.name}`,
    nodeId,
    fieldName: field.name,
    direction,
    schema: field,
  }));
}
