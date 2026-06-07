// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for ItemCreationModal — the node creation dialog.
 *
 * The modal does not create the node itself: it loads node templates (via the
 * `useTemplates` query), lets the user pick one, and delegates creation to the
 * parent through the `onCreate(templateId, position)` callback (which the
 * canvas wires to its graph-mutating create handler). These tests pin that
 * load → select → onCreate contract plus the template-fetch error path.
 *
 * Mocks at the apiClient layer so the real `templateApi` + `useTemplates` hook
 * run unchanged across the TanStack Query migration.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import type { Template } from '../../../../types';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { ItemCreationModal } from '../ItemCreationModal';
import { apiClient } from '../../../../services/api/client';

vi.mock('../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

function makeTemplate(overrides: Partial<Template> = {}): Template {
  return {
    id: 'tmpl-1',
    name: 'Person',
    description: 'A person node',
    template_type: 'node',
    properties: [],
    is_system: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

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

function renderModal(props: Partial<React.ComponentProps<typeof ItemCreationModal>> = {}) {
  const defaults = {
    open: true,
    onClose: vi.fn<() => void>(),
    onCreate: vi.fn<(id: string, pos?: { x: number; y: number }) => void>(),
  };
  return render(<ItemCreationModal {...defaults} {...props} />, { wrapper: makeWrapper() });
}

describe('ItemCreationModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads node templates and renders them in the select', async () => {
    mockTemplatesList([makeTemplate({ id: 't-person', name: 'Person' })]);
    renderModal();

    // The default-selected template name renders in the closed Select.
    expect(await screen.findByText('Person')).toBeInTheDocument();
    expect(mockedApiClient.get).toHaveBeenCalledWith('/templates', expect.anything());
  });

  it('excludes system node templates (isSystemTemplate ids)', async () => {
    // ItemCreationModal filters via isSystemTemplate, which matches the exact
    // infrastructure ids (system_lens / system_workflow / system_workflow_step).
    mockTemplatesList([
      makeTemplate({ id: 'system_lens', name: 'SystemLens' }),
      makeTemplate({ id: 't-ok', name: 'GoodTmpl' }),
    ]);
    renderModal();

    // Open the dropdown to inspect the available options.
    const combo = await screen.findByRole('combobox');
    await waitFor(() => expect(combo).not.toHaveAttribute('aria-disabled', 'true'));
    fireEvent.mouseDown(combo);
    const listbox = await screen.findByRole('listbox');
    expect(within(listbox).getByText('GoodTmpl')).toBeInTheDocument();
    expect(within(listbox).queryByText('SystemLens')).not.toBeInTheDocument();
  });

  it('calls onCreate with the selected template id and position, then onClose', async () => {
    const onCreate = vi.fn<(id: string, pos?: { x: number; y: number }) => void>();
    const onClose = vi.fn<() => void>();
    mockTemplatesList([makeTemplate({ id: 't-person', name: 'Person' })]);

    renderModal({ onCreate, onClose, position: { x: 10, y: 20 } });

    // Wait for the default selection to populate (Create enabled).
    await screen.findByText('Person');
    const createBtn = screen.getByRole('button', { name: 'Create' });
    await waitFor(() => expect(createBtn).not.toBeDisabled());

    fireEvent.click(createBtn);

    expect(onCreate).toHaveBeenCalledWith('t-person', { x: 10, y: 20 });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('lets the user change the selected template before creating', async () => {
    const onCreate = vi.fn<(id: string, pos?: { x: number; y: number }) => void>();
    mockTemplatesList([
      makeTemplate({ id: 't-a', name: 'Alpha' }),
      makeTemplate({ id: 't-b', name: 'Beta' }),
    ]);

    renderModal({ onCreate });

    // Default selection is the first template; open the dropdown and pick Beta.
    await screen.findByText('Alpha');
    fireEvent.mouseDown(screen.getByRole('combobox'));
    const listbox = await screen.findByRole('listbox');
    fireEvent.click(within(listbox).getByText('Beta'));

    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(onCreate).toHaveBeenCalledWith('t-b', undefined);
  });

  it('shows the empty-state warning and disables Create when no templates exist', async () => {
    mockTemplatesList([]);
    renderModal();

    expect(
      await screen.findByText('No item templates available. Create a template first.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled();
  });

  it('surfaces an error alert when the template fetch fails', async () => {
    // getApiErrorMessage surfaces the Error.message, falling back to the
    // generic copy only when the error carries no message.
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/templates') return Promise.reject(new Error('boom'));
      return Promise.resolve({ data: {} });
    });

    renderModal();

    expect(await screen.findByText('boom')).toBeInTheDocument();
  });
});
