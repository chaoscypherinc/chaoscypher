// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import LoginPage from '../LoginPage';

vi.mock('../../services/api/client', () => installApiClientMock());

describe('LoginPage', () => {
  it('renders the sign-in card with heading, fields, and submit button', async () => {
    render(
      <Routes>
        <Route path="/login" element={<LoginPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/login'] }) },
    );

    // Brand heading proves the correct page mounted (not a blank/wrong screen).
    expect(
      await screen.findByRole('heading', { name: /chaos cypher/i }),
    ).toBeInTheDocument();

    // The login form's stable structural controls. The username field is a
    // textbox; the password field's label is start-anchored so it does not
    // also match the visibility toggle's "Show password" aria-label.
    expect(screen.getByRole('textbox', { name: /username/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/^password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument();
  });
});
