// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * InlineTagEditor tests, pinned across the TanStack Query migration.
 *
 * Mocks at the `apiClient` layer (like the EdgeDetailPage reference test) so
 * the real `useSourceTags` query/mutation hooks and the underlying service
 * modules run unchanged. The editor's server state — assigned tags + the
 * global catalog — now lives in TanStack Query; add/remove/create are
 * mutations that invalidate `['source', sourceId, 'tags']`.
 *
 * Endpoints exercised:
 *   GET    /sources/{id}/tags        — assigned tags
 *   GET    /sources/tags             — global tag catalog
 *   POST   /sources/{id}/tags/{tag}  — assign
 *   DELETE /sources/{id}/tags/{tag}  — unassign
 *   POST   /sources/tags             — create
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../test/renderWithProviders';
import type { SourceTag } from '../../../../types';

vi.mock('../../../../services/api/client', () => installApiClientMock());

vi.mock('../../../../components/TagManager', () => ({
  default: (props: { open: boolean; onClose: () => void; onTagsChanged: () => void }) => (
    <div
      data-testid="tag-manager"
      data-open={String(props.open)}
      onClick={props.onTagsChanged}
    />
  ),
}));

vi.mock('../../../../utils/logger', () => ({
  logger: {
    error: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
  },
}));

// Mock TagPalette so getRandomColor() won't fail
vi.mock('../../../../theme/colors', () => ({
  TagPalette: ['#ff0000', '#00ff00', '#0000ff'],
}));

// ---------------------------------------------------------------------------
// Imports AFTER mocks to pick up stubbed modules
// ---------------------------------------------------------------------------

import { InlineTagEditor } from '../InlineTagEditor';
import { apiClient } from '../../../../services/api/client';
import { logger } from '../../../../utils/logger';

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const makeTag = (id: string, name: string, color = '#aabbcc'): SourceTag => ({
  id,
  database_name: 'testdb',
  name,
  color,
  created_at: '2026-01-01T00:00:00Z',
});

const TAG_A = makeTag('tag-1', 'Alpha');
const TAG_B = makeTag('tag-2', 'Beta');
const TAG_C = makeTag('tag-3', 'Gamma');

/**
 * Wire the apiClient GET mock to serve assigned tags + catalog. `assigned`
 * is a getter so tests can mutate the array (e.g. after an assign) and have
 * the next refetch reflect it.
 */
function setupMocks(
  assignedTags: SourceTag[] = [TAG_A],
  availableTags: SourceTag[] = [TAG_A, TAG_B, TAG_C],
) {
  const state = { assigned: assignedTags, available: availableTags };
  mockedApiClient.get.mockImplementation((url: string) => {
    if (/\/sources\/[^/]+\/tags$/.test(url)) {
      return Promise.resolve({ data: state.assigned });
    }
    if (url === '/sources/tags') {
      return Promise.resolve({ data: state.available });
    }
    return Promise.resolve({ data: {} });
  });
  mockedApiClient.post.mockResolvedValue({ data: {} });
  mockedApiClient.delete.mockResolvedValue({ data: {} });
  return state;
}

function renderEditor() {
  return render(<InlineTagEditor sourceId="s1" />, { wrapper: makeWrapper() });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('InlineTagEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // -------------------------------------------------------------------------
  // Initial load
  // -------------------------------------------------------------------------

  describe('initial load', () => {
    it('fetches assigned tags and the catalog on mount', async () => {
      setupMocks();
      renderEditor();

      await waitFor(() => {
        expect(mockedApiClient.get).toHaveBeenCalledWith('/sources/s1/tags');
        expect(mockedApiClient.get).toHaveBeenCalledWith('/sources/tags');
      });
    });

    it('renders assigned tag chips after loading', async () => {
      setupMocks([TAG_A, TAG_B], [TAG_A, TAG_B, TAG_C]);
      renderEditor();

      await waitFor(() => {
        expect(screen.getByText('Alpha')).toBeInTheDocument();
        expect(screen.getByText('Beta')).toBeInTheDocument();
      });
    });

    it('renders the Add tag chip and the Manage tags button', async () => {
      setupMocks([], [TAG_A]);
      renderEditor();

      await waitFor(() => {
        expect(screen.getByText('Add tag')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /manage tags/i })).toBeInTheDocument();
      });
    });

    it('does not render chips when there are no assigned tags', async () => {
      setupMocks([], [TAG_A, TAG_B]);
      renderEditor();

      await waitFor(() => expect(screen.getByText('Add tag')).toBeInTheDocument());
      expect(screen.queryByText('Alpha')).not.toBeInTheDocument();
      expect(screen.queryByText('Beta')).not.toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Load error paths
  // -------------------------------------------------------------------------

  describe('error handling on load', () => {
    it('logs an error when the assigned-tags fetch fails', async () => {
      mockedApiClient.get.mockImplementation((url: string) => {
        if (/\/sources\/[^/]+\/tags$/.test(url)) {
          return Promise.reject(new Error('network'));
        }
        return Promise.resolve({ data: [] });
      });

      renderEditor();

      await waitFor(() => {
        expect(logger.error).toHaveBeenCalledWith(
          expect.stringContaining('Failed to load tags'),
          expect.any(Error),
        );
      });
    });

    it('logs an error when the catalog fetch fails', async () => {
      mockedApiClient.get.mockImplementation((url: string) => {
        if (url === '/sources/tags') return Promise.reject(new Error('network'));
        return Promise.resolve({ data: [] });
      });

      renderEditor();

      await waitFor(() => {
        expect(logger.error).toHaveBeenCalledWith(
          expect.stringContaining('Failed to load available tags'),
          expect.any(Error),
        );
      });
    });
  });

  // -------------------------------------------------------------------------
  // Remove (unassign) a tag
  // -------------------------------------------------------------------------

  describe('removing a tag', () => {
    it('DELETEs the tag assignment when the chip delete icon is clicked', async () => {
      const state = setupMocks([TAG_A], [TAG_A, TAG_B]);

      renderEditor();

      await waitFor(() => expect(screen.getByText('Alpha')).toBeInTheDocument());

      // After unassign, the refetch should report no assigned tags.
      mockedApiClient.delete.mockImplementationOnce(() => {
        state.assigned = [];
        return Promise.resolve({ data: {} });
      });

      // MUI Chip renders delete as CancelIcon SVG with data-testid
      const deleteIcon = screen.getByTestId('CancelIcon');
      fireEvent.click(deleteIcon);

      await waitFor(() => {
        expect(mockedApiClient.delete).toHaveBeenCalledWith('/sources/s1/tags/tag-1');
      });
    });

    it('logs an error when unassign fails', async () => {
      setupMocks([TAG_A], [TAG_A]);
      mockedApiClient.delete.mockRejectedValue(new Error('fail'));

      renderEditor();

      await waitFor(() => expect(screen.getByText('Alpha')).toBeInTheDocument());

      const deleteIcon = screen.getByTestId('CancelIcon');
      fireEvent.click(deleteIcon);

      await waitFor(() => {
        expect(logger.error).toHaveBeenCalledWith(
          expect.stringContaining('Failed to unassign tag'),
          expect.any(Error),
        );
      });
    });
  });

  // -------------------------------------------------------------------------
  // Add tag — open input
  // -------------------------------------------------------------------------

  describe('add tag input', () => {
    it('shows autocomplete when Add tag chip is clicked', async () => {
      setupMocks([], [TAG_B, TAG_C]);
      renderEditor();

      await waitFor(() => expect(screen.getByText('Add tag')).toBeInTheDocument());

      fireEvent.click(screen.getByText('Add tag'));

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/type tag name/i)).toBeInTheDocument();
      });
    });

    it('hides Add tag chip and shows input when clicked', async () => {
      setupMocks([], [TAG_A]);
      renderEditor();

      await waitFor(() => expect(screen.getByText('Add tag')).toBeInTheDocument());

      fireEvent.click(screen.getByText('Add tag'));

      expect(screen.queryByText('Add tag')).not.toBeInTheDocument();
      expect(screen.getByPlaceholderText(/type tag name/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Add tag — select existing tag
  // -------------------------------------------------------------------------

  describe('assigning an existing tag', () => {
    it('POSTs the assignment when an existing unassigned tag is selected', async () => {
      const user = userEvent.setup();
      setupMocks([], [TAG_B, TAG_C]);

      renderEditor();

      await waitFor(() => expect(screen.getByText('Add tag')).toBeInTheDocument());

      await user.click(screen.getByText('Add tag'));
      await waitFor(() => expect(screen.getByPlaceholderText(/type tag name/i)).toBeInTheDocument());

      const input = screen.getByPlaceholderText(/type tag name/i);
      await user.type(input, 'Be');

      await waitFor(() => expect(screen.getByText('Beta')).toBeInTheDocument());
      await user.click(screen.getByText('Beta'));

      await waitFor(() => {
        expect(mockedApiClient.post).toHaveBeenCalledWith('/sources/s1/tags/tag-2');
      });
    });

    it('logs an error when assign fails', async () => {
      const user = userEvent.setup();
      setupMocks([], [TAG_B]);
      mockedApiClient.post.mockRejectedValue(new Error('assign fail'));

      renderEditor();

      await waitFor(() => expect(screen.getByText('Add tag')).toBeInTheDocument());

      await user.click(screen.getByText('Add tag'));
      await waitFor(() => expect(screen.getByPlaceholderText(/type tag name/i)).toBeInTheDocument());

      const input = screen.getByPlaceholderText(/type tag name/i);
      await user.type(input, 'Be');

      await waitFor(() => expect(screen.getByText('Beta')).toBeInTheDocument());
      await user.click(screen.getByText('Beta'));

      await waitFor(() => {
        expect(logger.error).toHaveBeenCalledWith(
          expect.stringContaining('Failed to assign tag'),
          expect.any(Error),
        );
      });
    });
  });

  // -------------------------------------------------------------------------
  // TagManager dialog
  // -------------------------------------------------------------------------

  describe('TagManager dialog', () => {
    it('TagManager stub is rendered with open=false initially', async () => {
      setupMocks();
      renderEditor();

      await waitFor(() => {
        const tm = screen.getByTestId('tag-manager');
        expect(tm).toHaveAttribute('data-open', 'false');
      });
    });

    it('opens TagManager when Manage tags button is clicked', async () => {
      setupMocks();
      renderEditor();

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /manage tags/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /manage tags/i }));

      await waitFor(() => {
        const tm = screen.getByTestId('tag-manager');
        expect(tm).toHaveAttribute('data-open', 'true');
      });
    });

    it('refreshes tags when TagManager fires onTagsChanged', async () => {
      setupMocks([TAG_A], [TAG_A, TAG_B]);
      renderEditor();

      await waitFor(() => expect(screen.getByText('Alpha')).toBeInTheDocument());

      const callsBefore = mockedApiClient.get.mock.calls.filter(
        (c) => c[0] === '/sources/s1/tags',
      ).length;

      // The stub's onClick fires onTagsChanged → refreshTags() invalidates.
      const tm = screen.getByTestId('tag-manager');
      fireEvent.click(tm);

      await waitFor(() => {
        const callsAfter = mockedApiClient.get.mock.calls.filter(
          (c) => c[0] === '/sources/s1/tags',
        ).length;
        expect(callsAfter).toBeGreaterThan(callsBefore);
      });
    });
  });

  // -------------------------------------------------------------------------
  // Create new tag (freeSolo path)
  // -------------------------------------------------------------------------

  describe('creating a new tag', () => {
    it('POSTs a new tag then assigns it for a brand-new tag name', async () => {
      const user = userEvent.setup();
      setupMocks([], []); // no available tags at all
      const newTag = makeTag('tag-new', 'NewTag');
      mockedApiClient.post.mockImplementation((url: string) => {
        if (url === '/sources/tags') return Promise.resolve({ data: newTag });
        return Promise.resolve({ data: {} });
      });

      renderEditor();

      await waitFor(() => expect(screen.getByText('Add tag')).toBeInTheDocument());

      await user.click(screen.getByText('Add tag'));
      await waitFor(() => expect(screen.getByPlaceholderText(/type tag name/i)).toBeInTheDocument());

      const input = screen.getByPlaceholderText(/type tag name/i);
      await user.type(input, 'NewTag');

      await waitFor(() => {
        expect(screen.getByText(/Create "NewTag"/i)).toBeInTheDocument();
      });

      await user.click(screen.getByText(/Create "NewTag"/i));

      await waitFor(() => {
        expect(mockedApiClient.post).toHaveBeenCalledWith(
          '/sources/tags',
          expect.objectContaining({ name: 'NewTag' }),
        );
        expect(mockedApiClient.post).toHaveBeenCalledWith('/sources/s1/tags/tag-new');
      });
    });
  });

  // -------------------------------------------------------------------------
  // ClickAway closes input
  // -------------------------------------------------------------------------

  describe('ClickAwayListener', () => {
    it('closes the input when user clicks away', async () => {
      setupMocks([], [TAG_A]);
      render(
        <div>
          <InlineTagEditor sourceId="s1" />
          <button type="button" data-testid="outside">Outside</button>
        </div>,
        { wrapper: makeWrapper() },
      );

      await waitFor(() => expect(screen.getByText('Add tag')).toBeInTheDocument());

      fireEvent.click(screen.getByText('Add tag'));
      await waitFor(() => expect(screen.getByPlaceholderText(/type tag name/i)).toBeInTheDocument());

      fireEvent.click(screen.getByTestId('outside'));

      await waitFor(() => {
        expect(screen.queryByPlaceholderText(/type tag name/i)).not.toBeInTheDocument();
        expect(screen.getByText('Add tag')).toBeInTheDocument();
      });
    });
  });
});
