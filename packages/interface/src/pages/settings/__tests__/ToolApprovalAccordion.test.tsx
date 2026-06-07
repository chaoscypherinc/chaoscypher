// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactNode } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material';
import ToolApprovalAccordion from '../ToolApprovalAccordion';
import type { Settings } from '../../../types';

const theme = createTheme({ palette: { mode: 'dark' } });
function Wrapper({ children }: { children: ReactNode }) {
  return <ThemeProvider theme={theme}>{children}</ThemeProvider>;
}

/** Build a Settings stub with a given tool_approval (or none). */
function settingsWith(toolApproval?: string): Settings {
  return {
    chat: toolApproval ? { tool_approval: toolApproval } : undefined,
  } as unknown as Settings;
}

function renderAccordion(toolApproval?: string) {
  const setSettings = vi.fn();
  render(
    <ToolApprovalAccordion settings={settingsWith(toolApproval)} setSettings={setSettings} />,
    { wrapper: Wrapper },
  );
  return { setSettings };
}

describe('ToolApprovalAccordion', () => {
  it('is collapsed by default', () => {
    renderAccordion('ask-on-write');
    expect(screen.getByRole('button', { name: /tool call approval/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('shows the current mode on a chip without expanding', () => {
    renderAccordion('ask-on-write');
    expect(screen.getByText('Ask on write')).toBeInTheDocument();
  });

  it('falls back to "Never ask" (warning) when chat settings are absent', () => {
    renderAccordion(undefined);
    const chipLabel = screen.getByText('Never ask');
    // The MuiChip root carries the color class; the label is its child.
    const chipRoot = chipLabel.closest('.MuiChip-root');
    expect(chipRoot).not.toBeNull();
    expect(chipRoot).toHaveClass('MuiChip-colorWarning');
  });

  it('shows the "Always ask" chip when set to always-ask', () => {
    renderAccordion('always-ask');
    // "Always ask" is also the Select's displayed value (mounted but collapsed),
    // so scope the assertion to the chip inside the summary button.
    const summary = screen.getByRole('button', { name: /tool call approval/i });
    expect(within(summary).getByText('Always ask')).toBeInTheDocument();
  });

  it('reveals the approval-mode select when expanded', () => {
    renderAccordion('never-ask');
    fireEvent.click(screen.getByRole('button', { name: /tool call approval/i }));
    expect(screen.getByRole('combobox', { name: /approval mode/i })).toBeInTheDocument();
  });

  it('calls setSettings with the new mode when the select changes', () => {
    const { setSettings } = renderAccordion('never-ask');
    fireEvent.click(screen.getByRole('button', { name: /tool call approval/i }));

    // Open the MUI Select listbox and pick a different option.
    fireEvent.mouseDown(screen.getByRole('combobox', { name: /approval mode/i }));
    const listbox = within(screen.getByRole('listbox'));
    fireEvent.click(listbox.getByText('Always ask'));

    expect(setSettings).toHaveBeenCalledTimes(1);
    const next = setSettings.mock.calls[0][0] as Settings;
    expect(next.chat?.tool_approval).toBe('always-ask');
  });
});
