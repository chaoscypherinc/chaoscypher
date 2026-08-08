// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useWorkflowSerialization: Hook for workflow save/load operations
 *
 * Handles converting between canvas state and API format,
 * saving workflows, and loading existing workflows.
 */

import { useState, useCallback } from 'react';
import type { Node, Edge } from '@xyflow/react';
import { workflowsApi } from '../../../services/api/workflows';
import { triggersApi } from '../../../services/api/triggers';
import { serializeWorkflowSteps, deserializeWorkflow, validateWorkflow } from '../utils/serialization';
import type { ValidationError, WorkflowMetadata, EventTriggerNodeData } from '../types';

/** A canvas-node-to-server-step id assignment made during a save. */
export interface StepIdAssignment {
  /** Canvas node id the step was created from. */
  nodeId: string;
  /** Server-assigned step id. */
  stepId: string;
}

interface UseWorkflowSerializationOptions {
  onSuccess?: (message: string) => void;
  onError?: (error: string) => void;
  /**
   * Called after a save creates new steps, with the server-assigned ids so
   * the caller can write them back onto the canvas nodes (`data.stepId`).
   * Without this write-back, unchanged steps get deleted and recreated on
   * every subsequent save.
   */
  onStepIdsAssigned?: (assignments: StepIdAssignment[]) => void;
}

interface UseWorkflowSerializationResult {
  // State
  workflow: WorkflowMetadata | null;
  isLoading: boolean;
  isSaving: boolean;
  error: string | null;
  validationErrors: ValidationError[];

  // Actions
  loadWorkflow: (workflowId: string) => Promise<{ nodes: Node[]; edges: Edge[] } | null>;
  saveWorkflow: (
    nodes: Node[],
    edges: Edge[],
    metadata: Partial<WorkflowMetadata>
  ) => Promise<string | null>;
  createWorkflow: (
    nodes: Node[],
    edges: Edge[],
    metadata: Partial<WorkflowMetadata>
  ) => Promise<string | null>;
  validate: (nodes: Node[], edges: Edge[]) => ValidationError[];
}

export function useWorkflowSerialization(
  options: UseWorkflowSerializationOptions = {}
): UseWorkflowSerializationResult {
  const { onSuccess, onError, onStepIdsAssigned } = options;

  const [workflow, setWorkflow] = useState<WorkflowMetadata | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [validationErrors, setValidationErrors] = useState<ValidationError[]>([]);

  /**
   * Load an existing workflow from the API
   */
  const loadWorkflow = useCallback(
    async (workflowId: string): Promise<{ nodes: Node[]; edges: Edge[] } | null> => {
      setIsLoading(true);
      setError(null);

      try {
        // Fetch workflow metadata, steps, and triggers in parallel
        const [workflowData, steps, triggers] = await Promise.all([
          workflowsApi.get(workflowId),
          workflowsApi.listSteps(workflowId),
          workflowsApi.listTriggers(workflowId).catch(() => []), // Gracefully handle if triggers fail
        ]);

        setWorkflow(workflowData as WorkflowMetadata);

        // Convert to canvas format (now with triggers)
        const { nodes, edges } = deserializeWorkflow(
          workflowData as WorkflowMetadata,
          steps,
          triggers
        );

        return { nodes, edges };
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to load workflow';
        setError(message);
        onError?.(message);
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [onError]
  );

  /**
   * Save an existing workflow
   */
  const saveWorkflow = useCallback(
    async (
      nodes: Node[],
      edges: Edge[],
      metadata: Partial<WorkflowMetadata>
    ): Promise<string | null> => {
      if (!workflow?.id) {
        const message = 'No workflow loaded';
        setError(message);
        onError?.(message);
        return null;
      }

      // Validate first
      const errors = validateWorkflow(nodes, edges);
      setValidationErrors(errors);
      if (errors.length > 0) {
        const message = `Validation failed: ${errors[0].message}`;
        setError(message);
        onError?.(message);
        return null;
      }

      setIsSaving(true);
      setError(null);

      try {
        // Update workflow metadata
        await workflowsApi.update(workflow.id, {
          name: metadata.name,
          description: metadata.description,
          category: metadata.category,
          tags: metadata.tags,
        });

        // Get existing steps
        const existingSteps = await workflowsApi.listSteps(workflow.id);
        const existingStepIds = new Set(existingSteps.map((s) => s.id));

        // Serialize canvas to steps, keeping each step correlated with the
        // canvas node (and existing stepId) it was serialized from.
        const serializedSteps = serializeWorkflowSteps(nodes, edges);

        // Delete removed steps
        for (const existingStep of existingSteps) {
          const stillExists = nodes.some(
            (n) => (n.data as { stepId?: string }).stepId === existingStep.id
          );
          if (!stillExists) {
            await workflowsApi.deleteStep(workflow.id, existingStep.id);
          }
        }

        // Create or update steps — each serialized step is matched against
        // its own originating node's stepId.
        const stepIdAssignments: StepIdAssignment[] = [];
        for (const { nodeId, stepId, step } of serializedSteps) {
          if (stepId && existingStepIds.has(stepId)) {
            // Update the step belonging to this node
            await workflowsApi.updateStep(workflow.id, stepId, step);
          } else {
            // Create new step and remember the server-assigned id so the
            // caller can write it back onto the node.
            const created = await workflowsApi.createStep(workflow.id, step);
            if (created?.id) {
              stepIdAssignments.push({ nodeId, stepId: created.id });
            }
          }
        }
        if (stepIdAssignments.length > 0) {
          onStepIdsAssigned?.(stepIdAssignments);
        }

        // Reorder steps
        const updatedSteps = await workflowsApi.listSteps(workflow.id);
        const stepOrder = updatedSteps
          .sort((a, b) => a.step_number - b.step_number)
          .map((s) => s.id);
        await workflowsApi.reorderSteps(workflow.id, stepOrder);

        // Handle triggers - sync EventTriggerNodes with backend
        const existingTriggers = await workflowsApi.listTriggers(workflow.id).catch(() => []);
        const existingTriggerIds = new Set(existingTriggers.map((t) => t.id));

        // Get all trigger nodes from canvas
        const triggerNodes = nodes.filter((n) => n.type === 'eventTriggerNode');

        // Delete removed triggers
        for (const existingTrigger of existingTriggers) {
          const stillExists = triggerNodes.some(
            (n) => (n.data as EventTriggerNodeData).triggerId === existingTrigger.id
          );
          if (!stillExists) {
            await triggersApi.delete(existingTrigger.id);
          }
        }

        // Create or update triggers
        for (const triggerNode of triggerNodes) {
          const data = triggerNode.data as EventTriggerNodeData;

          if (data.triggerId && existingTriggerIds.has(data.triggerId)) {
            // Update existing trigger
            await triggersApi.update(data.triggerId, {
              name: data.name,
              event_source: data.eventSource,
              filters: data.filters,
              workflow_inputs: data.workflowInputs || undefined,
              enabled: data.enabled,
              priority: data.priority,
            });
          } else {
            // Create new trigger
            await triggersApi.create({
              name: data.name,
              event_source: data.eventSource,
              workflow_id: workflow.id,
              filters: data.filters,
              workflow_inputs: data.workflowInputs || undefined,
              enabled: data.enabled,
              priority: data.priority,
            });
          }
        }

        onSuccess?.('Workflow saved successfully');
        return workflow.id;
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to save workflow';
        setError(message);
        onError?.(message);
        return null;
      } finally {
        setIsSaving(false);
      }
    },
    [workflow, onSuccess, onError, onStepIdsAssigned]
  );

  /**
   * Create a new workflow
   */
  const createWorkflow = useCallback(
    async (
      nodes: Node[],
      edges: Edge[],
      metadata: Partial<WorkflowMetadata>
    ): Promise<string | null> => {
      // Validate first
      const errors = validateWorkflow(nodes, edges);
      setValidationErrors(errors);
      if (errors.length > 0) {
        const message = `Validation failed: ${errors[0].message}`;
        setError(message);
        onError?.(message);
        return null;
      }

      setIsSaving(true);
      setError(null);

      try {
        // Create workflow
        const newWorkflow = await workflowsApi.create({
          name: metadata.name || 'New Workflow',
          description: metadata.description,
          category: metadata.category,
          tags: metadata.tags,
          input_schema: metadata.input_schema,
          output_schema: metadata.output_schema,
        });

        setWorkflow(newWorkflow as WorkflowMetadata);

        // Serialize and create steps, writing server-assigned ids back to
        // the caller so later saves update instead of delete+recreate.
        const serializedSteps = serializeWorkflowSteps(nodes, edges);
        const stepIdAssignments: StepIdAssignment[] = [];
        for (const { nodeId, step } of serializedSteps) {
          const created = await workflowsApi.createStep(newWorkflow.id, step);
          if (created?.id) {
            stepIdAssignments.push({ nodeId, stepId: created.id });
          }
        }
        if (stepIdAssignments.length > 0) {
          onStepIdsAssigned?.(stepIdAssignments);
        }

        // Create triggers from EventTriggerNodes
        const triggerNodes = nodes.filter((n) => n.type === 'eventTriggerNode');
        for (const triggerNode of triggerNodes) {
          const data = triggerNode.data as EventTriggerNodeData;
          await triggersApi.create({
            name: data.name,
            event_source: data.eventSource,
            workflow_id: newWorkflow.id,
            filters: data.filters,
            workflow_inputs: data.workflowInputs || undefined,
            enabled: data.enabled,
            priority: data.priority,
          });
        }

        onSuccess?.('Workflow created successfully');
        return newWorkflow.id;
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to create workflow';
        setError(message);
        onError?.(message);
        return null;
      } finally {
        setIsSaving(false);
      }
    },
    [onSuccess, onError, onStepIdsAssigned]
  );

  /**
   * Validate workflow without saving
   */
  const validate = useCallback((nodes: Node[], edges: Edge[]): ValidationError[] => {
    const errors = validateWorkflow(nodes, edges);
    setValidationErrors(errors);
    return errors;
  }, []);

  return {
    workflow,
    isLoading,
    isSaving,
    error,
    validationErrors,
    loadWorkflow,
    saveWorkflow,
    createWorkflow,
    validate,
  };
}
