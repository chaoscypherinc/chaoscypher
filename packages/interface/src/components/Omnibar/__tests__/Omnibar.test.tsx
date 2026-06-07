// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for the Omnibar command-palette shell.
 *
 * Strategy: the Omnibar delegates result rendering to mode children
 * (SearchMode / CommandMode / ChatMode / HelpMode / StateZero). We mock
 * each child as a stub that records the props it received, so we can assert
 * (a) which mode rendered for a given query/prefix, and (b) the query and
 * selectedIndex the shell passes down. Keyboard handling, prefix detection,
 * mode activation, and the onItemCount → selectedIndex clamp are exercised
 * through the real shell against those stubs.
 */

import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, type Mock } from 'vitest';
import type { ModeResultsProps } from '../types';

// ---------------------------------------------------------------------------
// Capture the props handed to each mode child. The shell wires onItemCount
// into each mode; we keep a reference so a test can simulate a mode reporting
// its result count back to the shell (which drives arrow-key clamping).
// ---------------------------------------------------------------------------
interface CapturedMode {
  query?: string;
  selectedIndex?: number;
  onItemCount?: (count: number) => void;
  onClose?: () => void;
  onActivateMode?: (prefix: string) => void;
}

const captured: Record<string, CapturedMode> = {};

function recordSearch(p: ModeResultsProps) {
  captured.search = p;
  return (
    <div data-testid="mode-search" data-query={p.query} data-selected={String(p.selectedIndex)} />
  );
}
function recordCommand(p: ModeResultsProps) {
  captured.command = p;
  return (
    <div data-testid="mode-command" data-query={p.query} data-selected={String(p.selectedIndex)} />
  );
}
function recordChat(p: ModeResultsProps) {
  captured.chat = p;
  return (
    <div data-testid="mode-chat" data-query={p.query} data-selected={String(p.selectedIndex)} />
  );
}
function recordHelp(p: ModeResultsProps) {
  captured.help = p;
  return (
    <div data-testid="mode-help" data-query={p.query} data-selected={String(p.selectedIndex)} />
  );
}

interface StateZeroStubProps {
  onClose: () => void;
  selectedIndex: number;
  onItemCount: (count: number) => void;
  onActivateMode: (prefix: string) => void;
}
function recordStateZero(p: StateZeroStubProps) {
  captured.stateZero = p;
  return (
    <div data-testid="mode-statezero" data-selected={String(p.selectedIndex)}>
      <button type="button" data-testid="sz-activate-command" onClick={() => p.onActivateMode('>')}>
        activate-command
      </button>
    </div>
  );
}

vi.mock('../modes/SearchMode', () => ({ SearchMode: (p: ModeResultsProps) => recordSearch(p) }));
vi.mock('../modes/CommandMode', () => ({ CommandMode: (p: ModeResultsProps) => recordCommand(p) }));
vi.mock('../modes/ChatMode', () => ({ ChatMode: (p: ModeResultsProps) => recordChat(p) }));
vi.mock('../modes/HelpMode', () => ({ HelpMode: (p: ModeResultsProps) => recordHelp(p) }));
vi.mock('../StateZero', () => ({ StateZero: (p: StateZeroStubProps) => recordStateZero(p) }));

// ---------------------------------------------------------------------------
// Import component AFTER mocking
// ---------------------------------------------------------------------------
import { Omnibar } from '../Omnibar';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
interface RenderOpts {
  isOpen?: boolean;
  onClose?: Mock<() => void>;
  initialQuery?: string;
  initialMode?: string;
  openKey?: number;
  withAnchor?: boolean;
}

function renderOmnibar(opts: RenderOpts = {}) {
  const onClose = opts.onClose ?? vi.fn<() => void>();
  const isOpen = opts.isOpen ?? true;
  const anchorEl = opts.withAnchor === false ? null : document.createElement('div');
  if (anchorEl) document.body.appendChild(anchorEl);
  const result = render(
    <Omnibar
      isOpen={isOpen}
      onClose={onClose}
      initialQuery={opts.initialQuery}
      initialMode={opts.initialMode}
      openKey={opts.openKey ?? 1}
      anchorEl={anchorEl}
    />,
  );
  return { onClose, anchorEl, ...result };
}

function getInput(): HTMLInputElement {
  return screen.getByRole('textbox') as HTMLInputElement;
}

beforeEach(() => {
  for (const key of Object.keys(captured)) delete captured[key];
  localStorage.clear();
});

describe('<Omnibar />', () => {
  it('renders nothing when isOpen is false', () => {
    renderOmnibar({ isOpen: false });
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mode-statezero')).not.toBeInTheDocument();
  });

  it('renders nothing when there is no anchor element', () => {
    renderOmnibar({ withAnchor: false });
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('shows StateZero for an empty query when open', () => {
    renderOmnibar();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    expect(screen.getByTestId('mode-statezero')).toBeInTheDocument();
    expect(screen.queryByTestId('mode-search')).not.toBeInTheDocument();
  });

  it('switches to SearchMode when a plain query is typed', () => {
    renderOmnibar();
    fireEvent.change(getInput(), { target: { value: 'hello world' } });
    const searchEl = screen.getByTestId('mode-search');
    expect(searchEl).toBeInTheDocument();
    expect(searchEl).toHaveAttribute('data-query', 'hello world');
    expect(screen.queryByTestId('mode-statezero')).not.toBeInTheDocument();
  });

  it('activates CommandMode when ">" is typed into an empty input', () => {
    renderOmnibar();
    fireEvent.change(getInput(), { target: { value: '>' } });
    expect(screen.getByTestId('mode-command')).toBeInTheDocument();
    // Prefix is consumed: the query handed to the mode is empty, not ">".
    expect(screen.getByTestId('mode-command')).toHaveAttribute('data-query', '');
  });

  it('activates ChatMode when "/" is typed into an empty input', () => {
    renderOmnibar();
    fireEvent.change(getInput(), { target: { value: '/' } });
    expect(screen.getByTestId('mode-chat')).toBeInTheDocument();
  });

  it('activates HelpMode when "?" is typed into an empty input', () => {
    renderOmnibar();
    fireEvent.change(getInput(), { target: { value: '?' } });
    expect(screen.getByTestId('mode-help')).toBeInTheDocument();
  });

  it('honors initialQuery on open (SearchMode with that query)', () => {
    renderOmnibar({ initialQuery: 'preloaded' });
    const searchEl = screen.getByTestId('mode-search');
    expect(searchEl).toHaveAttribute('data-query', 'preloaded');
    expect(getInput().value).toBe('preloaded');
  });

  it('honors initialMode on open (renders the matching mode child)', () => {
    renderOmnibar({ initialMode: '>' });
    expect(screen.getByTestId('mode-command')).toBeInTheDocument();
    // The mode chip label is rendered instead of the search icon.
    expect(screen.getByText('COMMAND')).toBeInTheDocument();
  });

  it('ignores an unknown initialMode prefix and falls back to StateZero', () => {
    renderOmnibar({ initialMode: '!' });
    expect(screen.getByTestId('mode-statezero')).toBeInTheDocument();
  });

  it('calls onClose when Escape is pressed', () => {
    const { onClose } = renderOmnibar();
    fireEvent.keyDown(getInput(), { key: 'Escape' });
    // Both the shell's own keydown handler and MUI's Modal escape listener
    // fire here (the keydown bubbles to the Modal); we only care that the
    // close request reached the parent.
    expect(onClose).toHaveBeenCalled();
  });

  it('clears the active mode on Backspace when the query is empty', () => {
    renderOmnibar({ initialMode: '>' });
    expect(screen.getByTestId('mode-command')).toBeInTheDocument();
    fireEvent.keyDown(getInput(), { key: 'Backspace' });
    // Back in state zero (no mode, empty query).
    expect(screen.getByTestId('mode-statezero')).toBeInTheDocument();
    expect(screen.queryByTestId('mode-command')).not.toBeInTheDocument();
  });

  it('does not advance selectedIndex with ArrowDown when no items reported', () => {
    renderOmnibar({ initialQuery: 'q' });
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-selected', '0');
    fireEvent.keyDown(getInput(), { key: 'ArrowDown' });
    // count is 0 → stays at 0.
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-selected', '0');
  });

  it('moves selectedIndex with ArrowDown/ArrowUp after a mode reports its item count', () => {
    renderOmnibar({ initialQuery: 'q' });
    // The mode reports 3 results back to the shell.
    act(() => {
      captured.search.onItemCount?.(3);
    });

    fireEvent.keyDown(getInput(), { key: 'ArrowDown' });
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-selected', '1');

    fireEvent.keyDown(getInput(), { key: 'ArrowDown' });
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-selected', '2');

    // Wraps around past the end back to 0.
    fireEvent.keyDown(getInput(), { key: 'ArrowDown' });
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-selected', '0');

    // ArrowUp wraps to the last item.
    fireEvent.keyDown(getInput(), { key: 'ArrowUp' });
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-selected', '2');
  });

  it('clamps selectedIndex when the reported item count shrinks below it', () => {
    renderOmnibar({ initialQuery: 'q' });
    act(() => {
      captured.search.onItemCount?.(5);
    });
    fireEvent.keyDown(getInput(), { key: 'ArrowDown' });
    fireEvent.keyDown(getInput(), { key: 'ArrowDown' });
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-selected', '2');

    // Results shrink to 2 items → selectedIndex (2) is clamped to count-1 (1).
    act(() => {
      captured.search.onItemCount?.(2);
    });
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-selected', '1');
  });

  it('resets selectedIndex to 0 when the query changes', () => {
    renderOmnibar({ initialQuery: 'q' });
    act(() => {
      captured.search.onItemCount?.(3);
    });
    fireEvent.keyDown(getInput(), { key: 'ArrowDown' });
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-selected', '1');

    fireEvent.change(getInput(), { target: { value: 'different' } });
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-selected', '0');
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-query', 'different');
  });

  it('lets StateZero activate a mode via onActivateMode', () => {
    renderOmnibar();
    fireEvent.click(screen.getByTestId('sz-activate-command'));
    expect(screen.getByTestId('mode-command')).toBeInTheDocument();
    expect(screen.queryByTestId('mode-statezero')).not.toBeInTheDocument();
  });

  it('passes onClose down to the active mode child', () => {
    const { onClose } = renderOmnibar({ initialQuery: 'q' });
    expect(captured.search.onClose).toBe(onClose);
  });

  it('shows the inline hint banner the first time and hides it after dismiss', () => {
    renderOmnibar();
    const dismiss = screen.getByText('✕');
    expect(dismiss).toBeInTheDocument();
    fireEvent.click(dismiss);
    expect(screen.queryByText('✕')).not.toBeInTheDocument();
    // Dismissal is persisted so the banner stays gone.
    expect(localStorage.getItem('chaoscypher-omnibar-hint-count')).toBe('dismissed');
  });

  it('does not show the hint banner once dismissed in storage', () => {
    localStorage.setItem('chaoscypher-omnibar-hint-count', 'dismissed');
    renderOmnibar();
    expect(screen.queryByText('✕')).not.toBeInTheDocument();
  });

  it('stops showing the hint banner after the max show count is reached', () => {
    localStorage.setItem('chaoscypher-omnibar-hint-count', '3');
    renderOmnibar();
    expect(screen.queryByText('✕')).not.toBeInTheDocument();
  });

  it('does not treat a prefix typed mid-query as a mode switch', () => {
    renderOmnibar({ initialQuery: 'abc' });
    // Already in search mode with a non-empty query; typing a value that
    // happens to equal a prefix should NOT activate a mode (guard requires
    // an empty query + no active mode).
    fireEvent.change(getInput(), { target: { value: '>' } });
    expect(screen.getByTestId('mode-search')).toBeInTheDocument();
    expect(screen.getByTestId('mode-search')).toHaveAttribute('data-query', '>');
    expect(screen.queryByTestId('mode-command')).not.toBeInTheDocument();
  });
});
