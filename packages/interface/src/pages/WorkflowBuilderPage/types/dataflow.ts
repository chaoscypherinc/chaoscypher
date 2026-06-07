// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Data Flow Types for Workflow Builder
 *
 * Defines types for field-level data flow visualization including
 * field schemas, data ports, and field connections between nodes.
 */

/**
 * Field data type enumeration
 */
export type FieldType = 'string' | 'number' | 'boolean' | 'object' | 'array' | 'any';

/**
 * Field schema representation derived from JSON Schema
 */
export interface FieldSchema {
  /** Field name/key */
  name: string;
  /** Data type of the field */
  type: FieldType;
  /** Human-readable description */
  description?: string;
  /** Whether the field is required */
  required: boolean;
  /** Default value if not provided */
  defaultValue?: unknown;
  /** Allowed values (for enums) */
  enum?: unknown[];
  /** For array types, the item type */
  itemType?: FieldType;
  /** For object types, nested schema */
  properties?: FieldSchema[];
}

/**
 * Data port on a node representing an input or output field
 */
export interface DataPort {
  /** Unique port ID in format "nodeId.fieldName" */
  id: string;
  /** Parent node ID */
  nodeId: string;
  /** Field name from schema */
  fieldName: string;
  /** Whether this is an input or output port */
  direction: 'input' | 'output';
  /** Field schema information */
  schema: FieldSchema;
  /** Whether this port has a connection */
  connected?: boolean;
  /** Position offset for rendering (optional) */
  position?: { x: number; y: number };
}

/**
 * Field-level connection between two ports
 */
export interface FieldConnection {
  /** Source field in format "nodeId.fieldName" or just "fieldName" for same edge */
  sourceField: string;
  /** Target field in format "nodeId.fieldName" or just "fieldName" for same edge */
  targetField: string;
  /** Optional transformation expression */
  transform?: string;
}

/**
 * Extended edge data with field connections
 */
export type DataFlowEdgeData = {
  /** Field-level connections on this edge */
  fieldConnections: FieldConnection[];
  /** Edge label (optional) */
  label?: string;
  /** Whether this is a conditional branch */
  branch?: 'true' | 'false';
}

/**
 * Unified entry node data combining workflow inputs and event triggers
 */
export type UnifiedEntryNodeData = {
  /** Node label */
  label: string;
  /** Workflow input fields from input_schema */
  workflowInputs: FieldSchema[];
  /** Selected event trigger source */
  eventSource?: string;
  /** Event trigger fields (derived from eventSource) */
  eventFields?: FieldSchema[];
  /** Combined available output ports */
  outputPorts: DataPort[];
  /** Event filters (optional) */
  filters?: Record<string, unknown>;
}

/**
 * Extended step node data with port information
 */
export type MultiPortStepNodeData = {
  /** Step ID (for existing steps) */
  stepId?: string;
  /** Display name */
  name: string;
  /** Description */
  description?: string;
  /** Tool type */
  toolType: 'system_tool' | 'user_tool' | 'workflow';
  /** Tool identifier */
  toolId: string;
  /** Tool display name */
  toolName: string;
  /** Tool category for styling */
  toolCategory: string;
  /** Input ports derived from tool input_schema */
  inputPorts: DataPort[];
  /** Output ports derived from tool output_schema */
  outputPorts: DataPort[];
  /** Tool configuration with field mappings */
  configuration: Record<string, unknown>;
  /** Continue execution on error */
  continueOnError: boolean;
  /** AI thinking mode */
  thinkingMode?: string;
  /** Execution status for visualization */
  executionStatus?: 'pending' | 'running' | 'completed' | 'failed';
}

import { DataTypeColors } from '../../../theme/colors';

/**
 * Get color for a field type
 */
export function getFieldTypeColor(type: FieldType): string {
  return DataTypeColors[type] || DataTypeColors.any;
}

/**
 * Create a port ID from node ID and field name
 */
export function createPortId(nodeId: string, fieldName: string): string {
  return `${nodeId}.${fieldName}`;
}
