// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for TemplateSelectionModal — the search-driven template picker dialog.
 * Covers: open/load cycle, type filtering, search, selection, recently-used
 * section, close button, and error path.
 *
 * Mocks at the apiClient layer (not the service barrel) so the real
 * `templateApi` + `useTemplates` query hook run unchanged across the TanStack
 * Query migration. The component is wrapped in the shared provider stack so
 * the query client is available.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { Template } from '../../../../types';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { TemplateSelectionModal } from '../TemplateSelectionModal';
import { apiClient } from '../../../../services/api/client';
import { logger } from '../../../../utils/logger';

vi.mock('../../../../services/api/client', () => installApiClientMock());

vi.mock('../../../../components/TemplateIcon', () => ({
  default: () => <span data-testid="template-icon" />,
}));

vi.mock('../../../../utils/logger', () => ({
  logger: {
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    debug: vi.fn(),
  },
}));

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderModal(props: Partial<React.ComponentProps<typeof TemplateSelectionModal>> = {}) {
  const defaults = {
    open: true,
    onClose: vi.fn<() => void>(),
    onSelect: vi.fn<(id: string) => void>(),
  };
  return render(<TemplateSelectionModal {...defaults} {...props} />, {
    wrapper: makeWrapper(),
  });
}

function makeTemplate(overrides: Partial<Template> = {}): Template {
  return {
    id: 'tmpl-1',
    name: 'Company',
    description: 'A company node',
    template_type: 'node',
    properties: [{ name: 'industry', display_name: 'Industry', property_type: 'string' }],
    is_system: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

/**
 * `templateApi.list` fetches all pages via `apiClient.get('/templates', ...)`
 * which returns `{ data: { data: [...], pagination } }`. Stub that shape with
 * a single, terminal page.
 */
function mockTemplatesList(templates: Template[]) {
  mockedApiClient.get.mockImplementation((url: string) => {
    if (url === '/templates') {
      return Promise.resolve({
        data: {
          data: templates,
          pagination: {
            total: templates.length,
            page: 1,
            page_size: 100,
            total_pages: 1,
            has_next: false,
            has_prev: false,
          },
        },
      });
    }
    return Promise.resolve({ data: {} });
  });
}

// ---------------------------------------------------------------------------
// localStorage stub helpers
// ---------------------------------------------------------------------------

const RECENT_KEY = 'recent_templates';

function setRecentIds(ids: string[]) {
  localStorage.setItem(RECENT_KEY, JSON.stringify(ids));
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('TemplateSelectionModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  // -------------------------------------------------------------------------
  // Rendering when closed
  // -------------------------------------------------------------------------

  it('renders nothing when open=false', () => {
    mockTemplatesList([]);
    renderModal({ open: false });
    // Dialog content is not in the DOM when closed (MUI keepMounted is false by default)
    expect(screen.queryByText(/Select/i)).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Dialog title and type label
  // -------------------------------------------------------------------------

  it('shows "Select Item Template" for node type (default)', async () => {
    mockTemplatesList([]);
    renderModal({ templateType: 'node' });
    expect(await screen.findByText('Select Item Template')).toBeInTheDocument();
  });

  it('shows "Select Link Template" for edge type', async () => {
    mockTemplatesList([]);
    renderModal({ templateType: 'edge' });
    expect(await screen.findByText('Select Link Template')).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Loading templates and rendering list
  // -------------------------------------------------------------------------

  it('fetches templates on open and renders returned templates', async () => {
    const tmpl = makeTemplate({ id: 'tmpl-node', name: 'Person', template_type: 'node' });
    mockTemplatesList([tmpl]);

    renderModal({ templateType: 'node' });

    await waitFor(() => expect(screen.getByText('Person')).toBeInTheDocument());
    expect(mockedApiClient.get).toHaveBeenCalledWith('/templates', expect.anything());
    // Description rendered
    expect(screen.getByText('A company node')).toBeInTheDocument();
    // Template icon stub rendered
    expect(screen.getAllByTestId('template-icon').length).toBeGreaterThan(0);
  });

  it('filters out templates whose template_type does not match', async () => {
    const nodeT = makeTemplate({ id: 'n1', name: 'NodeTmpl', template_type: 'node' });
    const edgeT = makeTemplate({ id: 'e1', name: 'EdgeTmpl', template_type: 'edge' });
    mockTemplatesList([nodeT, edgeT]);

    renderModal({ templateType: 'node' });

    await waitFor(() => expect(screen.getByText('NodeTmpl')).toBeInTheDocument());
    expect(screen.queryByText('EdgeTmpl')).not.toBeInTheDocument();
  });

  it('filters out node templates whose id contains "lens"', async () => {
    const lensT = makeTemplate({ id: 'lens-overview', name: 'LensView', template_type: 'node' });
    const normalT = makeTemplate({ id: 'tmpl-person', name: 'Person', template_type: 'node' });
    mockTemplatesList([lensT, normalT]);

    renderModal({ templateType: 'node' });

    await waitFor(() => expect(screen.getByText('Person')).toBeInTheDocument());
    expect(screen.queryByText('LensView')).not.toBeInTheDocument();
  });

  it('filters out node templates whose id contains "workflow"', async () => {
    const wfT = makeTemplate({ id: 'workflow-basic', name: 'BasicWorkflow', template_type: 'node' });
    const normalT = makeTemplate({ id: 'tmpl-company', name: 'Company', template_type: 'node' });
    mockTemplatesList([wfT, normalT]);

    renderModal({ templateType: 'node' });

    await waitFor(() => expect(screen.getByText('Company')).toBeInTheDocument());
    expect(screen.queryByText('BasicWorkflow')).not.toBeInTheDocument();
  });

  it('shows system chip for is_system templates', async () => {
    const sysTmpl = makeTemplate({ id: 'sys-1', name: 'SysNode', is_system: true, template_type: 'node' });
    mockTemplatesList([sysTmpl]);

    renderModal({ templateType: 'node' });

    await waitFor(() => expect(screen.getByText('SysNode')).toBeInTheDocument());
    expect(screen.getByText('System')).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Empty state
  // -------------------------------------------------------------------------

  it('shows "No templates available" when list is empty and no search', async () => {
    mockTemplatesList([]);
    renderModal({ templateType: 'node' });

    expect(await screen.findByText('No templates available')).toBeInTheDocument();
  });

  it('shows "No templates found" when search yields no results', async () => {
    const tmpl = makeTemplate({ id: 'tmpl-x', name: 'Banana', template_type: 'node' });
    mockTemplatesList([tmpl]);

    renderModal({ templateType: 'node' });
    await waitFor(() => expect(screen.getByText('Banana')).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText('Search templates...'), {
      target: { value: 'zzz-no-match' },
    });

    await waitFor(() =>
      expect(screen.getByText('No templates found')).toBeInTheDocument(),
    );
    expect(screen.getByText('Try adjusting your search terms')).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Search filtering
  // -------------------------------------------------------------------------

  it('filters templates by name as user types', async () => {
    const tmpl1 = makeTemplate({ id: 'n1', name: 'Company', description: 'corp desc', template_type: 'node' });
    const tmpl2 = makeTemplate({ id: 'n2', name: 'Person', description: 'human desc', template_type: 'node' });
    mockTemplatesList([tmpl1, tmpl2]);

    renderModal({ templateType: 'node' });
    await waitFor(() => expect(screen.getByText('Company')).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText('Search templates...'), {
      target: { value: 'comp' },
    });

    await waitFor(() => expect(screen.queryByText('Person')).not.toBeInTheDocument());
    expect(screen.getByText('Company')).toBeInTheDocument();
  });

  it('filters templates by description as user types', async () => {
    const tmpl1 = makeTemplate({ id: 'n1', name: 'Org', description: 'organization entity', template_type: 'node' });
    const tmpl2 = makeTemplate({ id: 'n2', name: 'Place', description: 'physical location', template_type: 'node' });
    mockTemplatesList([tmpl1, tmpl2]);

    renderModal({ templateType: 'node' });
    await waitFor(() => expect(screen.getByText('Org')).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText('Search templates...'), {
      target: { value: 'location' },
    });

    await waitFor(() => expect(screen.queryByText('Org')).not.toBeInTheDocument());
    expect(screen.getByText('Place')).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Template selection
  // -------------------------------------------------------------------------

  it('calls onSelect with the template id when a card is clicked', async () => {
    const onSelect = vi.fn<(id: string) => void>();
    const tmpl = makeTemplate({ id: 'tmpl-chosen', name: 'ChosenOne', template_type: 'node' });
    mockTemplatesList([tmpl]);

    renderModal({ templateType: 'node', onSelect });
    await waitFor(() => expect(screen.getByText('ChosenOne')).toBeInTheDocument());

    fireEvent.click(screen.getByText('ChosenOne'));

    expect(onSelect).toHaveBeenCalledWith('tmpl-chosen');
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('calls onClose after selecting a template', async () => {
    const onClose = vi.fn<() => void>();
    const tmpl = makeTemplate({ id: 'tmpl-2', name: 'Widget', template_type: 'node' });
    mockTemplatesList([tmpl]);

    renderModal({ templateType: 'node', onClose });
    await waitFor(() => expect(screen.getByText('Widget')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Widget'));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('saves the selected template id to recent_templates in localStorage', async () => {
    const tmpl = makeTemplate({ id: 'tmpl-save', name: 'SaveMe', template_type: 'node' });
    mockTemplatesList([tmpl]);

    renderModal({ templateType: 'node' });
    await waitFor(() => expect(screen.getByText('SaveMe')).toBeInTheDocument());

    fireEvent.click(screen.getByText('SaveMe'));

    const stored = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]') as string[];
    expect(stored).toContain('tmpl-save');
  });

  // -------------------------------------------------------------------------
  // Close button
  // -------------------------------------------------------------------------

  it('calls onClose when the close icon button is clicked', async () => {
    const onClose = vi.fn<() => void>();
    mockTemplatesList([]);

    renderModal({ onClose });
    await screen.findByRole('button', { name: 'Close' });

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('clears the search query after the modal is closed and reopened', async () => {
    const tmpl = makeTemplate({ id: 'n1', name: 'Alpha', template_type: 'node' });
    mockTemplatesList([tmpl]);

    const wrapper = makeWrapper();
    const { rerender } = render(
      <TemplateSelectionModal open onClose={vi.fn()} onSelect={vi.fn()} templateType="node" />,
      { wrapper },
    );
    await waitFor(() => expect(screen.getByText('Alpha')).toBeInTheDocument());

    // Type a search query
    fireEvent.change(screen.getByPlaceholderText('Search templates...'), {
      target: { value: 'alpha query' },
    });

    // Close the dialog (clears the query in handleClose)
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    // Reopen
    rerender(
      <TemplateSelectionModal open onClose={vi.fn()} onSelect={vi.fn()} templateType="node" />,
    );

    await waitFor(() => expect(screen.getByText('Alpha')).toBeInTheDocument());
    expect(screen.getByPlaceholderText('Search templates...')).toHaveValue('');
  });

  // -------------------------------------------------------------------------
  // Recently used section
  // -------------------------------------------------------------------------

  it('shows "Recently Used" section when recentTemplates exist and no search', async () => {
    const tmpl = makeTemplate({ id: 'recent-1', name: 'RecentTmpl', template_type: 'node' });
    mockTemplatesList([tmpl]);
    setRecentIds(['recent-1']);

    renderModal({ templateType: 'node' });

    await waitFor(() => expect(screen.getByText('Recently Used')).toBeInTheDocument());
    // Template name should appear (at least once, possibly in both sections)
    expect(screen.getAllByText('RecentTmpl').length).toBeGreaterThan(0);
  });

  it('hides "Recently Used" section during an active search', async () => {
    const tmpl = makeTemplate({ id: 'r1', name: 'RecentNode', template_type: 'node' });
    mockTemplatesList([tmpl]);
    setRecentIds(['r1']);

    renderModal({ templateType: 'node' });

    await waitFor(() => expect(screen.getByText('Recently Used')).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText('Search templates...'), {
      target: { value: 'RecentNode' },
    });

    await waitFor(() =>
      expect(screen.queryByText('Recently Used')).not.toBeInTheDocument(),
    );
  });

  it('shows "All Templates" label when recent templates AND main templates exist', async () => {
    const recentTmpl = makeTemplate({ id: 'r1', name: 'RecentOne', template_type: 'node' });
    const otherTmpl = makeTemplate({ id: 'o1', name: 'OtherOne', template_type: 'node' });
    mockTemplatesList([recentTmpl, otherTmpl]);
    setRecentIds(['r1']);

    renderModal({ templateType: 'node' });

    await waitFor(() => expect(screen.getByText('All Templates')).toBeInTheDocument());
    expect(screen.getByText('Recently Used')).toBeInTheDocument();
    expect(screen.getByText('OtherOne')).toBeInTheDocument();
  });

  it('excludes recent templates from the main list', async () => {
    const recentTmpl = makeTemplate({ id: 'r1', name: 'RecentItem', template_type: 'node' });
    const otherTmpl = makeTemplate({ id: 'o1', name: 'OtherItem', template_type: 'node' });
    mockTemplatesList([recentTmpl, otherTmpl]);
    setRecentIds(['r1']);

    renderModal({ templateType: 'node' });

    await waitFor(() => expect(screen.getByText('All Templates')).toBeInTheDocument());

    // RecentItem appears exactly once (in Recently Used), OtherItem appears once (main list)
    const recentItems = screen.getAllByText('RecentItem');
    const otherItems = screen.getAllByText('OtherItem');
    expect(recentItems).toHaveLength(1);
    expect(otherItems).toHaveLength(1);
  });

  it('does not show "Recently Used" when no matching templates are loaded', async () => {
    // Set a recent id but the template does not exist in the loaded list
    mockTemplatesList([]);
    setRecentIds(['non-existent-id']);

    renderModal({ templateType: 'node' });

    await screen.findByText('No templates available');
    expect(screen.queryByText('Recently Used')).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Props count chip
  // -------------------------------------------------------------------------

  it('renders the property count chip for each template', async () => {
    const tmpl = makeTemplate({
      id: 'p1',
      name: 'PropRich',
      template_type: 'node',
      properties: [
        { name: 'a', display_name: 'A', property_type: 'string' },
        { name: 'b', display_name: 'B', property_type: 'string' },
      ],
    });
    mockTemplatesList([tmpl]);

    renderModal({ templateType: 'node' });

    await waitFor(() => expect(screen.getByText('2 props')).toBeInTheDocument());
  });

  // -------------------------------------------------------------------------
  // Error path
  // -------------------------------------------------------------------------

  it('calls logger.error when the template fetch rejects', async () => {
    const error = new Error('network failure');
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/templates') return Promise.reject(error);
      return Promise.resolve({ data: {} });
    });

    renderModal({ templateType: 'node' });

    await waitFor(() =>
      expect(logger.error).toHaveBeenCalledWith('Error loading templates:', error),
    );
  });

  it('calls logger.error when localStorage.getItem throws', async () => {
    mockTemplatesList([]);

    const originalGetItem = Storage.prototype.getItem;
    Storage.prototype.getItem = vi.fn<(key: string) => string | null>(() => {
      throw new Error('localStorage unavailable');
    });

    renderModal({ templateType: 'node' });

    await waitFor(() => expect(logger.error).toHaveBeenCalled());

    Storage.prototype.getItem = originalGetItem;
  });

  // -------------------------------------------------------------------------
  // Re-fetch on templateType change
  // -------------------------------------------------------------------------

  it('re-fetches templates when templateType prop changes', async () => {
    const nodeTmpl = makeTemplate({ id: 'n1', name: 'NodeType', template_type: 'node' });
    const edgeTmpl = makeTemplate({ id: 'e1', name: 'EdgeType', template_type: 'edge' });

    // Server filters by template_type, so route the response on the query param.
    mockedApiClient.get.mockImplementation((url: string, opts?: { params?: Record<string, unknown> }) => {
      if (url === '/templates') {
        const type = opts?.params?.template_type;
        const list = type === 'edge' ? [edgeTmpl] : [nodeTmpl];
        return Promise.resolve({
          data: {
            data: list,
            pagination: {
              total: list.length,
              page: 1,
              page_size: 100,
              total_pages: 1,
              has_next: false,
              has_prev: false,
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    const wrapper = makeWrapper();
    const { rerender } = render(
      <TemplateSelectionModal open onClose={vi.fn()} onSelect={vi.fn()} templateType="node" />,
      { wrapper },
    );
    await waitFor(() => expect(screen.getByText('NodeType')).toBeInTheDocument());

    rerender(
      <TemplateSelectionModal open onClose={vi.fn()} onSelect={vi.fn()} templateType="edge" />,
    );

    await waitFor(() => expect(screen.getByText('EdgeType')).toBeInTheDocument());
    // One call per distinct templateType (separate query keys → separate fetches).
    const templateCalls = mockedApiClient.get.mock.calls.filter((c) => c[0] === '/templates');
    expect(templateCalls.length).toBeGreaterThanOrEqual(2);
  });
});
