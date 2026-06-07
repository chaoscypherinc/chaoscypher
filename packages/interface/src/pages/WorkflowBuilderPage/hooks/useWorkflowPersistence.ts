// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Workflow Persistence Hook
 *
 * Manages loading, saving, and settings updates for workflows. Handles
 * both new workflow creation and existing workflow editing, including
 * navigation, dirty tracking, success/error feedback, and the unsaved-
 * changes confirmation flow.
 *
 * Server state is owned by TanStack Query: the workflow metadata load is a
 * `useWorkflow(workflowId)` query (gated by `enabled` so a brand-new, unsaved
 * workflow with no id never fetches), and the settings create/update is a
 * mutation that invalidates the workflow detail + list on success. The
 * ReactFlow canvas hydration (deserializing the loaded workflow's
 * nodes/edges) and the canvas->API save still flow through
 * `useWorkflowSerialization`, which remains local/unchanged.
 */

import { useState, useCallback, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router';
import { useReactFlow, type Node, type Edge } from '@xyflow/react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { workflowsApi } from '../../../services/api/workflows';
import { useWorkflow } from '../../../services/api/useWorkflows';
import { useWorkflowSerialization } from './useWorkflowSerialization';
import { useStepTemplates } from './useStepTemplates';
import { logger } from '../../../utils/logger';
import type {
  WorkflowStepNodeData,
  UnifiedEntryNodeData,
  WorkflowMetadata,
  StepTemplate,
} from '../types';

// Module-local query keys mirroring `useWorkflows.ts` so mutation
// invalidations land on the same caches the load query reads from.
const WORKFLOWS_QUERY_KEY = ['workflows'] as const;

function workflowQueryKey(workflowId: string) {
  return ['workflows', workflowId] as const;
}

// ---------------------------------------------------------------------------
// Return type
// ---------------------------------------------------------------------------

interface UseWorkflowPersistenceReturn {
  /** Loaded/created workflow metadata, or null for new unsaved workflows. */
  workflow: WorkflowMetadata | null;
  /** Whether the canvas has unsaved changes. */
  isDirty: boolean;
  /** Mark the canvas as having unsaved changes. */
  markDirty: () => void;
  /** Clear the dirty flag (e.g. after a successful save). */
  clearDirty: () => void;

  // Loading / saving / feedback
  isLoading: boolean;
  isSaving: boolean;
  error: string | null;
  setError: (error: string | null) => void;
  successMessage: string | null;
  setSuccessMessage: (message: string | null) => void;

  // Modal toggles
  isTestModalOpen: boolean;
  setIsTestModalOpen: (open: boolean) => void;
  isSettingsModalOpen: boolean;
  setIsSettingsModalOpen: (open: boolean) => void;
  isTemplatesPanelOpen: boolean;
  setIsTemplatesPanelOpen: (open: boolean) => void;

  // Unsaved-changes confirmation
  confirmLeaveOpen: boolean;
  setConfirmLeaveOpen: (open: boolean) => void;
  handleConfirmLeave: () => void;

  // Actions
  handleBack: () => void;
  handleSave: () => void;
  handleTestExecution: () => void;
  handleAutoLayout: () => void;
  handleSettingsSave: (settings: Partial<WorkflowMetadata>) => Promise<void>;
  handleApplyTemplate: (template: StepTemplate, pushUndo: () => void) => void;
  handleSaveAsTemplate: (name: string, nodeData: WorkflowStepNodeData) => void;

  /** Call in an effect to initialise a new workflow entry node. */
  initEntryNode: () => void;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Hook encapsulating workflow lifecycle operations.
 *
 * @param nodes - Current node array from `useNodesState`
 * @param edges - Current edge array from `useEdgesState`
 * @param setNodes - Setter from `useNodesState`
 * @param setEdges - Setter from `useEdgesState`
 */
export function useWorkflowPersistence(
  nodes: Node[],
  edges: Edge[],
  setNodes: React.Dispatch<React.SetStateAction<Node[]>>,
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>,
): UseWorkflowPersistenceReturn {
  const navigate = useNavigate();
  const { workflowId } = useParams<{ workflowId?: string }>();
  const { fitView } = useReactFlow();
  const queryClient = useQueryClient();

  // -- Workflow metadata ----------------------------------------------------
  // The committed metadata for the workflow being edited. Hydrated from the
  // load query for existing workflows; set directly by the create/update
  // mutation for new or freshly-saved ones.
  const [workflow, setWorkflow] = useState<WorkflowMetadata | null>(null);
  const [isDirty, setIsDirty] = useState(false);

  // -- Modal state ----------------------------------------------------------
  const [isTestModalOpen, setIsTestModalOpen] = useState(false);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
  const [isTemplatesPanelOpen, setIsTemplatesPanelOpen] = useState(false);

  // -- Feedback -------------------------------------------------------------
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // -- Unsaved-changes confirmation -----------------------------------------
  const [confirmLeaveOpen, setConfirmLeaveOpen] = useState(false);

  // -- Serialization (kept local: ReactFlow <-> API mapping) ---------------
  const { saveWorkflow: serializeSaveWorkflow, loadWorkflow } = useWorkflowSerialization();

  const { saveTemplate } = useStepTemplates();

  // -- Dirty helpers --------------------------------------------------------
  const markDirty = useCallback(() => setIsDirty(true), []);
  const clearDirty = useCallback(() => setIsDirty(false), []);

  // =========================================================================
  // Load query (server state) — metadata for the workflow being edited.
  // Gated on `workflowId` so a new/unsaved workflow never fetches.
  // =========================================================================
  const {
    data: loadedWorkflow,
    isLoading: isWorkflowLoading,
    isError: isWorkflowError,
    error: workflowError,
  } = useWorkflow(workflowId ?? null);

  // The canvas hydration is still owned by the serialization hook (it fetches
  // steps + triggers and deserializes them into nodes/edges). Re-running it
  // when the workflow id changes preserves the previous wiring exactly.
  useEffect(() => {
    if (!workflowId) return;

    let cancelled = false;
    loadWorkflow(workflowId)
      .then((result) => {
        if (cancelled || !result) return;
        setNodes(result.nodes);
        setEdges(result.edges);
      })
      .catch((err) => {
        logger.error('Failed to load workflow:', err);
        setError('Failed to load workflow');
      });

    return () => {
      cancelled = true;
    };
  }, [workflowId, loadWorkflow, setNodes, setEdges]);

  // Sync the loaded metadata from the query into local workflow state.
  useEffect(() => {
    if (loadedWorkflow) {
      setWorkflow(loadedWorkflow as WorkflowMetadata);
    }
  }, [loadedWorkflow]);

  // Surface a load failure from the metadata query.
  useEffect(() => {
    if (isWorkflowError) {
      logger.error('Failed to load workflow:', workflowError);
      setError('Failed to load workflow');
    }
  }, [isWorkflowError, workflowError]);

  // A load is in flight while either the metadata query or the canvas
  // deserialization is still resolving for an existing workflow.
  const isLoading = !!workflowId && isWorkflowLoading;

  // Initialise new workflow with UnifiedEntryNode
  const initEntryNode = useCallback(() => {
    if (!workflowId && nodes.length === 0 && !isLoading) {
      const entryNode: Node<UnifiedEntryNodeData> = {
        id: 'entry',
        type: 'unifiedEntryNode',
        position: { x: 400, y: 50 },
        data: {
          label: 'Start',
          workflowInputs: [],
          eventSource: 'manual',
          eventFields: [],
          outputPorts: [],
        },
      };
      setNodes([entryNode]);
    }
  }, [workflowId, nodes.length, isLoading, setNodes]);

  useEffect(() => {
    initEntryNode();
  }, [initEntryNode]);

  // =========================================================================
  // Settings create/update mutation (server state)
  // =========================================================================
  const settingsMutation = useMutation<
    WorkflowMetadata,
    Error,
    Partial<WorkflowMetadata>
  >({
    mutationFn: async (settings) => {
      if (workflow) {
        const updated = await workflowsApi.update(workflow.id, settings);
        return updated as WorkflowMetadata;
      }
      const created = await workflowsApi.create(
        settings as Parameters<typeof workflowsApi.create>[0],
      );
      return created as WorkflowMetadata;
    },
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: workflowQueryKey(saved.id) });
      void queryClient.invalidateQueries({ queryKey: WORKFLOWS_QUERY_KEY });
    },
  });

  // Saving covers both the canvas->API serialization save and a settings
  // create/update mutation in flight.
  const [isSerializing, setIsSerializing] = useState(false);
  const isSaving = isSerializing || settingsMutation.isPending;

  // =========================================================================
  // Persistence actions
  // =========================================================================

  const handleBack = useCallback(() => {
    if (isDirty) {
      setConfirmLeaveOpen(true);
    } else {
      navigate('/automations');
    }
  }, [navigate, isDirty]);

  const handleConfirmLeave = useCallback(() => {
    setConfirmLeaveOpen(false);
    navigate('/automations');
  }, [navigate]);

  const handleSave = useCallback(async () => {
    if (!workflow) {
      setIsSettingsModalOpen(true);
      return;
    }
    setIsSerializing(true);
    try {
      const result = await serializeSaveWorkflow(nodes, edges, {
        name: workflow.name,
        description: workflow.description,
        category: workflow.category,
        tags: workflow.tags,
      });
      if (result) {
        setSuccessMessage('Workflow saved successfully');
        setIsDirty(false);
        // Re-sync caches so the next read reflects the persisted steps/meta.
        void queryClient.invalidateQueries({ queryKey: workflowQueryKey(workflow.id) });
        void queryClient.invalidateQueries({ queryKey: WORKFLOWS_QUERY_KEY });
      }
    } catch (err) {
      logger.error('Failed to save workflow:', err);
      setError('Failed to save workflow');
    } finally {
      setIsSerializing(false);
    }
  }, [workflow, nodes, edges, serializeSaveWorkflow, queryClient]);

  const handleTestExecution = useCallback(() => {
    if (!workflow) {
      setError('Please save the workflow before testing');
      return;
    }
    setIsTestModalOpen(true);
  }, [workflow]);

  const handleAutoLayout = useCallback(() => {
    fitView({ padding: 0.2, duration: 400 });
  }, [fitView]);

  const handleSettingsSave = useCallback(
    async (settings: Partial<WorkflowMetadata>) => {
      try {
        const saved = await settingsMutation.mutateAsync(settings);
        if (workflow) {
          setWorkflow(saved);
          setSuccessMessage('Workflow settings updated');
        } else {
          setWorkflow(saved);
          navigate(`/automations/builder/${saved.id}`, { replace: true });
          setSuccessMessage('Workflow created');
        }
      } catch (err) {
        logger.error('Failed to save workflow settings:', err);
        setError('Failed to save workflow settings');
      }
    },
    [workflow, navigate, settingsMutation],
  );

  // =========================================================================
  // Templates
  // =========================================================================

  const handleApplyTemplate = useCallback(
    (template: StepTemplate, pushUndo: () => void) => {
      pushUndo();
      const newNode: Node<WorkflowStepNodeData> = {
        id: `step-${Date.now()}`,
        type: 'stepNode',
        position: { x: 200, y: 100 + nodes.length * 150 },
        data: {
          name: template.name,
          description: template.description || '',
          toolType: template.toolType,
          toolId: template.toolId,
          toolName: template.name,
          toolCategory: template.category,
          configuration: template.configuration,
          continueOnError: false,
        },
      };
      setNodes((nds) => [...nds, newNode]);
      setIsDirty(true);
      setIsTemplatesPanelOpen(false);
      setSuccessMessage('Template applied');
    },
    [nodes, setNodes],
  );

  const handleSaveAsTemplate = useCallback(
    (name: string, nodeData: WorkflowStepNodeData) => {
      saveTemplate(name, nodeData);
      setSuccessMessage('Step saved as template');
    },
    [saveTemplate],
  );

  return {
    workflow,
    isDirty,
    markDirty,
    clearDirty,

    isLoading,
    isSaving,
    error,
    setError,
    successMessage,
    setSuccessMessage,

    isTestModalOpen,
    setIsTestModalOpen,
    isSettingsModalOpen,
    setIsSettingsModalOpen,
    isTemplatesPanelOpen,
    setIsTemplatesPanelOpen,

    confirmLeaveOpen,
    setConfirmLeaveOpen,
    handleConfirmLeave,

    handleBack,
    handleSave,
    handleTestExecution,
    handleAutoLayout,
    handleSettingsSave,
    handleApplyTemplate,
    handleSaveAsTemplate,

    initEntryNode,
  };
}
