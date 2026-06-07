// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for LogsTab.tsx after its migration to TanStack Query.
 *
 * The application log-level read/write (`useLogLevel` / `useSetLogLevel`) and
 * the diagnostic-bundle export (`useExportDiagnostics`) now flow through
 * `../hooks/useLogLevel`, which call the `logs` service module. We mock that
 * service and render inside `makeWrapper`.
 *
 * The polling log viewer (`useLogViewer`) and the presentational children
 * (EventsTab / LogPane / ServiceStatusBar) are stubbed so the tests focus on
 * the migrated level-select + export controls. LogsTab defaults to the
 * "Events" sub-tab; we click a service tab ("All") to reveal those controls.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import LogsTab from '../LogsTab';
import { makeWrapper } from '../../../test/renderWithProviders';

const getLevel = vi.fn();
const setLevel = vi.fn();
const exportBundle = vi.fn();

vi.mock('../../../services/api/logs', () => ({
  loggingApi: {
    getLevel: () => getLevel(),
    setLevel: (level: string) => setLevel(level),
  },
  diagnosticsApi: {
    exportBundle: () => exportBundle(),
  },
}));

// Polling log viewer — stubbed with a minimal steady state.
vi.mock('../hooks/useLogViewer', () => ({
  useLogViewer: () => ({
    activeTab: 'all',
    setActiveTab: vi.fn(),
    lines: ['line one', 'line two'],
    totalLines: 2,
    status: { available: false, services: [] },
    loading: false,
    paused: false,
    togglePause: vi.fn(),
    error: null,
  }),
}));

vi.mock('../EventsTab', () => ({ default: () => <div data-testid="events-tab" /> }));
vi.mock('../components/LogPane', () => ({ LogPane: () => <div data-testid="log-pane" /> }));
vi.mock('../components/ServiceStatusBar', () => ({ ServiceStatusBar: () => <div data-testid="status-bar" /> }));

function renderTab() {
  render(<LogsTab settings={null} setSettings={vi.fn()} />, { wrapper: makeWrapper() });
}

/** Switch from the default Events sub-tab to the "All" service-log tab. */
function gotoServiceLogs() {
  fireEvent.click(screen.getByRole('tab', { name: 'All' }));
}

beforeEach(() => {
  vi.clearAllMocks();
  getLevel.mockResolvedValue({ level: 'INFO', numeric_level: 20, available_levels: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] });
  setLevel.mockResolvedValue({ success: true, old_level: 'INFO', new_level: 'DEBUG', message: 'ok' });
  exportBundle.mockResolvedValue(undefined);
});

describe('LogsTab', () => {
  it('reads the current log level on mount and renders the level select on the service tab', async () => {
    renderTab();
    await waitFor(() => expect(getLevel).toHaveBeenCalledTimes(1));

    gotoServiceLogs();
    // The level control appears once availableLevels is populated.
    await waitFor(() => expect(screen.getByText('Application Log Level')).toBeInTheDocument());
    expect(screen.getByText('INFO')).toBeInTheDocument();
  });

  it('changes the log level via the select and reflects the new value', async () => {
    renderTab();
    await waitFor(() => expect(getLevel).toHaveBeenCalled());
    gotoServiceLogs();
    await waitFor(() => expect(screen.getByText('Application Log Level')).toBeInTheDocument());

    fireEvent.mouseDown(screen.getByRole('combobox'));
    fireEvent.click(await screen.findByRole('option', { name: 'DEBUG' }));

    await waitFor(() => expect(setLevel).toHaveBeenCalledWith('DEBUG'));
    // setQueryData writes the new level back into the cache -> shown in select.
    await waitFor(() => expect(screen.getByText('DEBUG')).toBeInTheDocument());
  });

  it('exports the diagnostic bundle when the export button is clicked', async () => {
    renderTab();
    await waitFor(() => expect(getLevel).toHaveBeenCalled());
    gotoServiceLogs();

    fireEvent.click(await screen.findByRole('button', { name: /export diagnostic bundle/i }));

    await waitFor(() => expect(exportBundle).toHaveBeenCalledTimes(1));
  });

  it('shows an error alert when the diagnostic export fails', async () => {
    exportBundle.mockRejectedValue(new Error('export boom'));
    renderTab();
    await waitFor(() => expect(getLevel).toHaveBeenCalled());
    gotoServiceLogs();

    fireEvent.click(await screen.findByRole('button', { name: /export diagnostic bundle/i }));

    await waitFor(() => {
      expect(screen.getByText(/failed to export diagnostic bundle/i)).toBeInTheDocument();
    });
  });
});
