// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for GeneralSettingsTab.tsx, focused on the inline TLSAccordion that
 * was migrated to TanStack Query.
 *
 * The TLS status read (`useTlsStatus`) and the enable/disable toggle
 * (`useToggleTls`) now flow through `../hooks/useTlsStatus`, which call the
 * `tls` service module. We mock that service and render inside `makeWrapper`.
 * The other sections (Import/Export, Network Access) are heavy children with
 * their own wiring; they're stubbed so these tests target the TLS behaviour.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import GeneralSettingsTab from '../GeneralSettingsTab';
import { makeWrapper } from '../../../test/renderWithProviders';
import type { Settings } from '../../../types';

const getStatus = vi.fn();
const enableSelfSigned = vi.fn();
const disable = vi.fn();

vi.mock('../../../services/api/tls', () => ({
  tlsApi: {
    getStatus: () => getStatus(),
    enableSelfSigned: (hostname?: string) => enableSelfSigned(hostname),
    disable: () => disable(),
  },
}));

vi.mock('../ImportExportSection', () => ({ default: () => <div data-testid="import-export" /> }));
vi.mock('../NetworkAccessAccordion', () => ({ default: () => <div data-testid="network-access" /> }));
// Stub the account/API-keys accordions: they have their own heavy wiring
// (auth context, API-keys query). These stubs capture the autoFocus prop so we
// can assert the focusSection → autoFocus deep-link plumbing.
vi.mock('../AccountAccordion', () => ({
  default: (props: { autoFocus?: boolean }) => (
    <div data-testid="account-accordion" data-autofocus={String(!!props.autoFocus)} />
  ),
}));
vi.mock('../ApiKeysAccordion', () => ({
  default: (props: { autoFocus?: boolean }) => (
    <div data-testid="api-keys-accordion" data-autofocus={String(!!props.autoFocus)} />
  ),
}));

function makeSettings(): Settings {
  return {
    dark_mode: true,
    auto_enable: false,
  } as unknown as Settings;
}

const noop = vi.fn();

function renderTab(focusSection?: 'account' | 'api-keys' | null) {
  const setSettings = vi.fn();
  render(
    <GeneralSettingsTab
      settings={makeSettings()}
      setSettings={setSettings}
      focusSection={focusSection}
      importing={false}
      exporting={false}
      importSuccess={false}
      importError={null}
      setImportError={noop}
      fileInputRef={{ current: null }}
      handleExport={async () => {}}
      handleImport={async () => {}}
      exportOptions={{
        includeTemplates: true,
        includeKnowledge: true,
        includeLenses: true,
        includeWorkflows: true,
        includeSources: true,
        includeEmbeddings: false,
      }}
      setExportOptions={noop}
    />,
    { wrapper: makeWrapper() },
  );
  return { setSettings };
}

beforeEach(() => {
  vi.clearAllMocks();
  getStatus.mockResolvedValue({ enabled: false });
  enableSelfSigned.mockResolvedValue({ status: 'ok', mode: 'selfsigned' });
  disable.mockResolvedValue({ status: 'ok' });
});

describe('GeneralSettingsTab — TLS accordion', () => {
  it('reads TLS status on mount and shows the Disabled chip', async () => {
    renderTab();
    await waitFor(() => expect(getStatus).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText('Disabled')).toBeInTheDocument());
    // Disabled -> the action button offers to Enable.
    expect(screen.getByRole('button', { name: 'Enable' })).toBeInTheDocument();
  });

  it('shows the Enabled chip + Disable button when TLS is already enabled', async () => {
    getStatus.mockResolvedValue({ enabled: true });
    renderTab();
    await waitFor(() => expect(screen.getByText('Enabled')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Disable' })).toBeInTheDocument();
  });

  it('enables self-signed TLS and shows the restart-required success message', async () => {
    renderTab();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Enable' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Enable' }));

    await waitFor(() => expect(enableSelfSigned).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(screen.getByText(/self-signed tls enabled/i)).toBeInTheDocument();
    });
    // After invalidation the status refetches; the next getStatus reflects on.
    expect(getStatus.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('disables TLS and shows the restart-required success message', async () => {
    getStatus.mockResolvedValue({ enabled: true });
    renderTab();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Disable' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Disable' }));

    await waitFor(() => expect(disable).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(screen.getByText(/tls disabled/i)).toBeInTheDocument();
    });
  });

  it('surfaces an error message when the toggle fails', async () => {
    enableSelfSigned.mockRejectedValue(new Error('toggle boom'));
    renderTab();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Enable' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Enable' }));

    await waitFor(() => {
      expect(screen.getByText(/toggle boom/i)).toBeInTheDocument();
    });
  });

  it('renders an Unknown state (no chip) while the status query is unresolved', async () => {
    // Never resolves -> tlsEnabled stays null -> no chip, no toggle button.
    getStatus.mockReturnValue(new Promise(() => {}));
    renderTab();
    expect(screen.queryByText('Enabled')).not.toBeInTheDocument();
    expect(screen.queryByText('Disabled')).not.toBeInTheDocument();
  });
});

describe('GeneralSettingsTab — Account / API keys sections', () => {
  /** True when `a` appears before `b` in document order. */
  function precedes(a: HTMLElement, b: HTMLElement): boolean {
    return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  }

  it('renders Account then API keys, both above Import/Export and Network access', () => {
    renderTab();
    const account = screen.getByTestId('account-accordion');
    const apiKeys = screen.getByTestId('api-keys-accordion');
    const importExport = screen.getByTestId('import-export');
    const network = screen.getByTestId('network-access');

    expect(precedes(account, apiKeys)).toBe(true);
    expect(precedes(apiKeys, importExport)).toBe(true);
    expect(precedes(importExport, network)).toBe(true);
  });

  it('does not auto-focus either section when no focusSection is given', () => {
    renderTab();
    expect(screen.getByTestId('account-accordion')).toHaveAttribute('data-autofocus', 'false');
    expect(screen.getByTestId('api-keys-accordion')).toHaveAttribute('data-autofocus', 'false');
  });

  it('forwards focusSection="account" as autoFocus to the Account accordion only', () => {
    renderTab('account');
    expect(screen.getByTestId('account-accordion')).toHaveAttribute('data-autofocus', 'true');
    expect(screen.getByTestId('api-keys-accordion')).toHaveAttribute('data-autofocus', 'false');
  });

  it('forwards focusSection="api-keys" as autoFocus to the API keys accordion only', () => {
    renderTab('api-keys');
    expect(screen.getByTestId('account-accordion')).toHaveAttribute('data-autofocus', 'false');
    expect(screen.getByTestId('api-keys-accordion')).toHaveAttribute('data-autofocus', 'true');
  });
});
