// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Field Classification Utilities for Workflow Variable Picker
 *
 * Classifies input fields as "configuration" vs "data flow" to determine:
 * 1. Whether to show the reference picker (config fields = static only)
 * 2. Which upstream fields are compatible/relevant for linking
 *
 * Also provides type compatibility and semantic relevance scoring
 * to filter and sort upstream field options intelligently.
 */

import type { FieldSchema } from '../types/dataflow';

/**
 * Field source from upstream nodes (for variable picker)
 */
export interface FieldSource {
  nodeId: string;
  nodeName: string;
  field: FieldSchema;
  reference: string;
}

// ============================================================================
// Configuration Field Detection
// ============================================================================

/**
 * Known configuration field name patterns.
 * These control HOW a tool operates, not WHAT data it processes.
 */
const CONFIG_FIELD_PATTERNS: RegExp[] = [
  /^temperature$/i,
  /^max_tokens$/i,
  /^max_retries$/i,
  /^timeout$/i,
  /^top_p$/i,
  /^batch_size$/i,
  /^chunk_overlap$/i,
  /^chunk_strategy$/i,
  /^output_format$/i,
  /^format$/i,
  /^model$/i,
  /^thinking_mode$/i,
  /^enable_/i,
  /^use_/i,
  /^allow_/i,
  /^skip_/i,
  /^limit$/i,
  /^offset$/i,
  /^page_size$/i,
  /^retry/i,
  /^concurrency$/i,
  /^parallelism$/i,
];

/**
 * Check if a field is a configuration parameter (not data flow).
 *
 * Config fields should default to static mode with no reference picker.
 *
 * Criteria:
 * - Has enum values (dropdown selection)
 * - Has min/max constraints (slider/bounded input)
 * - Boolean type (toggle)
 * - Matches known config field name patterns
 */
export function isConfigField(
  name: string,
  schema: Record<string, unknown>
): boolean {
  // Enum fields are always config (dropdown selection)
  if (schema.enum && Array.isArray(schema.enum) && schema.enum.length > 0) {
    return true;
  }

  // Boolean fields are always config (toggle)
  if (schema.type === 'boolean') {
    return true;
  }

  // Numeric fields with min/max constraints are config (sliders)
  if (
    (schema.type === 'number' || schema.type === 'integer') &&
    (schema.minimum !== undefined || schema.maximum !== undefined)
  ) {
    return true;
  }

  // Check against known config field name patterns
  for (const pattern of CONFIG_FIELD_PATTERNS) {
    if (pattern.test(name)) {
      return true;
    }
  }

  return false;
}

// ============================================================================
// Data Flow Field Detection
// ============================================================================

// ============================================================================
// Type Compatibility
// ============================================================================

/**
 * Type compatibility matrix.
 * Maps input types to compatible output types.
 */
const TYPE_COMPATIBILITY: Record<string, string[]> = {
  string: ['string', 'any'],
  number: ['number', 'integer', 'any'],
  integer: ['integer', 'number', 'any'],
  boolean: ['boolean', 'any'],
  object: ['object', 'any'],
  array: ['array', 'any'],
  any: ['string', 'number', 'integer', 'boolean', 'object', 'array', 'any'],
};

/**
 * Check if an output type is compatible with an input type.
 */
function areTypesCompatible(
  inputType: string,
  outputType: string
): boolean {
  const compatibleTypes = TYPE_COMPATIBILITY[inputType] || TYPE_COMPATIBILITY.any;
  return compatibleTypes.includes(outputType);
}

// ============================================================================
// Semantic Relevance Scoring
// ============================================================================

/**
 * Content-related output field patterns.
 * High relevance for text/content input fields.
 */
const CONTENT_OUTPUT_PATTERNS: RegExp[] = [
  /^result$/i,
  /^output$/i,
  /^content$/i,
  /^text$/i,
  /^response$/i,
  /^message$/i,
  /^extracted_/i,
  /^parsed_/i,
  /^formatted_/i,
  /^generated_/i,
];

/**
 * ID-related output field patterns.
 * High relevance for ID input fields.
 */
const ID_OUTPUT_PATTERNS: RegExp[] = [
  /^id$/i,
  /_id$/i,
  /^node_id$/i,
  /^file_id$/i,
  /^entity_id$/i,
  /^source_id$/i,
  /^created_id$/i,
];

/**
 * Low-relevance metadata patterns.
 * These are usually not what users want to link.
 */
const LOW_RELEVANCE_PATTERNS: RegExp[] = [
  /^success$/i,
  /^status$/i,
  /^error$/i,
  /^count$/i,
  /^total$/i,
  /^_metadata$/i,
  /^model$/i,
  /^tokens_used$/i,
];

/**
 * Content input field patterns (for matching with content outputs).
 */
const CONTENT_INPUT_PATTERNS: RegExp[] = [
  /^prompt$/i,
  /^text$/i,
  /^content$/i,
  /^input$/i,
  /^query$/i,
  /^message$/i,
  /^body$/i,
  /^context$/i,
  /^document$/i,
  /^system_prompt$/i,
  /^user_instructions$/i,
];

/**
 * ID input field patterns (for matching with ID outputs).
 */
const ID_INPUT_PATTERNS: RegExp[] = [
  /^id$/i,
  /_id$/i,
  /^node_id$/i,
  /^file_id$/i,
  /^entity_id$/i,
  /^source_id$/i,
];

/**
 * Calculate semantic relevance score between an input and output field.
 *
 * Higher score = more relevant match.
 * Score range: 0-100
 */
function getSemanticRelevance(
  inputName: string,
  outputName: string,
  outputType: string
): number {
  let score = 50; // Base score

  // Boost for string type outputs when input is content-related
  if (outputType === 'string') {
    score += 5;
  }

  // Check if input is a content field
  const isContentInput = CONTENT_INPUT_PATTERNS.some((p) => p.test(inputName));
  const isIdInput = ID_INPUT_PATTERNS.some((p) => p.test(inputName));

  // Check output field characteristics
  const isContentOutput = CONTENT_OUTPUT_PATTERNS.some((p) => p.test(outputName));
  const isIdOutput = ID_OUTPUT_PATTERNS.some((p) => p.test(outputName));
  const isLowRelevance = LOW_RELEVANCE_PATTERNS.some((p) => p.test(outputName));

  // Boost for matching categories
  if (isContentInput && isContentOutput) {
    score += 40; // Strong content-to-content match
  } else if (isIdInput && isIdOutput) {
    score += 40; // Strong ID-to-ID match
  } else if (isContentInput && isIdOutput) {
    score -= 20; // Mismatch: content field shouldn't get IDs
  } else if (isIdInput && isContentOutput) {
    score -= 20; // Mismatch: ID field shouldn't get content
  }

  // Penalize low-relevance fields
  if (isLowRelevance) {
    score -= 30;
  }

  // Boost for exact or similar name matches
  if (inputName.toLowerCase() === outputName.toLowerCase()) {
    score += 25; // Exact match
  } else if (
    inputName.toLowerCase().includes(outputName.toLowerCase()) ||
    outputName.toLowerCase().includes(inputName.toLowerCase())
  ) {
    score += 15; // Partial match
  }

  // Slight boost for common result/output names
  if (/^(result|output)$/i.test(outputName)) {
    score += 10;
  }

  // Clamp to 0-100
  return Math.max(0, Math.min(100, score));
}

// ============================================================================
// Field Filtering and Sorting
// ============================================================================

/**
 * Filter and sort upstream fields for a given input field.
 *
 * Returns fields that are:
 * 1. Type-compatible with the input field
 * 2. Sorted by semantic relevance (most relevant first)
 */
export function filterAndSortUpstreamFields(
  inputName: string,
  inputType: string,
  upstreamFields: FieldSource[]
): FieldSource[] {
  // Filter by type compatibility
  const compatible = upstreamFields.filter((field) =>
    areTypesCompatible(inputType, field.field.type)
  );

  // Sort by semantic relevance (descending)
  const sorted = compatible.sort((a, b) => {
    const scoreA = getSemanticRelevance(inputName, a.field.name, a.field.type);
    const scoreB = getSemanticRelevance(inputName, b.field.name, b.field.type);
    return scoreB - scoreA; // Higher score first
  });

  return sorted;
}
