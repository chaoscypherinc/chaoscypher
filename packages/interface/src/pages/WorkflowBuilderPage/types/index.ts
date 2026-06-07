// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowBuilder TypeScript types
 *
 * Type definitions for the workflow builder canvas, nodes, edges, and state management.
 */

import type { Node, Edge } from '@xyflow/react';
import type { FieldConnection } from './dataflow';

// Re-export dataflow types
export * from './dataflow';

// Validation error type — lives here (not in utils/serialization.ts) to keep
// types/ as the single source of truth for the WorkflowBuilder type surface.
export interface ValidationError {
  nodeId?: string;
  field?: string;
  message: string;
}

// ============================================================================
// Node Data Types
// ============================================================================

/**
 * Data for a workflow step node (tool execution)
 *
 * Uses `type` instead of `interface` so it satisfies the
 * `Record<string, unknown>` constraint required by @xyflow/react v12's Node generic.
 */
export type WorkflowStepNodeData = {
  /** Unique step identifier (maps to WorkflowStep.id) */
  stepId?: string;
  /** Display name for the step */
  name: string;
  /** Optional description */
  description?: string;
  /** Type of tool being executed */
  toolType: 'system_tool' | 'user_tool' | 'workflow';
  /** ID of the tool to execute */
  toolId: string;
  /** Human-readable tool name for display */
  toolName: string;
  /** Tool category for styling (ai, graph, logic, data, external) */
  toolCategory: string;
  /** Tool-specific configuration */
  configuration: Record<string, unknown>;
  /** Whether to continue workflow on step failure */
  continueOnError: boolean;
  /** AI thinking mode (enabled, disabled, auto) */
  thinkingMode?: string;
  /** Step execution status during test runs */
  executionStatus?: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
}

/**
 * Data for a trigger node (workflow start)
 */
export type TriggerNodeData = {
  /** Event source that triggers the workflow */
  eventSource: string;
  /** Filters to apply to events */
  filters: Record<string, unknown>;
  /** Display label */
  label: string;
}

/**
 * Data for an event trigger node (individual trigger)
 * Each trigger that points to this workflow is represented as its own node.
 */
export type EventTriggerNodeData = {
  /** Database trigger ID (null for new/unsaved triggers) */
  triggerId: string | null;
  /** Trigger name */
  name: string;
  /** Event source that triggers the workflow */
  eventSource: string;
  /** Filters to apply to events */
  filters: Record<string, unknown>;
  /** Workflow inputs mapping for trigger execution */
  workflowInputs: Record<string, unknown> | null;
  /** Whether the trigger is enabled */
  enabled: boolean;
  /** Priority (lower = higher priority) */
  priority: number;
}

/**
 * Data for a conditional node (if/else branching)
 */
export type ConditionalNodeData = {
  /** Display name */
  name: string;
  /** Condition expression */
  condition: StepCondition;
}

/**
 * Condition definition for conditional nodes
 */
export interface StepCondition {
  /** Field path to evaluate (e.g., "{{steps.step1.output.status}}") */
  field: string;
  /** Comparison operator */
  operator: ConditionOperator;
  /** Value to compare against */
  value: unknown;
}

export type ConditionOperator =
  | 'equals'
  | 'not_equals'
  | 'contains'
  | 'not_contains'
  | 'greater_than'
  | 'less_than'
  | 'is_empty'
  | 'is_not_empty';

// ============================================================================
// Edge Data Types
// ============================================================================

/**
 * Data for a standard workflow edge
 */
export type WorkflowEdgeData = {
  /** Optional label to display on edge */
  label?: string;
  /** Field-level connections on this edge */
  fieldConnections?: FieldConnection[];
}

/**
 * Data for a conditional edge (from if/else branches)
 */
export type ConditionalEdgeData = {
  /** Which branch this edge represents */
  branch: 'true' | 'false';
  /** Label is auto-set based on branch */
  label: string;
}

// ============================================================================
// Canvas State Types
// ============================================================================

/**
 * Snapshot of canvas state for undo/redo
 */
export interface CanvasSnapshot {
  nodes: Node[];
  edges: Edge[];
  timestamp: number;
}

/**
 * Workflow metadata (from API)
 */
export interface WorkflowMetadata {
  id: string;
  name: string;
  description?: string;
  category?: string;
  is_system: boolean;
  is_active: boolean;
  expose_as_ai_tool: boolean;
  input_schema: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  tags?: string[];
  icon?: string;
  created_at: string;
  updated_at: string;
}

// ============================================================================
// Tool Types
// ============================================================================

/**
 * System tool definition (from API)
 */
export interface SystemTool {
  id: string;
  category: string;
  icon?: string | null;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  version: string;
  is_active: boolean;
}

// ============================================================================
// Step Template Types
// ============================================================================

/**
 * Saved step template for reuse
 */
export interface StepTemplate {
  id: string;
  name: string;
  description?: string;
  category: string;
  toolType: 'system_tool' | 'user_tool' | 'workflow';
  toolId: string;
  configuration: Record<string, unknown>;
  createdAt: string;
}

