// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import type { SourceTag } from '../../types';

// ---------------------------------------------------------------------------
// Mock the api service module
// ---------------------------------------------------------------------------
const mockList = vi.fn<() => Promise<SourceTag[]>>();
const mockCreate = vi.fn<(data: { name: string; color?: string; description?: string }) => Promise<SourceTag>>();
const mockUpdate = vi.fn<(id: string, data: { name?: string; color?: string; description?: string }) => Promise<SourceTag>>();
const mockDelete = vi.fn<(id: string) => Promise<void>>();

vi.mock('../../services/api/sources', () => ({
  tagsApi: {
    list: (...args: Parameters<typeof mockList>) => mockList(...args),
    create: (...args: Parameters<typeof mockCreate>) => mockCreate(...args),
    update: (...args: Parameters<typeof mockUpdate>) => mockUpdate(...args),
    delete: (...args: Parameters<typeof mockDelete>) => mockDelete(...args),
  },
}));

// ---------------------------------------------------------------------------
// Import component AFTER mocking
// ---------------------------------------------------------------------------
import TagManager from '../TagManager';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const sampleTag: SourceTag = {
  id: 'tag-1',
  database_name: 'test_db',
  name: 'Research',
  color: '#3f51b5',
  description: 'Research related sources',
  created_at: '2024-01-01T00:00:00Z',
};

const anotherTag: SourceTag = {
  id: 'tag-2',
  database_name: 'test_db',
  name: 'Archive',
  color: '#e91e63',
  description: undefined,
  created_at: '2024-01-02T00:00:00Z',
};

function renderTagManager(overrides: {
  open?: boolean;
  onClose?: () => void;
  onTagsChanged?: () => void;
} = {}) {
  const onClose = overrides.onClose ?? vi.fn();
  const onTagsChanged = overrides.onTagsChanged ?? vi.fn();
  const open = overrides.open ?? true;
  render(<TagManager open={open} onClose={onClose} onTagsChanged={onTagsChanged} />);
  return { onClose, onTagsChanged };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('TagManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockList.mockResolvedValue([]);
    mockCreate.mockResolvedValue(sampleTag);
    mockUpdate.mockResolvedValue(sampleTag);
    mockDelete.mockResolvedValue(undefined);
  });

  // -------------------------------------------------------------------------
  // Mount / initial state
  // -------------------------------------------------------------------------
  describe('initial render', () => {
    it('renders the dialog title', async () => {
      renderTagManager();
      expect(await screen.findByText('Manage Tags')).toBeInTheDocument();
    });

    it('shows the "New Tag" button', async () => {
      renderTagManager();
      expect(await screen.findByRole('button', { name: /new tag/i })).toBeInTheDocument();
    });

    it('calls tagsApi.list on open', async () => {
      renderTagManager();
      await waitFor(() => expect(mockList).toHaveBeenCalledTimes(1));
    });

    it('shows "No tags yet" when the list is empty', async () => {
      mockList.mockResolvedValue([]);
      renderTagManager();
      expect(await screen.findByText('No tags yet')).toBeInTheDocument();
    });

    it('does NOT call tagsApi.list when open is false', () => {
      renderTagManager({ open: false });
      expect(mockList).not.toHaveBeenCalled();
    });
  });

  // -------------------------------------------------------------------------
  // Listing tags
  // -------------------------------------------------------------------------
  describe('listing tags', () => {
    it('renders tag names as chips', async () => {
      mockList.mockResolvedValue([sampleTag, anotherTag]);
      renderTagManager();
      expect(await screen.findByText('Research')).toBeInTheDocument();
      expect(await screen.findByText('Archive')).toBeInTheDocument();
    });

    it('renders tag description as secondary text', async () => {
      mockList.mockResolvedValue([sampleTag]);
      renderTagManager();
      expect(await screen.findByText('Research related sources')).toBeInTheDocument();
    });

    it('renders edit and delete buttons for each tag', async () => {
      mockList.mockResolvedValue([sampleTag]);
      renderTagManager();
      expect(await screen.findByRole('button', { name: /edit tag/i })).toBeInTheDocument();
      expect(await screen.findByRole('button', { name: /delete tag/i })).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Close button
  // -------------------------------------------------------------------------
  describe('close button', () => {
    it('calls onClose when the Close button is clicked', async () => {
      const { onClose } = renderTagManager();
      // Wait for list to resolve
      await screen.findByText('No tags yet');
      fireEvent.click(screen.getByRole('button', { name: /^close$/i }));
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  // -------------------------------------------------------------------------
  // Create tag flow
  // -------------------------------------------------------------------------
  describe('create tag flow', () => {
    it('shows the create form when "New Tag" is clicked', async () => {
      renderTagManager();
      await screen.findByText('No tags yet');
      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));
      expect(screen.getByText('Create New Tag')).toBeInTheDocument();
    });

    it('hides the tag list and shows form fields after clicking New Tag', async () => {
      renderTagManager();
      await screen.findByText('No tags yet');
      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));
      expect(screen.getByLabelText(/tag name/i)).toBeInTheDocument();
    });

    it('calls tagsApi.create with name and color on Create', async () => {
      mockList.mockResolvedValue([]);
      renderTagManager();
      await screen.findByText('No tags yet');

      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));

      // Fill tag name
      const nameInput = screen.getByLabelText(/tag name/i);
      fireEvent.change(nameInput, { target: { value: 'NewTag' } });

      // Click the Create button
      const createBtn = screen.getByRole('button', { name: /^create$/i });
      fireEvent.click(createBtn);

      await waitFor(() => {
        expect(mockCreate).toHaveBeenCalledWith(
          expect.objectContaining({ name: 'NewTag' })
        );
      });
    });

    it('calls onTagsChanged after creating a tag', async () => {
      const { onTagsChanged } = renderTagManager();
      await screen.findByText('No tags yet');

      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));
      const nameInput = screen.getByLabelText(/tag name/i);
      fireEvent.change(nameInput, { target: { value: 'NewTag' } });
      fireEvent.click(screen.getByRole('button', { name: /^create$/i }));

      await waitFor(() => expect(onTagsChanged).toHaveBeenCalledTimes(1));
    });

    it('hides the form and returns to list after successful create', async () => {
      mockList.mockResolvedValue([sampleTag]);
      renderTagManager();
      await screen.findByText('No tags yet');

      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));
      const nameInput = screen.getByLabelText(/tag name/i);
      fireEvent.change(nameInput, { target: { value: 'NewTag' } });
      fireEvent.click(screen.getByRole('button', { name: /^create$/i }));

      // After create, the list is reloaded and the form hides
      await waitFor(() => expect(screen.queryByText('Create New Tag')).not.toBeInTheDocument());
    });

    it('Create button is disabled when name is empty', async () => {
      renderTagManager();
      await screen.findByText('No tags yet');
      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));
      const createBtn = screen.getByRole('button', { name: /^create$/i });
      expect(createBtn).toBeDisabled();
    });

    it('allows selecting a color from the palette', async () => {
      renderTagManager();
      await screen.findByText('No tags yet');
      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));

      // The palette renders color buttons with aria-label = the color hex string
      // Click first available color button (TagPalette has at least 1 entry)
      const colorButtons = screen.getAllByRole('button').filter((btn) =>
        btn.getAttribute('aria-label')?.startsWith('#')
      );
      expect(colorButtons.length).toBeGreaterThan(0);
      fireEvent.click(colorButtons[0]);
      // No error thrown; color updated (no assertion on internal state needed)
    });

    it('allows entering a custom hex color', async () => {
      renderTagManager();
      await screen.findByText('No tags yet');
      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));

      const hexInput = screen.getByPlaceholderText('#3f51b5');
      fireEvent.change(hexInput, { target: { value: '#aabbcc' } });
      expect(hexInput).toHaveValue('#aabbcc');
    });

    it('allows entering a description', async () => {
      renderTagManager();
      await screen.findByText('No tags yet');
      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));

      const descInput = screen.getByLabelText(/description/i);
      fireEvent.change(descInput, { target: { value: 'A description' } });
      expect(descInput).toHaveValue('A description');
    });
  });

  // -------------------------------------------------------------------------
  // Cancel form
  // -------------------------------------------------------------------------
  describe('cancel form', () => {
    it('hides the form when Cancel is clicked', async () => {
      renderTagManager();
      await screen.findByText('No tags yet');
      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));
      expect(screen.getByText('Create New Tag')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
      expect(screen.queryByText('Create New Tag')).not.toBeInTheDocument();
    });

    it('does NOT call tagsApi.create when Cancel is clicked', async () => {
      renderTagManager();
      await screen.findByText('No tags yet');
      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));

      const nameInput = screen.getByLabelText(/tag name/i);
      fireEvent.change(nameInput, { target: { value: 'Unsaved' } });
      fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

      expect(mockCreate).not.toHaveBeenCalled();
    });
  });

  // -------------------------------------------------------------------------
  // Edit tag flow
  // -------------------------------------------------------------------------
  describe('edit tag flow', () => {
    it('shows "Edit Tag" heading when edit button is clicked', async () => {
      mockList.mockResolvedValue([sampleTag]);
      renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /edit tag/i }));
      expect(screen.getByText('Edit Tag')).toBeInTheDocument();
    });

    it('pre-fills the name field with the existing tag name', async () => {
      mockList.mockResolvedValue([sampleTag]);
      renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /edit tag/i }));
      const nameInput = screen.getByLabelText(/tag name/i);
      expect(nameInput).toHaveValue('Research');
    });

    it('pre-fills the description field with the existing description', async () => {
      mockList.mockResolvedValue([sampleTag]);
      renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /edit tag/i }));
      const descInput = screen.getByLabelText(/description/i);
      expect(descInput).toHaveValue('Research related sources');
    });

    it('calls tagsApi.update with the tag id and updated data on Update', async () => {
      mockList.mockResolvedValue([sampleTag]);
      renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /edit tag/i }));
      const nameInput = screen.getByLabelText(/tag name/i);
      fireEvent.change(nameInput, { target: { value: 'Updated Name' } });

      fireEvent.click(screen.getByRole('button', { name: /^update$/i }));

      await waitFor(() => {
        expect(mockUpdate).toHaveBeenCalledWith(
          'tag-1',
          expect.objectContaining({ name: 'Updated Name' })
        );
      });
    });

    it('calls onTagsChanged after updating a tag', async () => {
      mockList.mockResolvedValue([sampleTag]);
      const { onTagsChanged } = renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /edit tag/i }));
      const nameInput = screen.getByLabelText(/tag name/i);
      fireEvent.change(nameInput, { target: { value: 'Updated' } });
      fireEvent.click(screen.getByRole('button', { name: /^update$/i }));

      await waitFor(() => expect(onTagsChanged).toHaveBeenCalledTimes(1));
    });

    it('hides the form after a successful update', async () => {
      mockList.mockResolvedValue([sampleTag]);
      renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /edit tag/i }));
      fireEvent.click(screen.getByRole('button', { name: /^update$/i }));

      await waitFor(() => expect(screen.queryByText('Edit Tag')).not.toBeInTheDocument());
    });
  });

  // -------------------------------------------------------------------------
  // Delete tag flow
  // -------------------------------------------------------------------------
  describe('delete tag flow', () => {
    it('opens a confirmation dialog when delete button is clicked', async () => {
      mockList.mockResolvedValue([sampleTag]);
      renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /delete tag/i }));
      expect(await screen.findByText('Confirm Delete')).toBeInTheDocument();
      expect(screen.getByText(/are you sure you want to delete/i)).toBeInTheDocument();
    });

    it('calls tagsApi.delete and onTagsChanged when confirmed', async () => {
      mockList.mockResolvedValue([sampleTag]);
      const { onTagsChanged } = renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /delete tag/i }));
      await screen.findByText('Confirm Delete');

      // Click "Delete" in the confirm dialog
      const deleteBtn = screen.getAllByRole('button', { name: /^delete$/i })[0];
      fireEvent.click(deleteBtn);

      await waitFor(() => {
        expect(mockDelete).toHaveBeenCalledWith('tag-1');
        expect(onTagsChanged).toHaveBeenCalledTimes(1);
      });
    });

    it('closes the confirm dialog without deleting when Cancel is clicked', async () => {
      mockList.mockResolvedValue([sampleTag]);
      renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /delete tag/i }));
      await screen.findByText('Confirm Delete');

      // Cancel the dialog
      const cancelBtns = screen.getAllByRole('button', { name: /^cancel$/i });
      fireEvent.click(cancelBtns[cancelBtns.length - 1]);

      await waitFor(() =>
        expect(screen.queryByText('Confirm Delete')).not.toBeInTheDocument()
      );
      expect(mockDelete).not.toHaveBeenCalled();
    });

    it('reloads the tag list after deletion', async () => {
      mockList.mockResolvedValue([sampleTag]);
      renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /delete tag/i }));
      await screen.findByText('Confirm Delete');

      // Call count before delete confirmation
      const listCallsBefore = mockList.mock.calls.length;

      const deleteBtn = screen.getAllByRole('button', { name: /^delete$/i })[0];
      fireEvent.click(deleteBtn);

      await waitFor(() => {
        expect(mockList.mock.calls.length).toBeGreaterThan(listCallsBefore);
      });
    });
  });

  // -------------------------------------------------------------------------
  // Error handling
  // -------------------------------------------------------------------------
  describe('error handling', () => {
    it('shows an error message when tagsApi.list fails', async () => {
      mockList.mockRejectedValue(new Error('Network error'));
      renderTagManager();
      expect(await screen.findByText(/failed to load tags/i)).toBeInTheDocument();
    });

    it('shows an error message when tagsApi.create fails', async () => {
      mockList.mockResolvedValue([]);
      mockCreate.mockRejectedValue(new Error('Create failed'));
      renderTagManager();
      await screen.findByText('No tags yet');

      fireEvent.click(screen.getByRole('button', { name: /new tag/i }));
      const nameInput = screen.getByLabelText(/tag name/i);
      fireEvent.change(nameInput, { target: { value: 'FailTag' } });
      fireEvent.click(screen.getByRole('button', { name: /^create$/i }));

      expect(await screen.findByText(/failed to save tag/i)).toBeInTheDocument();
    });

    it('shows an error message when tagsApi.update fails', async () => {
      mockList.mockResolvedValue([sampleTag]);
      mockUpdate.mockRejectedValue(new Error('Update failed'));
      renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /edit tag/i }));
      fireEvent.click(screen.getByRole('button', { name: /^update$/i }));

      expect(await screen.findByText(/failed to save tag/i)).toBeInTheDocument();
    });

    it('shows an error message when tagsApi.delete fails', async () => {
      mockList.mockResolvedValue([sampleTag]);
      mockDelete.mockRejectedValue(new Error('Delete failed'));
      renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /delete tag/i }));
      await screen.findByText('Confirm Delete');

      const deleteBtn = screen.getAllByRole('button', { name: /^delete$/i })[0];
      fireEvent.click(deleteBtn);

      expect(await screen.findByText(/failed to delete tag/i)).toBeInTheDocument();
    });

    it('closes the confirm dialog even when delete fails', async () => {
      mockList.mockResolvedValue([sampleTag]);
      mockDelete.mockRejectedValue(new Error('Delete failed'));
      renderTagManager();
      await screen.findByText('Research');

      fireEvent.click(screen.getByRole('button', { name: /delete tag/i }));
      await screen.findByText('Confirm Delete');

      const deleteBtn = screen.getAllByRole('button', { name: /^delete$/i })[0];
      fireEvent.click(deleteBtn);

      await waitFor(() =>
        expect(screen.queryByText('Confirm Delete')).not.toBeInTheDocument()
      );
    });
  });

  // -------------------------------------------------------------------------
  // Multiple tags
  // -------------------------------------------------------------------------
  describe('multiple tags', () => {
    it('renders multiple edit and delete buttons for multiple tags', async () => {
      mockList.mockResolvedValue([sampleTag, anotherTag]);
      renderTagManager();
      await screen.findByText('Research');
      await screen.findByText('Archive');

      const editBtns = screen.getAllByRole('button', { name: /edit tag/i });
      const deleteBtns = screen.getAllByRole('button', { name: /delete tag/i });
      expect(editBtns).toHaveLength(2);
      expect(deleteBtns).toHaveLength(2);
    });

    it('edits the correct tag when clicking its edit button', async () => {
      mockList.mockResolvedValue([sampleTag, anotherTag]);
      renderTagManager();
      await screen.findByText('Research');

      const editBtns = screen.getAllByRole('button', { name: /edit tag/i });
      // Click the second tag's edit button
      fireEvent.click(editBtns[1]);

      const nameInput = screen.getByLabelText(/tag name/i);
      expect(nameInput).toHaveValue('Archive');
    });
  });
});
