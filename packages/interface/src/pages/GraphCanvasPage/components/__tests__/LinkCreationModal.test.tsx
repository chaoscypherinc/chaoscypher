// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for LinkCreationModal — the edge creation dialog.
 *
 * Like ItemCreationModal it does not create the edge itself: it loads edge
 * templates (via the `useTemplates` query), defaults the label to the
 * template name, and delegates creation to the parent via
 * `onCreate(sourceId, targetId, templateId, label)` (wired to the canvas's
 * graph-mutating edge create handler). These tests pin that load → select →
 * onCreate contract plus the template-fetch error path.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { Template } from '../../../../types';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { LinkCreationModal } from '../LinkCreationModal';
import { apiClient } from '../../../../services/api/client';

vi.mock('../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

function makeTemplate(overrides: Partial<Template> = {}): Template {
  return {
    id: 'edge-1',
    name: 'Supports',
    description: 'A supporting relationship',
    template_type: 'edge',
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

function renderModal(props: Partial<React.ComponentProps<typeof LinkCreationModal>> = {}) {
  const defaults = {
    open: true,
    onClose: vi.fn<() => void>(),
    onCreate:
      vi.fn<(s: string, t: string, tpl: string, label?: string) => void>(),
    sourceId: 'node-src',
    targetId: 'node-tgt',
  };
  return render(<LinkCreationModal {...defaults} {...props} />, { wrapper: makeWrapper() });
}

describe('LinkCreationModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads edge templates and defaults the label to the first template name', async () => {
    mockTemplatesList([makeTemplate({ id: 'e-supports', name: 'Supports' })]);
    renderModal();

    const labelInput = (await screen.findByLabelText('Label')) as HTMLInputElement;
    await waitFor(() => expect(labelInput.value).toBe('Supports'));
    expect(mockedApiClient.get).toHaveBeenCalledWith('/templates', expect.anything());
  });

  it('excludes non-edge templates', async () => {
    mockTemplatesList([
      makeTemplate({ id: 'n-1', name: 'NodeTmpl', template_type: 'node' }),
      makeTemplate({ id: 'e-1', name: 'EdgeTmpl', template_type: 'edge' }),
    ]);
    renderModal();

    await screen.findByText('EdgeTmpl');
    expect(screen.queryByText('NodeTmpl')).not.toBeInTheDocument();
  });

  it('calls onCreate with source, target, template id and label, then onClose', async () => {
    const onCreate =
      vi.fn<(s: string, t: string, tpl: string, label?: string) => void>();
    const onClose = vi.fn<() => void>();
    mockTemplatesList([makeTemplate({ id: 'e-supports', name: 'Supports' })]);

    renderModal({ onCreate, onClose, sourceId: 'src-1', targetId: 'tgt-1' });

    const labelInput = (await screen.findByLabelText('Label')) as HTMLInputElement;
    await waitFor(() => expect(labelInput.value).toBe('Supports'));

    // Edit the label, then create.
    fireEvent.change(labelInput, { target: { value: 'reinforces' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    expect(onCreate).toHaveBeenCalledWith('src-1', 'tgt-1', 'e-supports', 'reinforces');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('disables Create when source or target is missing', async () => {
    mockTemplatesList([makeTemplate({ id: 'e-1', name: 'Supports' })]);
    renderModal({ sourceId: undefined, targetId: undefined });

    await screen.findByLabelText('Label');
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
