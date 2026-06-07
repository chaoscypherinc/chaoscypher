// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import NetworkAccessAccordion from '../NetworkAccessAccordion';
import type { Settings } from '../../../types';

function wrap(ui: React.ReactNode) {
  return <ThemeProvider theme={createTheme()}>{ui}</ThemeProvider>;
}

function makeSettings(over: Partial<NonNullable<Settings['security']>> = {}): Settings {
  return {
    security: {
      allow_external_access: false,
      allowed_hosts: ['localhost', '127.0.0.1', '::1'],
      ...over,
    },
  } as unknown as Settings;
}

describe('NetworkAccessAccordion', () => {
  it('is collapsed by default', () => {
    const setSettings = vi.fn();
    render(wrap(<NetworkAccessAccordion settings={makeSettings()} setSettings={setSettings} />));
    expect(screen.getByRole('button', { name: /network access/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('renders the toggle in the off state by default', () => {
    const setSettings = vi.fn();
    render(wrap(<NetworkAccessAccordion settings={makeSettings()} setSettings={setSettings} />));
    const toggle = screen.getByLabelText(/allow access from any host/i);
    expect(toggle).not.toBeChecked();
  });

  it('renders the toggle in the on state when allow_external_access=true', () => {
    const setSettings = vi.fn();
    render(
      wrap(
        <NetworkAccessAccordion
          settings={makeSettings({ allow_external_access: true })}
          setSettings={setSettings}
        />,
      ),
    );
    const toggle = screen.getByLabelText(/allow access from any host/i);
    expect(toggle).toBeChecked();
  });

  it('calls setSettings with security.allow_external_access flipped on toggle', () => {
    const setSettings = vi.fn();
    render(wrap(<NetworkAccessAccordion settings={makeSettings()} setSettings={setSettings} />));
    const toggle = screen.getByLabelText(/allow access from any host/i);
    fireEvent.click(toggle);
    expect(setSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        security: expect.objectContaining({ allow_external_access: true }),
      }),
    );
  });

  it('shows the manual host chips', () => {
    const setSettings = vi.fn();
    render(wrap(<NetworkAccessAccordion settings={makeSettings()} setSettings={setSettings} />));
    expect(screen.getByText('localhost')).toBeInTheDocument();
    expect(screen.getByText('127.0.0.1')).toBeInTheDocument();
    expect(screen.getByText('::1')).toBeInTheDocument();
  });

  it('shows the bypass caption when external access is on', () => {
    const setSettings = vi.fn();
    render(
      wrap(
        <NetworkAccessAccordion
          settings={makeSettings({ allow_external_access: true })}
          setSettings={setSettings}
        />,
      ),
    );
    expect(screen.getByText(/allow-list is bypassed/i)).toBeInTheDocument();
  });
});
