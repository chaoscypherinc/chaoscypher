// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import SourcePage from '../SourcePage';

vi.mock('../../services/api/client', () => installApiClientMock());

describe('SourcePage', () => {
  it('renders the loaded source detail layout for a stub source id', async () => {
    render(
      <Routes>
        <Route path="/sources/:id" element={<SourcePage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/sources/test-source-1'] }) },
    );

    // The source query resolves to the stub object, so the page leaves its
    // loading state and renders the detail layout. These structural elements
    // only exist once `source` is truthy — they are absent during loading,
    // on the "Source not found" fallback, and if the wrong page mounted. The
    // tab strip's "Overview" tab is a static label that always renders for a
    // loaded source.
    expect(
      await screen.findByRole('tab', { name: /overview/i }),
    ).toBeInTheDocument();

    // The header's icon-only back button (aria-label="Back") likewise only
    // mounts for a loaded source — proving the detail header, not a blank
    // screen or error fallback, rendered.
    expect(screen.getByRole('button', { name: /back/i })).toBeInTheDocument();
  });
});
