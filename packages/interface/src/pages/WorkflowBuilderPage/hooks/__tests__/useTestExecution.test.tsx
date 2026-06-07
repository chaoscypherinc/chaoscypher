// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useTestExecution — workflow test execution hook.
 *
 * After the TanStack Query migration the hook owns server state through
 * `workflowsApi` (execute/cancel mutations) and `useWorkflowExecution`
 * (data-driven polling). Tests therefore mock at the apiClient layer and
 * wrap `renderHook` in `makeWrapper` so the real query client runs. Local
 * state (form/JSON input, validation, modal reset, output toggles) is
 * exercised directly.
 *
 * Strategy:
 * - vi.mock the apiClient; drive responses per endpoint via mockImplementation.
 * - Mock logger to observe error calls.
 * - Mock POLLING_INTERVALS to a known value.
 * - renderHook + act + waitFor from @testing-library/react, with makeWrapper.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { apiClient } from '../../../../services/api/client';
import { logger } from '../../../../utils/logger';
import { useTestExecution } from '../useTestExecution';
import type {
  WorkflowExecutionDetail,
  WorkflowStepExecution,
} from '../../../../services/api/workflows';

vi.mock('../../../../services/api/client', () => installApiClientMock());

vi.mock('../../../../utils/logger', () => ({
  logger: {
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    debug: vi.fn(),
  },
}));

vi.mock('../../../../constants/config', () => ({
  POLLING_INTERVALS: {
    TEST_EXECUTION: 1_000,
  },
}));

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeExecution(
  overrides?: Partial<WorkflowExecutionDetail>,
): WorkflowExecutionDetail {
  return {
    id: 'exec-1',
    workflow_id: 'wf-1',
    triggered_by: 'manual',
    inputs: {},
    status: 'running',
    step_executions: [],
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeStepExecution(
  overrides?: Partial<WorkflowStepExecution>,
): WorkflowStepExecution {
  return {
    id: 'step-exec-1',
    execution_id: 'exec-1',
    step_id: 'step-a',
    inputs: {},
    status: 'completed',
    retry_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

/**
 * Configure the mock apiClient for the workflows execution endpoints.
 * - POST /workflows/:id/executions            → start (returns execution_id)
 * - GET  /workflows/:id/executions/:execId     → poll (returns detail)
 * - POST /workflows/:id/executions/:execId/cancel → cancel
 */
function mockExecutionEndpoints(opts: {
  executionId?: string;
  detail?: WorkflowExecutionDetail | (() => WorkflowExecutionDetail);
  startError?: boolean;
  startThrows?: unknown;
  pollError?: boolean;
  cancelError?: unknown;
} = {}) {
  const {
    executionId = 'exec-1',
    detail = makeExecution({ status: 'completed' }),
    startError = false,
    startThrows,
    pollError = false,
    cancelError,
  } = opts;

  mockedApiClient.post.mockImplementation((url: string) => {
    if (url.endsWith('/cancel')) {
      if (cancelError !== undefined) return Promise.reject(cancelError);
      return Promise.resolve({ data: {} });
    }
    // start execution
    if (startThrows !== undefined) return Promise.reject(startThrows);
    if (startError) return Promise.reject(new Error('Network error'));
    return Promise.resolve({
      data: { execution_id: executionId, status: 'running', message: 'started' },
    });
  });

  mockedApiClient.get.mockImplementation(() => {
    if (pollError) return Promise.reject(new Error('poll fail'));
    const resolved = typeof detail === 'function' ? detail() : detail;
    return Promise.resolve({ data: resolved });
  });
}

function renderTestExecution(
  props: Parameters<typeof useTestExecution>[0],
) {
  return renderHook((p: Parameters<typeof useTestExecution>[0]) => useTestExecution(p), {
    initialProps: props,
    wrapper: makeWrapper(),
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Suite: initial state
// ---------------------------------------------------------------------------

describe('useTestExecution — initial state', () => {
  it('starts with isExecuting=false, execution/error/inputError null, empty inputs', () => {
    const { result } = renderTestExecution({ open: false, workflowId: 'wf-1' });
    expect(result.current.isExecuting).toBe(false);
    expect(result.current.execution).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.inputError).toBeNull();
    expect(result.current.formValues).toEqual({});
    expect(result.current.inputsJson).toBe('{}');
    expect(result.current.activeTab).toBe(0);
    expect(result.current.showOutputs).toEqual({});
  });

  it('exposes all expected functions', () => {
    const { result } = renderTestExecution({ open: false, workflowId: 'wf-1' });
    expect(typeof result.current.handleExecute).toBe('function');
    expect(typeof result.current.handleCancel).toBe('function');
    expect(typeof result.current.handleToggleJsonEditor).toBe('function');
    expect(typeof result.current.clearError).toBe('function');
    expect(typeof result.current.clearInputError).toBe('function');
    expect(typeof result.current.toggleOutput).toBe('function');
  });
});

// ---------------------------------------------------------------------------
// Suite: hasValidSchema
// ---------------------------------------------------------------------------

describe('useTestExecution — hasValidSchema', () => {
  it('is false when inputSchema is undefined', () => {
    const { result } = renderTestExecution({ open: false, workflowId: 'wf-1' });
    expect(result.current.hasValidSchema).toBe(false);
  });

  it('is false when inputSchema has no properties', () => {
    const { result } = renderTestExecution({ open: false, workflowId: 'wf-1', inputSchema: {} });
    expect(result.current.hasValidSchema).toBe(false);
  });

  it('is false when properties is empty object', () => {
    const { result } = renderTestExecution({
      open: false,
      workflowId: 'wf-1',
      inputSchema: { properties: {} },
    });
    expect(result.current.hasValidSchema).toBe(false);
  });

  it('is true when inputSchema has at least one property', () => {
    const { result } = renderTestExecution({
      open: false,
      workflowId: 'wf-1',
      inputSchema: { properties: { name: { type: 'string' } } },
    });
    expect(result.current.hasValidSchema).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Suite: open effect — state reset
// ---------------------------------------------------------------------------

describe('useTestExecution — open effect resets state', () => {
  it('resets error to null when modal reopens', async () => {
    const { result, rerender } = renderTestExecution({ open: true, workflowId: null });

    await act(async () => {
      await result.current.handleExecute();
    });
    expect(result.current.error).toBe('Workflow must be saved before testing');

    rerender({ open: false, workflowId: null });
    rerender({ open: true, workflowId: null });

    expect(result.current.error).toBeNull();
  });

  it('sets showJsonEditor=false when schema is valid on open', () => {
    const { result } = renderTestExecution({
      open: true,
      workflowId: 'wf-1',
      inputSchema: { properties: { name: { type: 'string' } } },
    });
    expect(result.current.showJsonEditor).toBe(false);
  });

  it('sets showJsonEditor=true when no valid schema on open', () => {
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });
    expect(result.current.showJsonEditor).toBe(true);
  });

  it('resets formValues to {} when modal reopens', () => {
    const { result, rerender } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    act(() => {
      result.current.setFormValues({ name: 'Alice' });
    });
    expect(result.current.formValues).toEqual({ name: 'Alice' });

    rerender({ open: false, workflowId: 'wf-1' });
    rerender({ open: true, workflowId: 'wf-1' });

    expect(result.current.formValues).toEqual({});
  });

  it('resets activeTab to 0 when modal reopens', () => {
    const { result, rerender } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    act(() => {
      result.current.setActiveTab(2);
    });
    expect(result.current.activeTab).toBe(2);

    rerender({ open: false, workflowId: 'wf-1' });
    rerender({ open: true, workflowId: 'wf-1' });

    expect(result.current.activeTab).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Suite: handleExecute — start execution
// ---------------------------------------------------------------------------

describe('useTestExecution — handleExecute', () => {
  it('sets error if workflowId is null and does not POST', async () => {
    mockExecutionEndpoints();
    const { result } = renderTestExecution({ open: true, workflowId: null });

    await act(async () => {
      await result.current.handleExecute();
    });

    expect(result.current.error).toBe('Workflow must be saved before testing');
    expect(result.current.isExecuting).toBe(false);
    expect(mockedApiClient.post).not.toHaveBeenCalled();
  });

  it('POSTs the execution with workflowId and form inputs', async () => {
    mockExecutionEndpoints({ detail: makeExecution({ status: 'completed' }) });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-42' });

    await act(async () => {
      await result.current.handleExecute();
    });

    expect(mockedApiClient.post).toHaveBeenCalledWith('/workflows/wf-42/executions', {
      inputs: {},
    });
  });

  it('resets isExecuting and sets error on execute failure', async () => {
    mockExecutionEndpoints({ startError: true });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    await act(async () => {
      await result.current.handleExecute();
    });

    expect(result.current.isExecuting).toBe(false);
    expect(result.current.error).toBe('Network error');
  });

  it('sets generic error message when thrown value is not an Error', async () => {
    mockExecutionEndpoints({ startThrows: 'plain string error' });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    await act(async () => {
      await result.current.handleExecute();
    });

    expect(result.current.error).toBe('Failed to start execution');
  });

  it('does not POST when JSON is invalid in JSON mode', async () => {
    mockExecutionEndpoints();
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    // No schema → starts in JSON mode; set invalid JSON
    act(() => {
      result.current.setInputsJson('{ invalid }');
    });

    await act(async () => {
      await result.current.handleExecute();
    });

    expect(mockedApiClient.post).not.toHaveBeenCalled();
    expect(result.current.inputError).toBe('Invalid JSON format');
  });

  it('POSTs JSON inputs in JSON editor mode', async () => {
    mockExecutionEndpoints({ detail: makeExecution({ status: 'completed' }) });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    act(() => {
      result.current.setInputsJson(JSON.stringify({ key: 'val' }));
    });

    await act(async () => {
      await result.current.handleExecute();
    });

    expect(mockedApiClient.post).toHaveBeenCalledWith('/workflows/wf-1/executions', {
      inputs: { key: 'val' },
    });
  });
});

// ---------------------------------------------------------------------------
// Suite: polling behavior
// ---------------------------------------------------------------------------

describe('useTestExecution — polling', () => {
  it('polls the started execution and surfaces its detail', async () => {
    const detail = makeExecution({ id: 'exec-99', status: 'completed' });
    mockExecutionEndpoints({ executionId: 'exec-99', detail });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    await act(async () => {
      await result.current.handleExecute();
    });

    await waitFor(() => expect(result.current.execution).not.toBeNull());
    expect(mockedApiClient.get).toHaveBeenCalledWith(
      '/workflows/wf-1/executions/exec-99',
    );
    expect(result.current.execution?.id).toBe('exec-99');
  });

  it('sets isExecuting=false once the execution reaches a terminal status', async () => {
    mockExecutionEndpoints({ detail: makeExecution({ status: 'completed' }) });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    await act(async () => {
      await result.current.handleExecute();
    });

    await waitFor(() => expect(result.current.execution?.status).toBe('completed'));
    expect(result.current.isExecuting).toBe(false);
  });

  it('treats "failed" and "cancelled" as terminal (isExecuting=false)', async () => {
    mockExecutionEndpoints({ detail: makeExecution({ status: 'failed' }) });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    await act(async () => {
      await result.current.handleExecute();
    });

    await waitFor(() => expect(result.current.execution?.status).toBe('failed'));
    expect(result.current.isExecuting).toBe(false);
  });

  it('keeps isExecuting=true while the execution is still running', async () => {
    mockExecutionEndpoints({ detail: makeExecution({ status: 'running' }) });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    await act(async () => {
      await result.current.handleExecute();
    });

    await waitFor(() => expect(result.current.execution?.status).toBe('running'));
    expect(result.current.isExecuting).toBe(true);
  });

  it('logs an error when polling fails', async () => {
    mockExecutionEndpoints({ pollError: true });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    await act(async () => {
      await result.current.handleExecute();
    });

    await waitFor(() =>
      expect(logger.error).toHaveBeenCalledWith('Polling error:', expect.any(Error)),
    );
  });

  it('calls onStepStatusChange with step statuses from the poll', async () => {
    const onStepStatusChange = vi.fn<(statuses: Record<string, string>) => void>();
    mockExecutionEndpoints({
      detail: makeExecution({
        status: 'completed',
        step_executions: [makeStepExecution({ step_id: 'step-a', status: 'completed' })],
      }),
    });
    const { result } = renderTestExecution({
      open: true,
      workflowId: 'wf-1',
      onStepStatusChange,
    });

    await act(async () => {
      await result.current.handleExecute();
    });

    await waitFor(() =>
      expect(onStepStatusChange).toHaveBeenCalledWith({ 'step-a': 'completed' }),
    );
  });
});

// ---------------------------------------------------------------------------
// Suite: handleCancel
// ---------------------------------------------------------------------------

describe('useTestExecution — handleCancel', () => {
  it('does nothing if execution is null', async () => {
    mockExecutionEndpoints();
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    await act(async () => {
      await result.current.handleCancel();
    });

    expect(mockedApiClient.post).not.toHaveBeenCalled();
  });

  it('cancels the running execution and stops treating the run as active', async () => {
    mockExecutionEndpoints({
      executionId: 'exec-5',
      detail: makeExecution({ id: 'exec-5', status: 'running' }),
    });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    await act(async () => {
      await result.current.handleExecute();
    });
    await waitFor(() => expect(result.current.execution?.id).toBe('exec-5'));
    expect(result.current.isExecuting).toBe(true);

    await act(async () => {
      await result.current.handleCancel();
    });

    expect(mockedApiClient.post).toHaveBeenCalledWith(
      '/workflows/wf-1/executions/exec-5/cancel',
    );
    expect(result.current.isExecuting).toBe(false);
  });

  it('sets error when cancel fails', async () => {
    mockExecutionEndpoints({
      detail: makeExecution({ status: 'running' }),
      cancelError: new Error('Cancel failed'),
    });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    await act(async () => {
      await result.current.handleExecute();
    });
    await waitFor(() => expect(result.current.execution).not.toBeNull());

    await act(async () => {
      await result.current.handleCancel();
    });

    expect(result.current.error).toBe('Cancel failed');
  });

  it('sets generic error when cancel throws a non-Error', async () => {
    mockExecutionEndpoints({
      detail: makeExecution({ status: 'running' }),
      cancelError: 'oops',
    });
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    await act(async () => {
      await result.current.handleExecute();
    });
    await waitFor(() => expect(result.current.execution).not.toBeNull());

    await act(async () => {
      await result.current.handleCancel();
    });

    expect(result.current.error).toBe('Failed to cancel execution');
  });
});

// ---------------------------------------------------------------------------
// Suite: handleToggleJsonEditor
// ---------------------------------------------------------------------------

describe('useTestExecution — handleToggleJsonEditor', () => {
  it('toggles from form to JSON editor mode and serializes form values', () => {
    const { result } = renderTestExecution({
      open: true,
      workflowId: 'wf-1',
      inputSchema: { properties: { name: { type: 'string' } } },
    });

    expect(result.current.showJsonEditor).toBe(false);

    act(() => {
      result.current.setFormValues({ name: 'Alice' });
    });
    act(() => {
      result.current.handleToggleJsonEditor();
    });

    expect(result.current.showJsonEditor).toBe(true);
    expect(result.current.inputsJson).toBe(JSON.stringify({ name: 'Alice' }, null, 2));
  });

  it('toggles from JSON to form mode with valid JSON', () => {
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    expect(result.current.showJsonEditor).toBe(true);

    act(() => {
      result.current.setInputsJson(JSON.stringify({ key: 'val' }));
    });
    act(() => {
      result.current.handleToggleJsonEditor();
    });

    expect(result.current.showJsonEditor).toBe(false);
    expect(result.current.formValues).toEqual({ key: 'val' });
    expect(result.current.inputError).toBeNull();
  });

  it('sets inputError and stays in JSON mode when JSON is invalid on toggle to form', () => {
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    act(() => {
      result.current.setInputsJson('{ invalid json }');
    });
    act(() => {
      result.current.handleToggleJsonEditor();
    });

    expect(result.current.showJsonEditor).toBe(true);
    expect(result.current.inputError).toBe('Invalid JSON - cannot switch to form view');
  });
});

// ---------------------------------------------------------------------------
// Suite: clearError / clearInputError
// ---------------------------------------------------------------------------

describe('useTestExecution — clearError / clearInputError', () => {
  it('clearError sets error to null', async () => {
    mockExecutionEndpoints();
    const { result } = renderTestExecution({ open: true, workflowId: null });

    await act(async () => {
      await result.current.handleExecute();
    });
    expect(result.current.error).not.toBeNull();

    act(() => {
      result.current.clearError();
    });
    expect(result.current.error).toBeNull();
  });

  it('clearInputError sets inputError to null', async () => {
    mockExecutionEndpoints();
    const { result } = renderTestExecution({ open: true, workflowId: 'wf-1' });

    act(() => {
      result.current.setInputsJson('bad json');
    });
    await act(async () => {
      await result.current.handleExecute();
    });
    expect(result.current.inputError).toBe('Invalid JSON format');

    act(() => {
      result.current.clearInputError();
    });
    expect(result.current.inputError).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Suite: toggleOutput
// ---------------------------------------------------------------------------

describe('useTestExecution — toggleOutput', () => {
  it('toggles a step output visible then hidden', () => {
    const { result } = renderTestExecution({ open: false, workflowId: 'wf-1' });

    act(() => { result.current.toggleOutput('step-1'); });
    expect(result.current.showOutputs['step-1']).toBe(true);

    act(() => { result.current.toggleOutput('step-1'); });
    expect(result.current.showOutputs['step-1']).toBe(false);
  });

  it('toggles multiple steps independently', () => {
    const { result } = renderTestExecution({ open: false, workflowId: 'wf-1' });

    act(() => { result.current.toggleOutput('step-1'); });
    act(() => { result.current.toggleOutput('step-2'); });
    expect(result.current.showOutputs['step-1']).toBe(true);
    expect(result.current.showOutputs['step-2']).toBe(true);

    act(() => { result.current.toggleOutput('step-1'); });
    expect(result.current.showOutputs['step-1']).toBe(false);
    expect(result.current.showOutputs['step-2']).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Suite: setActiveTab
// ---------------------------------------------------------------------------

describe('useTestExecution — setActiveTab', () => {
  it('updates activeTab', () => {
    const { result } = renderTestExecution({ open: false, workflowId: 'wf-1' });
    act(() => {
      result.current.setActiveTab(2);
    });
    expect(result.current.activeTab).toBe(2);
  });
});
