// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useTestExecution: Custom hook for workflow test execution state management.
 *
 * Encapsulates the execution lifecycle (start, poll, cancel), input handling
 * (form vs JSON mode), and step status propagation.
 *
 * Server state is owned by TanStack Query: starting an execution is a
 * mutation, polling is `useWorkflowExecution(...)` with a data-driven
 * `refetchInterval` (refetches while the execution is `running`/`pending`,
 * then settles to `false` at a terminal status), and cancelling is a
 * mutation. The form/JSON input state, validation, modal-open gating, and the
 * step-status-to-canvas propagation remain local React state.
 */

import { useState, useEffect, useMemo, useCallback } from 'react';
import type { WorkflowExecutionDetail } from '../../../services/api/workflows';
import { workflowsApi } from '../../../services/api/workflows';
import { useWorkflowExecution } from '../../../services/api/useWorkflowExecutions';
import { logger } from '../../../utils/logger';
import { POLLING_INTERVALS } from '../../../constants/config';

const TERMINAL_STATUSES = ['completed', 'failed', 'cancelled'];

interface UseTestExecutionOptions {
  /** Whether the parent modal is open. */
  open: boolean;
  /** The workflow ID to execute. */
  workflowId: string | null;
  /** The input schema for the workflow (used to determine form availability). */
  inputSchema?: Record<string, unknown>;
  /** Callback to propagate step status changes to the canvas. */
  onStepStatusChange?: (stepStatuses: Record<string, string>) => void;
}

interface UseTestExecutionResult {
  /** Current form field values (for form mode). */
  formValues: Record<string, unknown>;
  /** Update form field values. */
  setFormValues: React.Dispatch<React.SetStateAction<Record<string, unknown>>>;
  /** Raw JSON string (for JSON editor mode). */
  inputsJson: string;
  /** Update raw JSON string. */
  setInputsJson: React.Dispatch<React.SetStateAction<string>>;
  /** Whether the JSON editor is shown instead of the form. */
  showJsonEditor: boolean;
  /** Input validation error message. */
  inputError: string | null;
  /** Clear the input error. */
  clearInputError: () => void;
  /** Whether the schema is valid for rendering a dynamic form. */
  hasValidSchema: boolean;
  /** Toggle between form and JSON editor modes. */
  handleToggleJsonEditor: () => void;
  /** Whether an execution is currently running. */
  isExecuting: boolean;
  /** The current execution detail (null before first execution). */
  execution: WorkflowExecutionDetail | null;
  /** General error message. */
  error: string | null;
  /** Clear the general error. */
  clearError: () => void;
  /** Start a new execution with current inputs. */
  handleExecute: () => Promise<void>;
  /** Cancel the currently running execution. */
  handleCancel: () => Promise<void>;
  /** Currently active tab index. */
  activeTab: number;
  /** Set the active tab index. */
  setActiveTab: React.Dispatch<React.SetStateAction<number>>;
  /** Map of step IDs to their output visibility state. */
  showOutputs: Record<string, boolean>;
  /** Toggle output visibility for a specific step. */
  toggleOutput: (stepId: string) => void;
}

/**
 * Manages the full lifecycle of a workflow test execution including
 * input mode switching, execution polling, cancellation, and cleanup.
 */
export function useTestExecution({
  open,
  workflowId,
  inputSchema,
  onStepStatusChange,
}: UseTestExecutionOptions): UseTestExecutionResult {
  const [formValues, setFormValues] = useState<Record<string, unknown>>({});
  const [inputsJson, setInputsJson] = useState('{}');
  const [showJsonEditor, setShowJsonEditor] = useState(false);
  const [inputError, setInputError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showOutputs, setShowOutputs] = useState<Record<string, boolean>>({});
  const [activeTab, setActiveTab] = useState(0);

  // The id of the execution being polled, or null when none is in flight.
  const [executionId, setExecutionId] = useState<string | null>(null);
  // Cleared after a successful cancel so we stop treating the run as active
  // even before the poll observes the terminal status.
  const [cancelled, setCancelled] = useState(false);

  // -- Poll query (server state) -------------------------------------------
  // Refetches while the execution is running/pending; stops at terminal.
  const {
    data: executionData,
    isError: isPollError,
    error: pollError,
  } = useWorkflowExecution(workflowId, executionId, {
    pollInterval: POLLING_INTERVALS.TEST_EXECUTION,
  });
  const execution = executionData ?? null;

  // -- Mutations -----------------------------------------------------------
  const [isStarting, setIsStarting] = useState(false);

  // Whether an execution is currently in flight: starting it, or polling a
  // non-terminal execution that hasn't been cancelled.
  const isExecuting =
    isStarting ||
    (executionId !== null &&
      !cancelled &&
      (execution === null || !TERMINAL_STATUSES.includes(execution.status)));

  // Check if we have a valid schema to render a form
  const hasValidSchema = useMemo((): boolean => {
    if (!inputSchema) return false;
    const props = (inputSchema as Record<string, unknown>).properties;
    if (!props || typeof props !== 'object') return false;
    return Object.keys(props).length > 0;
  }, [inputSchema]);

  // Reset state when modal opens. Intentional setState-in-effect: clearing
  // stale execution state from a previous run is a side effect of the
  // `open` prop flipping true.
  useEffect(() => {
    if (open) {
      setExecutionId(null);
      setCancelled(false);
      setError(null);
      setIsStarting(false);
      setShowOutputs({});
      setFormValues({});
      setInputsJson('{}');
      setInputError(null);
      setActiveTab(0);
      // Default to form view if we have a valid schema
      setShowJsonEditor(!hasValidSchema);
    }
  }, [open, hasValidSchema]);

  // Propagate step statuses to the canvas whenever the polled execution
  // reports step executions.
  useEffect(() => {
    if (!onStepStatusChange || !execution?.step_executions) return;
    const statuses: Record<string, string> = {};
    execution.step_executions.forEach((step) => {
      statuses[step.step_id] = step.status;
    });
    onStepStatusChange(statuses);
  }, [execution, onStepStatusChange]);

  // Log polling failures without tearing the run down (matches the prior
  // "log and keep polling" behaviour).
  useEffect(() => {
    if (isPollError) {
      logger.error('Polling error:', pollError);
    }
  }, [isPollError, pollError]);

  // Toggle between form and JSON editor
  const handleToggleJsonEditor = useCallback(() => {
    if (showJsonEditor) {
      // Switching FROM JSON to form - parse JSON into form values
      try {
        const parsed = JSON.parse(inputsJson);
        setFormValues(parsed);
        setInputError(null);
      } catch {
        setInputError('Invalid JSON - cannot switch to form view');
        return;
      }
    } else {
      // Switching FROM form to JSON - convert form values to JSON
      setInputsJson(JSON.stringify(formValues, null, 2));
    }
    setShowJsonEditor(!showJsonEditor);
  }, [showJsonEditor, inputsJson, formValues]);

  // Get inputs for execution (from form or JSON based on mode)
  const getInputs = useCallback((): Record<string, unknown> | null => {
    if (showJsonEditor) {
      try {
        const parsed = JSON.parse(inputsJson);
        setInputError(null);
        return parsed;
      } catch {
        setInputError('Invalid JSON format');
        return null;
      }
    } else {
      // Form mode - values are already structured
      setInputError(null);
      return formValues;
    }
  }, [showJsonEditor, inputsJson, formValues]);

  // Start execution
  const handleExecute = useCallback(async () => {
    if (!workflowId) {
      setError('Workflow must be saved before testing');
      return;
    }

    const parsedInputs = getInputs();
    if (parsedInputs === null) return;

    setError(null);
    setExecutionId(null);
    setCancelled(false);
    setIsStarting(true);

    try {
      const result = await workflowsApi.execute(workflowId, parsedInputs);
      // Hand off to the poll query, which refetches to completion.
      setExecutionId(result.execution_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start execution');
    } finally {
      setIsStarting(false);
    }
  }, [workflowId, getInputs]);

  // Cancel execution
  const handleCancel = useCallback(async () => {
    if (!workflowId || !execution) return;

    try {
      await workflowsApi.cancelExecution(workflowId, execution.id);
      // Stop treating the run as active immediately; the poll has already
      // settled to `false` once the server reports the terminal status.
      setCancelled(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel execution');
    }
  }, [workflowId, execution]);

  // Toggle output visibility for a step
  const toggleOutput = useCallback((stepId: string) => {
    setShowOutputs((prev) => ({ ...prev, [stepId]: !prev[stepId] }));
  }, []);

  const clearInputError = useCallback(() => setInputError(null), []);
  const clearError = useCallback(() => setError(null), []);

  return {
    formValues,
    setFormValues,
    inputsJson,
    setInputsJson,
    showJsonEditor,
    inputError,
    clearInputError,
    hasValidSchema,
    handleToggleJsonEditor,
    isExecuting,
    execution,
    error,
    clearError,
    handleExecute,
    handleCancel,
    activeTab,
    setActiveTab,
    showOutputs,
    toggleOutput,
  };
}
