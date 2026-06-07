// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { ThemeProvider, createTheme } from '@mui/material';
import MiniSystemStatus from '../MiniSystemStatus';

const navigateSpy = vi.fn();
vi.mock('react-router', async (orig) => {
  const actual = await orig<typeof import('react-router')>();
  return { ...actual, useNavigate: () => navigateSpy };
});

// Mutable holder so individual describe blocks can swap the dashboard payload
// without re-mocking (vi.mock is hoisted; we mutate the object instead).
const dashboardHolder = {
  data: {
    counts: { knowledge_nodes: 0, links: 0, templates: 0, workflows: 0, awaiting_confirmation: 3 },
    llm: null,
    queue: [],
    workflows: null,
    processing: { paused: false, paused_at: null, reason: null },
  },
};

vi.mock('../../../contexts/useDashboard', () => ({
  useDashboard: () => ({ data: dashboardHolder.data, loading: false, refresh: vi.fn() }),
}));
vi.mock('../../../hooks/useSystemHealth', () => ({
  useSystemHealth: () => ({ health: { checks: {} }, loading: false, hasErrors: false, hasWarnings: false }),
}));

const theme = createTheme({ palette: { mode: 'dark' } });
function renderWidget() {
  return render(
    <MemoryRouter>
      <ThemeProvider theme={theme}>
        <MiniSystemStatus />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('MiniSystemStatus — awaiting-confirmation badge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset to the non-zero default before each test in this suite.
    dashboardHolder.data = {
      counts: { knowledge_nodes: 0, links: 0, templates: 0, workflows: 0, awaiting_confirmation: 3 },
      llm: null,
      queue: [],
      workflows: null,
      processing: { paused: false, paused_at: null, reason: null },
    };
  });

  it('shows the awaiting count and navigates to the filtered sources view on click', () => {
    renderWidget();
    const badge = screen.getByRole('button', { name: /3 sources? awaiting confirmation/i });
    fireEvent.click(badge);
    expect(navigateSpy).toHaveBeenCalledWith('/sources?status=awaiting_confirmation');
  });

  it('hides the badge button when awaiting_confirmation count is zero', () => {
    dashboardHolder.data = {
      counts: { knowledge_nodes: 0, links: 0, templates: 0, workflows: 0, awaiting_confirmation: 0 },
      llm: null,
      queue: [],
      workflows: null,
      processing: { paused: false, paused_at: null, reason: null },
    };
    renderWidget();
    expect(
      screen.queryByRole('button', { name: /awaiting confirmation/i }),
    ).not.toBeInTheDocument();
  });
});
