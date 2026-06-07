// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { makeWrapper } from '../../../test/renderWithProviders';
import { authApi } from '../../../services/api/auth';
import ApiKeysAccordion from '../ApiKeysAccordion';

// Mock the auth service at the module level so authApi methods can be spied on.
vi.mock('../../../services/api/auth', () => {
  return {
    authApi: {
      listKeys: vi.fn<() => Promise<unknown>>(),
      createKey: vi.fn<(name: string) => Promise<unknown>>(),
      revokeKey: vi.fn<(id: string) => Promise<void>>(),
      // Other methods that may be needed by providers
      getStatus: vi.fn<() => Promise<unknown>>(),
      getMe: vi.fn<() => Promise<unknown>>(),
      login: vi.fn<(u: string, p: string) => Promise<unknown>>(),
      logout: vi.fn<() => Promise<void>>(),
      setup: vi.fn<(u: string, p: string) => Promise<unknown>>(),
      changePassword: vi.fn<(o: string, n: string) => Promise<void>>(),
      changeUsername: vi.fn<(p: string, u: string) => Promise<unknown>>(),
    },
  };
});

const mockAuthApi = vi.mocked(authApi);

/** Render the accordion expanded (autoFocus) unless told otherwise. */
function renderAccordion(autoFocus = true) {
  return render(<ApiKeysAccordion autoFocus={autoFocus} />, { wrapper: makeWrapper() });
}

/** Minimal ApiKeyInfo fixture. */
function makeKey(overrides?: Partial<{ id: string; name: string; created_at: string; last_used_at: string | null }>) {
  return {
    id: 'key-1',
    name: 'My test key',
    created_at: '2025-01-15T10:00:00Z',
    last_used_at: null,
    ...overrides,
  };
}

describe('ApiKeysAccordion', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    // Default: auth status returns unauthenticated to keep AuthProvider happy
    mockAuthApi.getStatus.mockResolvedValue({ authenticated: false, setup_needed: false, username: null });
    mockAuthApi.getMe.mockRejectedValue(new Error('401'));

    // Default clipboard mock
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined),
      },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ---------------------------------------------------------------------------
  // Accordion shell
  // ---------------------------------------------------------------------------

  describe('accordion shell', () => {
    it('is collapsed by default (no autoFocus)', () => {
      mockAuthApi.listKeys.mockResolvedValue([]);
      renderAccordion(false);
      expect(screen.getByRole('button', { name: /api keys/i })).toHaveAttribute(
        'aria-expanded',
        'false',
      );
    });

    it('auto-expands when autoFocus is set (deep-link target)', () => {
      mockAuthApi.listKeys.mockResolvedValue([]);
      renderAccordion(true);
      expect(screen.getByRole('button', { name: /api keys/i })).toHaveAttribute(
        'aria-expanded',
        'true',
      );
    });
  });

  // ---------------------------------------------------------------------------
  // Initial load
  // ---------------------------------------------------------------------------

  describe('initial load', () => {
    it('shows a loading spinner on mount', () => {
      // Never-resolving promise to keep it in loading state
      mockAuthApi.listKeys.mockReturnValue(new Promise(() => {}));
      renderAccordion();
      expect(screen.getByRole('progressbar')).toBeInTheDocument();
    });

    it('renders the section heading', async () => {
      mockAuthApi.listKeys.mockResolvedValue([]);
      renderAccordion();
      await waitFor(() => expect(screen.getByText('API keys')).toBeInTheDocument());
    });

    it('renders the "New key" button', async () => {
      mockAuthApi.listKeys.mockResolvedValue([]);
      renderAccordion();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /new key/i })).toBeInTheDocument(),
      );
    });

    it('shows the empty-state message when no keys exist', async () => {
      mockAuthApi.listKeys.mockResolvedValue([]);
      renderAccordion();
      await waitFor(() =>
        expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument(),
      );
    });

    it('renders a list of keys in a table', async () => {
      mockAuthApi.listKeys.mockResolvedValue([
        makeKey({ id: 'key-1', name: 'CI Pipeline' }),
        makeKey({ id: 'key-2', name: 'Laptop CLI', last_used_at: '2025-03-01T09:00:00Z' }),
      ]);
      renderAccordion();
      await waitFor(() => expect(screen.getByText('CI Pipeline')).toBeInTheDocument());
      expect(screen.getByText('Laptop CLI')).toBeInTheDocument();
    });

    it('formats the "Last used" date and falls back to "Never"', async () => {
      mockAuthApi.listKeys.mockResolvedValue([
        makeKey({ id: 'key-1', name: 'Key A', last_used_at: null }),
      ]);
      renderAccordion();
      await waitFor(() => expect(screen.getByText('Key A')).toBeInTheDocument());
      // last_used_at is null → "Never"
      expect(screen.getAllByText('Never').length).toBeGreaterThan(0);
    });

    it('shows an error alert when listKeys fails', async () => {
      mockAuthApi.listKeys.mockRejectedValue(new Error('Network error'));
      renderAccordion();
      await waitFor(() =>
        expect(screen.getByRole('alert')).toBeInTheDocument(),
      );
      expect(screen.getByText(/network error/i)).toBeInTheDocument();
    });

    it('shows table column headers when keys exist', async () => {
      mockAuthApi.listKeys.mockResolvedValue([makeKey()]);
      renderAccordion();
      await waitFor(() => expect(screen.getByText('Name')).toBeInTheDocument());
      expect(screen.getByText('Created')).toBeInTheDocument();
      expect(screen.getByText('Last used')).toBeInTheDocument();
      expect(screen.getByText('Actions')).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Create key dialog
  // ---------------------------------------------------------------------------

  describe('create key dialog', () => {
    it('opens the create dialog when "New key" is clicked', async () => {
      mockAuthApi.listKeys.mockResolvedValue([]);
      renderAccordion();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /new key/i })).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole('button', { name: /new key/i }));
      await waitFor(() =>
        expect(screen.getByRole('dialog', { name: /create api key/i })).toBeInTheDocument(),
      );
    });

    it('shows the key name text field in the create dialog', async () => {
      mockAuthApi.listKeys.mockResolvedValue([]);
      renderAccordion();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /new key/i })).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole('button', { name: /new key/i }));
      await waitFor(() =>
        expect(screen.getByLabelText(/key name/i)).toBeInTheDocument(),
      );
    });

    it('Create button is disabled when key name is empty', async () => {
      mockAuthApi.listKeys.mockResolvedValue([]);
      renderAccordion();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /new key/i })).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole('button', { name: /new key/i }));
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^create$/i })).toBeDisabled(),
      );
    });

    it('closes the create dialog when Cancel is clicked', async () => {
      mockAuthApi.listKeys.mockResolvedValue([]);
      renderAccordion();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /new key/i })).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole('button', { name: /new key/i }));
      await waitFor(() =>
        expect(screen.getByRole('dialog', { name: /create api key/i })).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
      await waitFor(() =>
        expect(screen.queryByRole('dialog', { name: /create api key/i })).not.toBeInTheDocument(),
      );
    });

    it('calls createKey and then shows the reveal dialog with the plaintext key', async () => {
      const createdResponse = {
        id: 'new-key-id',
        name: 'My New Key',
        key: 'cc-supersecretplaintextkey123',
        created_at: '2025-05-01T00:00:00Z',
      };
      mockAuthApi.listKeys.mockResolvedValue([]);
      mockAuthApi.createKey.mockResolvedValue(createdResponse);

      renderAccordion();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /new key/i })).toBeInTheDocument(),
      );

      // Open create dialog
      fireEvent.click(screen.getByRole('button', { name: /new key/i }));
      await waitFor(() =>
        expect(screen.getByLabelText(/key name/i)).toBeInTheDocument(),
      );

      // Type a name
      const input = screen.getByLabelText(/key name/i);
      await userEvent.type(input, 'My New Key');

      // Click Create
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^create$/i })).not.toBeDisabled(),
      );
      fireEvent.click(screen.getByRole('button', { name: /^create$/i }));

      // The plaintext key reveal dialog should appear
      await waitFor(() =>
        expect(screen.getByText('cc-supersecretplaintextkey123')).toBeInTheDocument(),
      );
      expect(screen.getByText(/api key created/i)).toBeInTheDocument();
      expect(mockAuthApi.createKey).toHaveBeenCalledWith('My New Key');
    });

    it('shows the warning alert inside the reveal dialog', async () => {
      const createdResponse = {
        id: 'new-key-id',
        name: 'My New Key',
        key: 'cc-supersecretplaintextkey123',
        created_at: '2025-05-01T00:00:00Z',
      };
      mockAuthApi.listKeys.mockResolvedValue([]);
      mockAuthApi.createKey.mockResolvedValue(createdResponse);

      renderAccordion();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /new key/i })).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole('button', { name: /new key/i }));
      await waitFor(() => expect(screen.getByLabelText(/key name/i)).toBeInTheDocument());
      await userEvent.type(screen.getByLabelText(/key name/i), 'My New Key');
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^create$/i })).not.toBeDisabled(),
      );
      fireEvent.click(screen.getByRole('button', { name: /^create$/i }));

      await waitFor(() =>
        expect(screen.getByText(/save this key now/i)).toBeInTheDocument(),
      );
    });

    it('shows error alert when createKey fails', async () => {
      mockAuthApi.listKeys.mockResolvedValue([]);
      mockAuthApi.createKey.mockRejectedValue(new Error('Server error'));

      renderAccordion();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /new key/i })).toBeInTheDocument(),
      );

      fireEvent.click(screen.getByRole('button', { name: /new key/i }));
      await waitFor(() => expect(screen.getByLabelText(/key name/i)).toBeInTheDocument());
      await userEvent.type(screen.getByLabelText(/key name/i), 'Failing Key');
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^create$/i })).not.toBeDisabled(),
      );
      fireEvent.click(screen.getByRole('button', { name: /^create$/i }));

      // Wait for the dialog to close (createKey rejected so dialog stays up, but error shows in background)
      await waitFor(() =>
        expect(screen.getAllByRole('alert', { hidden: true }).length).toBeGreaterThan(0),
      );
      expect(screen.getByText(/server error/i)).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Copy-to-clipboard (reveal dialog)
  // ---------------------------------------------------------------------------

  describe('copy to clipboard', () => {
    async function renderWithRevealDialog() {
      const createdResponse = {
        id: 'new-key-id',
        name: 'Clipboard Key',
        key: 'cc-clipboardtestkey999',
        created_at: '2025-05-01T00:00:00Z',
      };
      mockAuthApi.listKeys.mockResolvedValue([]);
      mockAuthApi.createKey.mockResolvedValue(createdResponse);

      renderAccordion();
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /new key/i })).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole('button', { name: /new key/i }));
      await waitFor(() => expect(screen.getByLabelText(/key name/i)).toBeInTheDocument());
      await userEvent.type(screen.getByLabelText(/key name/i), 'Clipboard Key');
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /^create$/i })).not.toBeDisabled(),
      );
      fireEvent.click(screen.getByRole('button', { name: /^create$/i }));
      await waitFor(() =>
        expect(screen.getByText('cc-clipboardtestkey999')).toBeInTheDocument(),
      );
    }

    it('renders a copy button in the reveal dialog', async () => {
      await renderWithRevealDialog();
      expect(screen.getByRole('button', { name: /copy to clipboard/i })).toBeInTheDocument();
    });

    it('calls clipboard.writeText with the key when copy is clicked', async () => {
      await renderWithRevealDialog();
      fireEvent.click(screen.getByRole('button', { name: /copy to clipboard/i }));
      await waitFor(() =>
        expect(navigator.clipboard.writeText).toHaveBeenCalledWith('cc-clipboardtestkey999'),
      );
    });

    it('changes copy button aria-label to "Copied!" after clicking', async () => {
      await renderWithRevealDialog();
      fireEvent.click(screen.getByRole('button', { name: /copy to clipboard/i }));
      // After a successful clipboard write the component sets keyCopied = true
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /copied!/i })).toBeInTheDocument(),
      );
    });

    it('dismisses the reveal dialog when "I\'ve saved it" is clicked', async () => {
      await renderWithRevealDialog();
      const savedButton = screen.getByRole('button', { name: /i've saved it/i });
      fireEvent.click(savedButton);
      await waitFor(() =>
        expect(screen.queryByText('cc-clipboardtestkey999')).not.toBeInTheDocument(),
      );
    });
  });

  // ---------------------------------------------------------------------------
  // Revoke (delete) key
  // ---------------------------------------------------------------------------

  describe('revoke key', () => {
    it('shows revoke buttons for each key', async () => {
      mockAuthApi.listKeys.mockResolvedValue([
        makeKey({ id: 'key-1', name: 'Key Alpha' }),
        makeKey({ id: 'key-2', name: 'Key Beta' }),
      ]);
      renderAccordion();
      await waitFor(() => expect(screen.getByText('Key Alpha')).toBeInTheDocument());
      expect(screen.getByRole('button', { name: /revoke key alpha/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /revoke key beta/i })).toBeInTheDocument();
    });

    it('opens the revoke confirmation dialog when the delete button is clicked', async () => {
      mockAuthApi.listKeys.mockResolvedValue([makeKey({ id: 'key-1', name: 'CI Pipeline' })]);
      renderAccordion();
      await waitFor(() => expect(screen.getByText('CI Pipeline')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: /revoke ci pipeline/i }));
      await waitFor(() =>
        expect(screen.getByRole('dialog', { name: /revoke api key/i })).toBeInTheDocument(),
      );
      // Confirm the dialog title is visible
      expect(screen.getByText('Revoke API key')).toBeInTheDocument();
    });

    it('shows the key name in the revoke confirmation dialog', async () => {
      mockAuthApi.listKeys.mockResolvedValue([makeKey({ id: 'key-1', name: 'Secret Script' })]);
      renderAccordion();
      await waitFor(() => expect(screen.getByText('Secret Script')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: /revoke secret script/i }));
      await waitFor(() =>
        expect(screen.getByRole('dialog', { name: /revoke api key/i })).toBeInTheDocument(),
      );
      // The key name appears inside a <strong> within the dialog's confirmation paragraph.
      // Multiple matches expected (table row + dialog strong), so use getAllByText.
      expect(screen.getAllByText('Secret Script').length).toBeGreaterThanOrEqual(2);
    });

    it('closes the revoke dialog when Cancel is clicked', async () => {
      mockAuthApi.listKeys.mockResolvedValue([makeKey({ id: 'key-1', name: 'Cancel Key' })]);
      renderAccordion();
      await waitFor(() => expect(screen.getByText('Cancel Key')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: /revoke cancel key/i }));
      await waitFor(() =>
        expect(screen.getByRole('dialog', { name: /revoke api key/i })).toBeInTheDocument(),
      );
      // Click the cancel button inside the dialog
      const cancelBtn = screen.getAllByRole('button', { name: /cancel/i })[0];
      fireEvent.click(cancelBtn);
      await waitFor(() =>
        expect(screen.queryByRole('dialog', { name: /revoke api key/i })).not.toBeInTheDocument(),
      );
    });

    it('calls revokeKey with the correct id and refreshes the list', async () => {
      mockAuthApi.listKeys
        .mockResolvedValueOnce([makeKey({ id: 'key-1', name: 'To Delete' })])
        .mockResolvedValueOnce([]); // after revoke, list is empty
      mockAuthApi.revokeKey.mockResolvedValue(undefined);

      renderAccordion();
      await waitFor(() => expect(screen.getByText('To Delete')).toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: /revoke to delete/i }));
      await waitFor(() =>
        expect(screen.getByRole('dialog', { name: /revoke api key/i })).toBeInTheDocument(),
      );

      // Confirm revoke
      fireEvent.click(screen.getByRole('button', { name: /^revoke$/i }));

      await waitFor(() => expect(mockAuthApi.revokeKey).toHaveBeenCalledWith('key-1'));
      // After revoke, list refreshes to empty
      await waitFor(() => expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument());
    });

    it('shows an error alert when revokeKey fails', async () => {
      mockAuthApi.listKeys.mockResolvedValue([makeKey({ id: 'key-1', name: 'Bad Key' })]);
      mockAuthApi.revokeKey.mockRejectedValue(new Error('Delete failed'));

      renderAccordion();
      await waitFor(() => expect(screen.getByText('Bad Key')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: /revoke bad key/i }));
      await waitFor(() =>
        expect(screen.getByRole('dialog', { name: /revoke api key/i })).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole('button', { name: /^revoke$/i }));

      // The error alert is in the background (behind the dialog which stays open on error),
      // so we need { hidden: true } to find it.
      await waitFor(() =>
        expect(screen.getAllByRole('alert', { hidden: true }).length).toBeGreaterThan(0),
      );
      expect(screen.getByText(/delete failed/i)).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Error dismissal
  // ---------------------------------------------------------------------------

  describe('error alert dismissal', () => {
    it('can dismiss an error alert by clicking the close button', async () => {
      mockAuthApi.listKeys.mockRejectedValue(new Error('Load failed'));
      renderAccordion();
      await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
      // MUI Alert renders a close button with role="button"
      const closeBtn = screen.getByTitle('Close');
      fireEvent.click(closeBtn);
      await waitFor(() =>
        expect(screen.queryByRole('alert')).not.toBeInTheDocument(),
      );
    });
  });
});
