// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useWorkflowPersistence — workflow builder load/save hook.
 *
 * After the TanStack Query migration the metadata LOAD is a
 * `useWorkflow(workflowId)` query (gated on the route id) and the settings
 * create/update is a mutation. The ReactFlow canvas hydration and the
 * canvas->API save still flow through `useWorkflowSerialization`, which is
 * mocked here so these tests isolate the persistence hook's query/mutation
 * wiring (load fires + hydrates the canvas, save serializes, create/update
 * routes to create vs update + navigates).
 *
 * Strategy:
 * - vi.mock the apiClient; drive `useWorkflow` / the settings mutation per URL.
 * - vi.mock useWorkflowSerialization + useStepTemplates so the canvas
 *   serialization is a controllable stub.
 * - Wrap renderHook in makeWrapper (Router + QueryClient) plus ReactFlowProvider.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, act, waitFor } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { ReactFlowProvider, type Node, type Edge } from '@xyflow/react';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { apiClient } from '../../../../services/api/client';
import { useWorkflowPersistence } from '../useWorkflowPersistence';
import type { Workflow } from '../../../../services/api/workflows';

vi.mock('../../../../services/api/client', () => installApiClientMock());

const mockNavigate = vi.fn<(to: string, opts?: { replace?: boolean }) => void>();
vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>();
  return { ...actual, useNavigate: () => mockNavigate };
});

const mockLoadWorkflow = vi.fn<(id: string) => Promise<{ nodes: Node[]; edges: Edge[] } | null>>();
const mockSaveWorkflow =
  vi.fn<(nodes: Node[], edges: Edge[], meta: Record<string, unknown>) => Promise<string | null>>();
vi.mock('../useWorkflowSerialization', () => ({
  useWorkflowSerialization: () => ({
    workflow: null,
    isLoading: false,
    isSaving: false,
    error: null,
    validationErrors: [],
    loadWorkflow: mockLoadWorkflow,
    saveWorkflow: mockSaveWorkflow,
    createWorkflow: vi.fn(),
    validate: vi.fn(),
  }),
}));

const mockSaveTemplate = vi.fn();
vi.mock('../useStepTemplates', () => ({
  useStepTemplates: () => ({ saveTemplate: mockSaveTemplate }),
}));

vi.mock('../../../../utils/logger', () => ({
  logger: { error: vi.fn(), info: vi.fn(), warn: vi.fn(), debug: vi.fn() },
}));

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

// ---------------------------------------------------------------------------
// Fixtures / helpers
// ---------------------------------------------------------------------------

function makeApiWorkflow(overrides?: Partial<Workflow>): Workflow {
  return {
    id: 'wf-1',
    database_name: 'default',
    name: 'Test Workflow',
    description: 'A test workflow',
    is_system: false,
    is_active: true,
    expose_as_ai_tool: false,
    input_schema: {},
    tags: [],
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedApiClient.get.mockResolvedValue({ data: makeApiWorkflow() });
  mockedApiClient.post.mockResolvedValue({ data: makeApiWorkflow({ id: 'wf-new' }) });
  mockedApiClient.patch.mockResolvedValue({ data: makeApiWorkflow({ name: 'Updated' }) });
  mockLoadWorkflow.mockResolvedValue({ nodes: [], edges: [] });
  mockSaveWorkflow.mockResolvedValue('wf-1');
});

// ---------------------------------------------------------------------------
// We need useParams to resolve :workflowId. makeWrapper's MemoryRouter has no
// <Routes>, so render the hook inside a matching route.
// ---------------------------------------------------------------------------

function renderWithRoute(opts: { workflowId?: string } = {}) {
  const { workflowId } = opts;
  const path = workflowId ? `/builder/${workflowId}` : '/builder';

  let nodes: Node[] = [];
  let edges: Edge[] = [];
  const setNodes = vi.fn((updater: React.SetStateAction<Node[]>) => {
    nodes = typeof updater === 'function' ? (updater as (n: Node[]) => Node[])(nodes) : updater;
  });
  const setEdges = vi.fn((updater: React.SetStateAction<Edge[]>) => {
    edges = typeof updater === 'function' ? (updater as (e: Edge[]) => Edge[])(edges) : updater;
  });

  const Wrapper = makeWrapper({ initialEntries: [path] });

  let hookValue: ReturnType<typeof useWorkflowPersistence> | undefined;
  function HookHost() {
    hookValue = useWorkflowPersistence(nodes, edges, setNodes, setEdges);
    return null;
  }

  const utils = render(
    <Wrapper>
      <ReactFlowProvider>
        <Routes>
          <Route path="/builder" element={<HookHost />} />
          <Route path="/builder/:workflowId" element={<HookHost />} />
        </Routes>
      </ReactFlowProvider>
    </Wrapper>,
  );

  return {
    ...utils,
    get current() {
      if (!hookValue) throw new Error('hook not mounted');
      return hookValue;
    },
    setNodes,
    setEdges,
    getNodes: () => nodes,
  };
}

// ---------------------------------------------------------------------------
// Suite: load
// ---------------------------------------------------------------------------

describe('useWorkflowPersistence — load', () => {
  it('does not fetch metadata for a new (unsaved) workflow with no id', async () => {
    renderWithRoute();
    // Give effects a tick.
    await act(async () => { await Promise.resolve(); });
    expect(mockedApiClient.get).not.toHaveBeenCalledWith('/workflows/undefined');
    expect(mockLoadWorkflow).not.toHaveBeenCalled();
  });

  it('fetches the workflow metadata and hydrates the canvas when editing', async () => {
    const hydrated = {
      nodes: [{ id: 'entry', type: 'unifiedEntryNode', position: { x: 0, y: 0 }, data: {} }] as Node[],
      edges: [] as Edge[],
    };
    mockLoadWorkflow.mockResolvedValue(hydrated);
    mockedApiClient.get.mockResolvedValue({ data: makeApiWorkflow({ id: 'wf-7', name: 'Loaded WF' }) });

    const view = renderWithRoute({ workflowId: 'wf-7' });

    await waitFor(() => expect(view.current.workflow?.name).toBe('Loaded WF'));
    expect(mockedApiClient.get).toHaveBeenCalledWith('/workflows/wf-7');
    expect(mockLoadWorkflow).toHaveBeenCalledWith('wf-7');
    // Canvas hydration applied the deserialized nodes.
    expect(view.setNodes).toHaveBeenCalledWith(hydrated.nodes);
  });
});

// ---------------------------------------------------------------------------
// Suite: save (canvas serialization)
// ---------------------------------------------------------------------------

describe('useWorkflowPersistence — save', () => {
  it('opens the settings modal instead of saving when no workflow exists yet', async () => {
    const view = renderWithRoute();
    await act(async () => { await Promise.resolve(); });

    await act(async () => {
      view.current.handleSave();
    });

    expect(mockSaveWorkflow).not.toHaveBeenCalled();
    expect(view.current.isSettingsModalOpen).toBe(true);
  });

  it('serializes + saves an existing workflow and clears dirty on success', async () => {
    mockedApiClient.get.mockResolvedValue({ data: makeApiWorkflow({ id: 'wf-7' }) });
    mockSaveWorkflow.mockResolvedValue('wf-7');

    const view = renderWithRoute({ workflowId: 'wf-7' });
    await waitFor(() => expect(view.current.workflow?.id).toBe('wf-7'));

    await act(async () => {
      await view.current.handleSave();
    });

    expect(mockSaveWorkflow).toHaveBeenCalled();
    expect(view.current.successMessage).toBe('Workflow saved successfully');
    expect(view.current.isDirty).toBe(false);
  });

  it('surfaces an error when the serialization save throws', async () => {
    mockedApiClient.get.mockResolvedValue({ data: makeApiWorkflow({ id: 'wf-7' }) });
    mockSaveWorkflow.mockRejectedValue(new Error('save boom'));

    const view = renderWithRoute({ workflowId: 'wf-7' });
    await waitFor(() => expect(view.current.workflow?.id).toBe('wf-7'));

    await act(async () => {
      await view.current.handleSave();
    });

    expect(view.current.error).toBe('Failed to save workflow');
  });
});

// ---------------------------------------------------------------------------
// Suite: settings create/update mutation
// ---------------------------------------------------------------------------

describe('useWorkflowPersistence — settings create/update', () => {
  it('creates a new workflow (POST) and navigates to the saved id', async () => {
    mockedApiClient.post.mockResolvedValue({ data: makeApiWorkflow({ id: 'wf-created' }) });

    const view = renderWithRoute();
    await act(async () => { await Promise.resolve(); });

    await act(async () => {
      await view.current.handleSettingsSave({ name: 'Brand New' });
    });

    expect(mockedApiClient.post).toHaveBeenCalledWith('/workflows', { name: 'Brand New' });
    expect(mockNavigate).toHaveBeenCalledWith('/automations/builder/wf-created', { replace: true });
    expect(view.current.successMessage).toBe('Workflow created');
    expect(view.current.workflow?.id).toBe('wf-created');
  });

  it('updates an existing workflow (PATCH) without navigating', async () => {
    mockedApiClient.get.mockResolvedValue({ data: makeApiWorkflow({ id: 'wf-7' }) });
    mockedApiClient.patch.mockResolvedValue({ data: makeApiWorkflow({ id: 'wf-7', name: 'Renamed' }) });

    const view = renderWithRoute({ workflowId: 'wf-7' });
    await waitFor(() => expect(view.current.workflow?.id).toBe('wf-7'));

    await act(async () => {
      await view.current.handleSettingsSave({ name: 'Renamed' });
    });

    expect(mockedApiClient.patch).toHaveBeenCalledWith('/workflows/wf-7', { name: 'Renamed' });
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(view.current.successMessage).toBe('Workflow settings updated');
    expect(view.current.workflow?.name).toBe('Renamed');
  });

  it('surfaces an error when the create/update mutation fails', async () => {
    mockedApiClient.post.mockRejectedValue(new Error('create boom'));

    const view = renderWithRoute();
    await act(async () => { await Promise.resolve(); });

    await act(async () => {
      await view.current.handleSettingsSave({ name: 'X' });
    });

    expect(view.current.error).toBe('Failed to save workflow settings');
  });
});
