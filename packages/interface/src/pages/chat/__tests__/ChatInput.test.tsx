// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ChatInput — the Send button morphs into Stop while the assistant is
 * generating (Phase 2 stop/cancel), with an Esc shortcut and a disabled
 * "stopping" state between the click and the turn actually ending.
 */

import { createRef } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material';
import ChatInput from '../ChatInput';

const theme = createTheme({ palette: { mode: 'dark' } });

function renderInput(overrides: Partial<React.ComponentProps<typeof ChatInput>> = {}) {
  const props = {
    input: '',
    loading: false,
    contextInfo: null,
    inputRef: createRef<HTMLInputElement>(),
    onInputChange: vi.fn(),
    onSend: vi.fn(),
    onStop: vi.fn(),
    stopping: false,
    ...overrides,
  };
  render(
    <ThemeProvider theme={theme}>
      <ChatInput {...props} />
    </ThemeProvider>,
  );
  return props;
}

describe('ChatInput stop/cancel', () => {
  it('shows Send when idle', () => {
    renderInput({ input: 'hello' });
    expect(screen.getByRole('button', { name: 'Send (Enter)' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Stop generating/ })).not.toBeInTheDocument();
  });

  it('morphs into Stop while loading and fires onStop on click', () => {
    const props = renderInput({ loading: true });
    const stop = screen.getByRole('button', { name: 'Stop generating (Esc)' });
    fireEvent.click(stop);
    expect(props.onStop).toHaveBeenCalledTimes(1);
    expect(props.onSend).not.toHaveBeenCalled();
  });

  it('fires onStop on Escape in the text field while loading', () => {
    const props = renderInput({ loading: true });
    const field = screen.getByPlaceholderText(/Ask me anything/);
    fireEvent.keyDown(field, { key: 'Escape' });
    expect(props.onStop).toHaveBeenCalledTimes(1);
  });

  it('ignores Escape when idle', () => {
    const props = renderInput({ loading: false });
    const field = screen.getByPlaceholderText(/Ask me anything/);
    fireEvent.keyDown(field, { key: 'Escape' });
    expect(props.onStop).not.toHaveBeenCalled();
  });

  it('disables the Stop button while stopping', () => {
    const props = renderInput({ loading: true, stopping: true });
    const stop = screen.getByRole('button', { name: 'Stopping' });
    expect(stop).toBeDisabled();
    fireEvent.click(stop);
    expect(props.onStop).not.toHaveBeenCalled();
    // Esc is disarmed too while a stop is already in flight.
    const field = screen.getByPlaceholderText(/Ask me anything/);
    fireEvent.keyDown(field, { key: 'Escape' });
    expect(props.onStop).not.toHaveBeenCalled();
  });

  it('keeps the plain disabled Send when no onStop is provided', () => {
    render(
      <ThemeProvider theme={theme}>
        <ChatInput
          input=""
          loading={true}
          contextInfo={null}
          inputRef={createRef<HTMLInputElement>()}
          onInputChange={vi.fn()}
          onSend={vi.fn()}
        />
      </ThemeProvider>,
    );
    expect(screen.getByRole('button', { name: 'Send (Enter)' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /Stop generating/ })).not.toBeInTheDocument();
  });
});
