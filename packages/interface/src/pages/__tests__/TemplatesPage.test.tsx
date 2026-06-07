// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import TemplatesPage from '../TemplatesPage';

vi.mock('../../services/api/client', () => installApiClientMock());

describe('TemplatesPage', () => {
  it('renders the page heading and description once loaded', async () => {
    render(
      <Routes>
        <Route path="/templates" element={<TemplatesPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/templates'] }) },
    );

    // The page starts in a loading state, then resolves to the templates view.
    // The <h4> "Templates" heading only renders after loading completes, so
    // finding it proves the correct page reached its loaded state — this would
    // fail on a blank screen, a stuck spinner, or the wrong page rendering.
    const heading = await screen.findByRole('heading', { name: 'Templates' });
    expect(heading).toBeInTheDocument();

    // A static, structural label that is independent of any API-loaded data,
    // confirming the page body (not just a header shell) rendered.
    expect(
      screen.getByText(
        'Templates define the structure of items and links in your knowledge graph.',
      ),
    ).toBeInTheDocument();

    // The primary call-to-action button is always present in the loaded view.
    expect(
      screen.getByRole('button', { name: /create template/i }),
    ).toBeInTheDocument();
  });
});
