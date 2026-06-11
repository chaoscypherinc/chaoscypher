// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ChatMessageList scroll behavior (Phase 3b): auto-follow only while the
 * user is near the bottom; when scrolled up, the position is preserved and
 * a jump-to-latest FAB (with a new-content badge) appears instead.
 */

import { createRef } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { ThemeProvider, createTheme } from '@mui/material';
import ChatMessageList from '../ChatMessageList';
import type { ExtendedChatMessage } from '../types';

const theme = createTheme({ palette: { mode: 'dark' } });

const scrollIntoViewMock = vi.fn();

beforeEach(() => {
  scrollIntoViewMock.mockClear();
  window.HTMLElement.prototype.scrollIntoView = scrollIntoViewMock;
});

function msg(content: string, role: 'user' | 'assistant' = 'user'): ExtendedChatMessage {
  return { role, content } as ExtendedChatMessage;
}

function renderList(messages: ExtendedChatMessage[]) {
  const props = {
    messages,
    loading: false,
    contextInfo: null,
    onQuickAction: vi.fn(),
    messagesEndRef: createRef<HTMLDivElement>(),
  };
  const utils = render(
    <MemoryRouter>
      <ThemeProvider theme={theme}>
        <ChatMessageList {...props} />
      </ThemeProvider>
    </MemoryRouter>,
  );
  const rerenderList = (next: ExtendedChatMessage[]) =>
    utils.rerender(
      <MemoryRouter>
        <ThemeProvider theme={theme}>
          <ChatMessageList {...props} messages={next} />
        </ThemeProvider>
      </MemoryRouter>,
    );
  return { ...utils, rerenderList };
}

/** Make the scroll region report a scrolled-up position, then fire scroll. */
function scrollUp(region: HTMLElement) {
  Object.defineProperty(region, 'scrollHeight', { value: 1000, configurable: true });
  Object.defineProperty(region, 'clientHeight', { value: 400, configurable: true });
  Object.defineProperty(region, 'scrollTop', { value: 100, configurable: true, writable: true });
  fireEvent.scroll(region);
}

describe('ChatMessageList scroll behavior', () => {
  it('auto-follows new messages while near the bottom (no FAB)', () => {
    const { rerenderList } = renderList([msg('q1')]);
    scrollIntoViewMock.mockClear();
    rerenderList([msg('q1'), msg('a1', 'assistant')]);
    expect(scrollIntoViewMock).toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /jump to latest/i })).not.toBeInTheDocument();
  });

  it('preserves position and shows the FAB when scrolled up', () => {
    const { rerenderList } = renderList([msg('q1')]);
    scrollUp(screen.getByTestId('chat-scroll-region'));
    expect(screen.getByRole('button', { name: /jump to latest/i })).toBeInTheDocument();

    scrollIntoViewMock.mockClear();
    rerenderList([msg('q1'), msg('a1', 'assistant')]);
    expect(scrollIntoViewMock).not.toHaveBeenCalled();
  });

  it('FAB click jumps to the bottom and hides the FAB', () => {
    renderList([msg('q1')]);
    scrollUp(screen.getByTestId('chat-scroll-region'));

    scrollIntoViewMock.mockClear();
    fireEvent.click(screen.getByRole('button', { name: /jump to latest/i }));
    expect(scrollIntoViewMock).toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /jump to latest/i })).not.toBeInTheDocument();
  });
});
