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
import { serializeWorkflow, deserializeWorkflow, validateWorkflow } from '../utils/serialization';
import type { ValidationError, WorkflowMetadata, EventTriggerNodeData } from '../types';

interface UseWorkflowSerializationOptions {
  onSuccess?: (message: string) => void;
  onError?: (error: string) => void;
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
  const { onSuccess, onError } = options;

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
        setError('No workflow loaded');
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

        // Serialize canvas to steps
        const newSteps = serializeWorkflow(nodes, edges);

        // Delete removed steps
        for (const existingStep of existingSteps) {
          const stillExists = nodes.some(
            (n) => (n.data as { stepId?: string }).stepId === existingStep.id
          );
          if (!stillExists) {
            await workflowsApi.deleteStep(workflow.id, existingStep.id);
          }
        }

        // Create or update steps
        for (const step of newSteps) {
          const existingNode = nodes.find(
            (n) => (n.data as { stepId?: string }).stepId && existingStepIds.has((n.data as { stepId?: string }).stepId!)
          );

          if (existingNode && (existingNode.data as { stepId?: string }).stepId) {
            // Update existing step
            await workflowsApi.updateStep(
              workflow.id,
              (existingNode.data as { stepId?: string }).stepId!,
              step
            );
          } else {
            // Create new step
            await workflowsApi.createStep(workflow.id, step);
          }
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
    [workflow, onSuccess, onError]
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

        // Serialize and create steps
        const steps = serializeWorkflow(nodes, edges);
        for (const step of steps) {
          await workflowsApi.createStep(newWorkflow.id, step);
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
    [onSuccess, onError]
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
