// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { ThemeProvider, createTheme } from '@mui/material';
import AccountAccordion from '../AccountAccordion';
import { authApi } from '../../../services/api/auth';

// Minimal theme wrapper — no AuthProvider (useAuth is mocked below)
const theme = createTheme({ palette: { mode: 'dark' } });
function Wrapper({ children }: { children: ReactNode }) {
  return (
    <MemoryRouter>
      <ThemeProvider theme={theme}>{children}</ThemeProvider>
    </MemoryRouter>
  );
}

// Capture navigate mock so we can assert redirect after password change
const mockNavigate = vi.fn();
vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Provide a realistic useAuth implementation that the component actually calls
const mockLogout = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
const mockRecheckSetup = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);

vi.mock('../../../contexts/useAuth', () => ({
  useAuth: () => ({
    user: { username: 'alice' },
    logout: mockLogout,
    recheckSetup: mockRecheckSetup,
    needsSetup: false,
    isAuthenticated: true,
    loading: false,
    login: vi.fn(),
    completeSetup: vi.fn(),
  }),
}));

// ─── helpers ──────────────────────────────────────────────────────────────────

/** Render the accordion. Defaults to autoFocus so the forms are expanded. */
function renderAccordion(autoFocus = true) {
  return render(<AccountAccordion autoFocus={autoFocus} />, { wrapper: Wrapper });
}

/** Fill the Change Password form (first "Current password" input). */
function fillPasswordForm(old: string, next: string, confirm: string) {
  // Both forms have "Current password" — the first belongs to the password form
  const currentPwInputs = screen.getAllByLabelText(/current password/i);
  fireEvent.change(currentPwInputs[0], { target: { value: old } });
  // Use exact label text to avoid matching "Confirm new password"
  fireEvent.change(screen.getByLabelText('New password *'), {
    target: { value: next },
  });
  fireEvent.change(screen.getByLabelText('Confirm new password *'), {
    target: { value: confirm },
  });
}

/** Fill the Change Username form. */
function fillUsernameForm(username: string, password: string) {
  fireEvent.change(screen.getByLabelText(/new username/i), {
    target: { value: username },
  });
  // Both forms have "Current password" — the last belongs to the username form
  const pwInputs = screen.getAllByLabelText(/current password/i);
  fireEvent.change(pwInputs[pwInputs.length - 1], {
    target: { value: password },
  });
}

// ─── tests ────────────────────────────────────────────────────────────────────

describe('AccountAccordion', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLogout.mockResolvedValue(undefined);
    mockRecheckSetup.mockResolvedValue(undefined);
  });

  // ── accordion shell ───────────────────────────────────────────────────────

  it('renders the Account summary without throwing', () => {
    renderAccordion();
    expect(screen.getByText('Account')).toBeInTheDocument();
  });

  it('is collapsed by default (no autoFocus)', () => {
    renderAccordion(false);
    expect(screen.getByRole('button', { name: /account/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('auto-expands when autoFocus is set (deep-link target)', () => {
    renderAccordion(true);
    expect(screen.getByRole('button', { name: /account/i })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  // ── rendering ───────────────────────────────────────────────────────────────

  it('displays the signed-in username from useAuth', () => {
    renderAccordion();
    expect(screen.getByText(/signed in as/i)).toBeInTheDocument();
    expect(screen.getByText('alice')).toBeInTheDocument();
  });

  it('renders the Change password section heading', () => {
    renderAccordion();
    expect(screen.getByRole('heading', { name: /change password/i })).toBeInTheDocument();
  });

  it('renders the Change username section heading', () => {
    renderAccordion();
    expect(screen.getByRole('heading', { name: /change username/i })).toBeInTheDocument();
  });

  it('renders the info alert about needing to sign in again', () => {
    renderAccordion();
    expect(screen.getByText(/you'll need to sign in again/i)).toBeInTheDocument();
  });

  // ── password form – show/hide toggle ────────────────────────────────────────

  it('password fields start as type=password', () => {
    renderAccordion();
    const pwFields = document.querySelectorAll('input[type="password"]');
    // At least current + new + confirm in the password form
    expect(pwFields.length).toBeGreaterThanOrEqual(3);
  });

  it('toggles password-form inputs to type=text when Show password is clicked', () => {
    renderAccordion();
    // "Show password" button is the first IconButton with that label
    const showBtn = screen.getAllByRole('button', { name: /show password/i })[0];
    fireEvent.click(showBtn);
    // After toggle, the button label flips to "Hide password"
    expect(screen.getAllByRole('button', { name: /hide password/i })[0]).toBeInTheDocument();
    // The first "Current password" input (password form) should now be type=text
    const currentPwInputs = screen.getAllByLabelText(/current password/i);
    expect(currentPwInputs[0]).toHaveAttribute('type', 'text');
  });

  it('clicking show password again hides the password again', () => {
    renderAccordion();
    const showBtn = screen.getAllByRole('button', { name: /show password/i })[0];
    fireEvent.click(showBtn); // show
    const hideBtn = screen.getAllByRole('button', { name: /hide password/i })[0];
    fireEvent.click(hideBtn); // hide again
    expect(screen.getAllByRole('button', { name: /show password/i })[0]).toBeInTheDocument();
  });

  // ── password form – submit button disabled state ─────────────────────────────

  it('Change password button is disabled when all fields are empty', () => {
    renderAccordion();
    const btn = screen.getByRole('button', { name: /change password/i });
    expect(btn).toBeDisabled();
  });

  it('Change password button is disabled when new password is too short', () => {
    renderAccordion();
    fillPasswordForm('oldpass', 'short', 'short');
    const btn = screen.getByRole('button', { name: /change password/i });
    expect(btn).toBeDisabled();
  });

  it('Change password button is disabled when new and confirm passwords do not match', () => {
    renderAccordion();
    fillPasswordForm('oldpass', 'newpassword123', 'differentpassword');
    const btn = screen.getByRole('button', { name: /change password/i });
    expect(btn).toBeDisabled();
  });

  it('Change password button is disabled when old password is empty', () => {
    renderAccordion();
    fillPasswordForm('', 'newpassword123', 'newpassword123');
    const btn = screen.getByRole('button', { name: /change password/i });
    expect(btn).toBeDisabled();
  });

  it('Change password button is enabled when all fields are valid', () => {
    renderAccordion();
    fillPasswordForm('oldpass', 'newpassword123', 'newpassword123');
    const btn = screen.getByRole('button', { name: /change password/i });
    expect(btn).not.toBeDisabled();
  });

  // ── password form – mismatch helper text ─────────────────────────────────────

  it("shows \"Passwords don't match\" when confirm differs from new", () => {
    renderAccordion();
    fireEvent.change(screen.getByLabelText('New password *'), {
      target: { value: 'validpassword1' },
    });
    fireEvent.change(screen.getByLabelText('Confirm new password *'), {
      target: { value: 'differentvalue' },
    });
    expect(screen.getByText(/passwords don't match/i)).toBeInTheDocument();
  });

  // ── password form – successful submission ────────────────────────────────────

  it('calls authApi.changePassword with correct args and navigates to /login on success', async () => {
    const spy = vi
      .spyOn(authApi, 'changePassword')
      .mockResolvedValueOnce(undefined);
    try {
      renderAccordion();
      fillPasswordForm('oldpass', 'newpassword123', 'newpassword123');
      fireEvent.click(screen.getByRole('button', { name: /change password/i }));

      await waitFor(() => {
        expect(spy).toHaveBeenCalledWith('oldpass', 'newpassword123');
      });
      await waitFor(() => {
        expect(mockLogout).toHaveBeenCalled();
      });
      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalledWith('/login', { replace: true });
      });
    } finally {
      spy.mockRestore();
    }
  });

  it('shows success message after password change', async () => {
    const spy = vi
      .spyOn(authApi, 'changePassword')
      .mockResolvedValueOnce(undefined);
    try {
      renderAccordion();
      fillPasswordForm('oldpass', 'newpassword123', 'newpassword123');
      fireEvent.click(screen.getByRole('button', { name: /change password/i }));

      await waitFor(() => {
        expect(screen.getByText(/password changed/i)).toBeInTheDocument();
      });
    } finally {
      spy.mockRestore();
    }
  });

  it('resets password fields after a successful change', async () => {
    const spy = vi
      .spyOn(authApi, 'changePassword')
      .mockResolvedValueOnce(undefined);
    try {
      renderAccordion();
      fillPasswordForm('oldpass', 'newpassword123', 'newpassword123');
      fireEvent.click(screen.getByRole('button', { name: /change password/i }));

      await waitFor(() => {
        expect(spy).toHaveBeenCalled();
      });
      // Fields should be cleared — first "Current password" input is the password form's
      const currentPwInputs = screen.getAllByLabelText(/current password/i);
      await waitFor(() => {
        expect(currentPwInputs[0]).toHaveValue('');
      });
    } finally {
      spy.mockRestore();
    }
  });

  // ── password form – API error ────────────────────────────────────────────────

  it('shows API error message when changePassword fails', async () => {
    const spy = vi
      .spyOn(authApi, 'changePassword')
      .mockRejectedValueOnce(new Error('Wrong current password'));
    try {
      renderAccordion();
      fillPasswordForm('badpass', 'newpassword123', 'newpassword123');
      fireEvent.click(screen.getByRole('button', { name: /change password/i }));

      await waitFor(() => {
        expect(screen.getByText(/wrong current password/i)).toBeInTheDocument();
      });
    } finally {
      spy.mockRestore();
    }
  });

  it('shows error alert when changePassword rejects with a non-Error object', async () => {
    // Throw an object without a .message — getApiErrorMessage will JSON.stringify it
    const spy = vi
      .spyOn(authApi, 'changePassword')
      .mockRejectedValueOnce({ code: 'ERR_UNKNOWN' });
    try {
      renderAccordion();
      fillPasswordForm('oldpass', 'newpassword123', 'newpassword123');
      fireEvent.click(screen.getByRole('button', { name: /change password/i }));

      await waitFor(() => {
        // getApiErrorMessage JSON-stringifies the object; the error alert should appear
        expect(screen.getByText(/ERR_UNKNOWN/)).toBeInTheDocument();
      });
    } finally {
      spy.mockRestore();
    }
  });

  it('can dismiss the password error alert', async () => {
    const spy = vi
      .spyOn(authApi, 'changePassword')
      .mockRejectedValueOnce(new Error('Server error'));
    try {
      renderAccordion();
      fillPasswordForm('oldpass', 'newpassword123', 'newpassword123');
      fireEvent.click(screen.getByRole('button', { name: /change password/i }));

      await waitFor(() => {
        expect(screen.getByText(/server error/i)).toBeInTheDocument();
      });
      // Close button on the Alert
      fireEvent.click(screen.getByRole('button', { name: /close/i }));
      await waitFor(() => {
        expect(screen.queryByText(/server error/i)).not.toBeInTheDocument();
      });
    } finally {
      spy.mockRestore();
    }
  });

  // ── username form – submit button disabled state ─────────────────────────────

  it('Change username button is disabled when fields are empty', () => {
    renderAccordion();
    const btn = screen.getByRole('button', { name: /change username/i });
    expect(btn).toBeDisabled();
  });

  it('Change username button is disabled when username is too short', () => {
    renderAccordion();
    fireEvent.change(screen.getByLabelText(/new username/i), {
      target: { value: 'ab' },
    });
    const pwInputs = screen.getAllByLabelText(/current password/i);
    fireEvent.change(pwInputs[pwInputs.length - 1], {
      target: { value: 'somepassword' },
    });
    const btn = screen.getByRole('button', { name: /change username/i });
    expect(btn).toBeDisabled();
  });

  it('Change username button is disabled when password is missing', () => {
    renderAccordion();
    fireEvent.change(screen.getByLabelText(/new username/i), {
      target: { value: 'newuser' },
    });
    const btn = screen.getByRole('button', { name: /change username/i });
    expect(btn).toBeDisabled();
  });

  it('Change username button is enabled when username >= 3 chars and password provided', () => {
    renderAccordion();
    fillUsernameForm('bob', 'mypassword');
    const btn = screen.getByRole('button', { name: /change username/i });
    expect(btn).not.toBeDisabled();
  });

  // ── username form – show/hide toggle ────────────────────────────────────────

  it('username form password input starts as type=password', () => {
    renderAccordion();
    const showBtns = screen.getAllByRole('button', { name: /show password/i });
    expect(showBtns.length).toBeGreaterThanOrEqual(1);
    const pwInputs = document.querySelectorAll('input[type="password"]');
    expect(pwInputs.length).toBeGreaterThan(0);
  });

  it('toggling show password in username form flips input to text', () => {
    renderAccordion();
    // Second show-password button belongs to the username form
    const showBtns = screen.getAllByRole('button', { name: /show password/i });
    const usernameFormToggle = showBtns[showBtns.length - 1];
    fireEvent.click(usernameFormToggle);
    const hideBtns = screen.getAllByRole('button', { name: /hide password/i });
    expect(hideBtns.length).toBeGreaterThanOrEqual(1);
  });

  // ── username form – successful submission ────────────────────────────────────

  it('calls authApi.changeUsername with correct args on success', async () => {
    const spy = vi
      .spyOn(authApi, 'changeUsername')
      .mockResolvedValueOnce({ username: 'bob' });
    try {
      renderAccordion();
      fillUsernameForm('bob', 'mypassword');
      fireEvent.click(screen.getByRole('button', { name: /change username/i }));

      await waitFor(() => {
        expect(spy).toHaveBeenCalledWith('mypassword', 'bob');
      });
      await waitFor(() => {
        expect(mockRecheckSetup).toHaveBeenCalled();
      });
    } finally {
      spy.mockRestore();
    }
  });

  it('shows success message with the new username after change', async () => {
    const spy = vi
      .spyOn(authApi, 'changeUsername')
      .mockResolvedValueOnce({ username: 'bob' });
    try {
      renderAccordion();
      fillUsernameForm('bob', 'mypassword');
      fireEvent.click(screen.getByRole('button', { name: /change username/i }));

      await waitFor(() => {
        expect(screen.getByText(/username changed to "bob"/i)).toBeInTheDocument();
      });
    } finally {
      spy.mockRestore();
    }
  });

  it('resets username fields after a successful change', async () => {
    const spy = vi
      .spyOn(authApi, 'changeUsername')
      .mockResolvedValueOnce({ username: 'bob' });
    try {
      renderAccordion();
      fillUsernameForm('bob', 'mypassword');
      fireEvent.click(screen.getByRole('button', { name: /change username/i }));

      await waitFor(() => {
        expect(spy).toHaveBeenCalled();
      });
      const newUsernameInput = screen.getByLabelText(/new username/i);
      await waitFor(() => {
        expect(newUsernameInput).toHaveValue('');
      });
    } finally {
      spy.mockRestore();
    }
  });

  // ── username form – API error ────────────────────────────────────────────────

  it('shows API error message when changeUsername fails', async () => {
    const spy = vi
      .spyOn(authApi, 'changeUsername')
      .mockRejectedValueOnce(new Error('Username already taken'));
    try {
      renderAccordion();
      fillUsernameForm('bob', 'mypassword');
      fireEvent.click(screen.getByRole('button', { name: /change username/i }));

      await waitFor(() => {
        expect(screen.getByText(/username already taken/i)).toBeInTheDocument();
      });
    } finally {
      spy.mockRestore();
    }
  });

  it('shows error alert when changeUsername rejects with a non-Error object', async () => {
    // Throw an object without a .message — getApiErrorMessage will JSON.stringify it
    const spy = vi
      .spyOn(authApi, 'changeUsername')
      .mockRejectedValueOnce({ code: 'ERR_UNKNOWN' });
    try {
      renderAccordion();
      fillUsernameForm('bob', 'mypassword');
      fireEvent.click(screen.getByRole('button', { name: /change username/i }));

      await waitFor(() => {
        // An error alert should appear (getApiErrorMessage returns JSON string)
        const alerts = screen.getAllByRole('alert');
        expect(alerts.some((a) => a.classList.contains('MuiAlert-colorError'))).toBe(true);
      });
    } finally {
      spy.mockRestore();
    }
  });

  it('can dismiss the username error alert', async () => {
    const spy = vi
      .spyOn(authApi, 'changeUsername')
      .mockRejectedValueOnce(new Error('Conflict'));
    try {
      renderAccordion();
      fillUsernameForm('bob', 'mypassword');
      fireEvent.click(screen.getByRole('button', { name: /change username/i }));

      await waitFor(() => {
        expect(screen.getByText(/conflict/i)).toBeInTheDocument();
      });
      const closeButtons = screen.getAllByRole('button', { name: /close/i });
      fireEvent.click(closeButtons[0]);
      await waitFor(() => {
        expect(screen.queryByText(/conflict/i)).not.toBeInTheDocument();
      });
    } finally {
      spy.mockRestore();
    }
  });

  it('can dismiss the username success alert', async () => {
    const spy = vi
      .spyOn(authApi, 'changeUsername')
      .mockResolvedValueOnce({ username: 'bob' });
    try {
      renderAccordion();
      fillUsernameForm('bob', 'mypassword');
      fireEvent.click(screen.getByRole('button', { name: /change username/i }));

      await waitFor(() => {
        expect(screen.getByText(/username changed to "bob"/i)).toBeInTheDocument();
      });
      const closeButtons = screen.getAllByRole('button', { name: /close/i });
      fireEvent.click(closeButtons[closeButtons.length - 1]);
      await waitFor(() => {
        expect(screen.queryByText(/username changed to "bob"/i)).not.toBeInTheDocument();
      });
    } finally {
      spy.mockRestore();
    }
  });

  // ── username form – trimming ─────────────────────────────────────────────────

  it('passes the trimmed username to the API', async () => {
    const spy = vi
      .spyOn(authApi, 'changeUsername')
      .mockResolvedValueOnce({ username: 'bob' });
    try {
      renderAccordion();
      fillUsernameForm('  bob  ', 'mypassword');
      fireEvent.click(screen.getByRole('button', { name: /change username/i }));

      await waitFor(() => {
        expect(spy).toHaveBeenCalledWith('mypassword', 'bob');
      });
    } finally {
      spy.mockRestore();
    }
  });
});
