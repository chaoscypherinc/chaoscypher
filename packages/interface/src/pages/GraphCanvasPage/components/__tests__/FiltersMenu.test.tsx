// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for the FiltersMenu popover in CanvasControlMenus.
 *
 * Both the template fetch (`useTemplates`) and the source fetch
 * (`useSourceSummaries`) are now TanStack Query hooks gated on `open`: they
 * populate their multi-select filters, defer fetching until the popover opens,
 * and log (without crashing) on failure. Mocks at the apiClient layer so the
 * real hooks + `templateApi` / `sourcesApi` services run.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { FiltersMenu } from '../CanvasControlMenus';
import { apiClient } from '../../../../services/api/client';
import { logger } from '../../../../utils/logger';
import type { Template } from '../../../../types';

vi.mock('../../../../services/api/client', () => installApiClientMock());

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

function makeTemplate(overrides: Partial<Template> = {}): Template {
  return {
    id: 'tmpl-1',
    name: 'Person',
    template_type: 'node',
    properties: [],
    is_system: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function mockEndpoints(
  opts: { templates?: Template[]; sources?: unknown[]; templateError?: boolean; sourceError?: boolean } = {},
) {
  const { templates = [], sources = [], templateError = false, sourceError = false } = opts;
  mockedApiClient.get.mockImplementation((url: string) => {
    if (url === '/templates') {
      if (templateError) return Promise.reject(new Error('tmpl boom'));
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
    if (url === '/sources') {
      if (sourceError) return Promise.reject(new Error('source boom'));
      return Promise.resolve({
        data: {
          data: sources,
          pagination: {
            total: sources.length,
            page: 1,
            page_size: 200,
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

function renderMenu(props: Partial<React.ComponentProps<typeof FiltersMenu>> = {}) {
  const anchor = document.createElement('div');
  document.body.appendChild(anchor);
  const defaults = {
    anchorEl: anchor,
    open: true,
    onClose: vi.fn<() => void>(),
    selectedTemplateFilters: [] as string[],
    onTemplateFiltersChange: vi.fn<(ids: string[]) => void>(),
    selectedSourceFilters: [] as string[],
    onSourceFiltersChange: vi.fn<(ids: string[]) => void>(),
  };
  return render(<FiltersMenu {...defaults} {...props} />, { wrapper: makeWrapper() });
}

describe('FiltersMenu', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads node templates (lens/workflow excluded) into the template filter', async () => {
    mockEndpoints({
      templates: [
        makeTemplate({ id: 'tmpl-person', name: 'Person' }),
        makeTemplate({ id: 'lens-x', name: 'LensTmpl' }),
        makeTemplate({ id: 'e-1', name: 'EdgeTmpl', template_type: 'edge' }),
      ],
    });

    renderMenu();

    // MUI outlined select renders the label in both the InputLabel and the
    // fieldset legend, so there are multiple matches once the control mounts.
    await waitFor(() =>
      expect(screen.getAllByText('Show Templates').length).toBeGreaterThan(0),
    );
    // Absence of the empty-state proves the kept template made it through.
    expect(screen.queryByText('No templates available')).not.toBeInTheDocument();
  });

  it('shows the empty template state when no node templates are returned', async () => {
    mockEndpoints({ templates: [], sources: [] });

    renderMenu();

    expect(await screen.findByText('No templates available')).toBeInTheDocument();
  });

  it('loads sources into the source filter', async () => {
    mockEndpoints({
      templates: [],
      sources: [{ id: 'src-1', title: 'Doc One', filename: 'one.pdf' }],
    });

    renderMenu();

    await waitFor(() =>
      expect(screen.getAllByText('Source Documents').length).toBeGreaterThan(0),
    );
    expect(screen.queryByText('No sources available')).not.toBeInTheDocument();
  });

  it('requests sources at the configured page size when open', async () => {
    mockEndpoints({
      templates: [],
      sources: [{ id: 'src-1', title: 'Doc One', filename: 'one.pdf' }],
    });

    renderMenu();

    await waitFor(() =>
      expect(mockedApiClient.get).toHaveBeenCalledWith('/sources', {
        // fakeSettings -> default public config page size (200).
        params: { page_size: 200 },
      }),
    );
  });

  it('does not fetch sources or templates while the popover is closed (enabled: open)', () => {
    mockEndpoints({ templates: [], sources: [] });

    renderMenu({ open: false });

    expect(mockedApiClient.get).not.toHaveBeenCalledWith('/sources', expect.anything());
    expect(mockedApiClient.get).not.toHaveBeenCalledWith('/templates', expect.anything());
  });

  it('logs an error when the template fetch fails (without crashing)', async () => {
    mockEndpoints({ templateError: true, sources: [] });

    renderMenu();

    await waitFor(() =>
      expect(logger.error).toHaveBeenCalledWith('Error loading templates:', expect.anything()),
    );
    // The menu still renders its static title.
    expect(screen.getByText('Filters')).toBeInTheDocument();
  });

  it('logs an error when the source fetch fails (without crashing)', async () => {
    mockEndpoints({ templates: [], sourceError: true });

    renderMenu();

    await waitFor(() =>
      expect(logger.error).toHaveBeenCalledWith('Error loading sources:', expect.anything()),
    );
    expect(screen.getByText('Filters')).toBeInTheDocument();
  });
});
