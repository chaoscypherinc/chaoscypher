// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import WorkflowBuilderPage from '../WorkflowBuilderPage/WorkflowBuilderPage';

vi.mock('../../services/api/client', () => installApiClientMock());

describe('WorkflowBuilderPage', () => {
  it('renders the new-workflow builder toolbar', async () => {
    render(
      <Routes>
        <Route path="/automations/builder" element={<WorkflowBuilderPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/automations/builder'] }) },
    );

    // For the new-workflow route there is no `:workflowId`, so the metadata
    // query stays disabled (no loading spinner) and the builder renders its
    // toolbar immediately with `workflow === null`. The toolbar's heading
    // therefore reads "New Workflow" — a stable structural marker that proves
    // the correct page rendered (a blank screen or the wrong page would not
    // expose this heading).
    expect(
      await screen.findByRole('heading', { name: 'New Workflow' }),
    ).toBeInTheDocument();

    // With no persisted workflow yet, the primary save action is labelled
    // "Create" (it becomes "Save" only once a workflow exists). Asserting the
    // accessible button confirms the toolbar's action strip rendered too.
    expect(screen.getByRole('button', { name: 'Create' })).toBeInTheDocument();
  });
});
