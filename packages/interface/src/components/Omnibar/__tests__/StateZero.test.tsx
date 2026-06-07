// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for the StateZero omnibar component — recent items + quick actions.
 */
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach, type Mock } from 'vitest';
import { MemoryRouter } from 'react-router';
import { ThemeProvider, createTheme } from '@mui/material';
import type { ReactNode } from 'react';
import { StateZero } from '../StateZero';
import { UploadDialogContext } from '../../../contexts/UploadDialogContext';
import type { RecentItem } from '../types';

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

const mockNavigate = vi.fn<() => void>();

vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// The real useRecentItems hook uses useSyncExternalStore + localStorage.
// We mock at the module level so tests control what items are returned.
const mockItems: RecentItem[] = [];

vi.mock('../useRecentItems', () => ({
  useRecentItems: () => ({
    items: mockItems,
    addRecentItem: vi.fn<() => void>(),
    clearRecentItems: vi.fn<() => void>(),
  }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const theme = createTheme({ palette: { mode: 'dark' } });

interface RenderOptions {
  onClose?: Mock<() => void>;
  selectedIndex?: number;
  onItemCount?: Mock<(count: number) => void>;
  onActivateMode?: Mock<(prefix: string) => void>;
  openUploadDialog?: Mock<() => void>;
}

function renderStateZero(opts: RenderOptions = {}) {
  const onClose = opts.onClose ?? vi.fn<() => void>();
  const selectedIndex = opts.selectedIndex ?? 0;
  const onItemCount = opts.onItemCount ?? vi.fn<(count: number) => void>();
  const onActivateMode = opts.onActivateMode ?? vi.fn<(prefix: string) => void>();
  const openUploadDialog = opts.openUploadDialog ?? vi.fn<() => void>();

  const Wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter>
      <ThemeProvider theme={theme}>
        <UploadDialogContext.Provider value={{ openUploadDialog }}>
          {children}
        </UploadDialogContext.Provider>
      </ThemeProvider>
    </MemoryRouter>
  );

  const result = render(
    <StateZero
      onClose={onClose}
      selectedIndex={selectedIndex}
      onItemCount={onItemCount}
      onActivateMode={onActivateMode}
    />,
    { wrapper: Wrapper },
  );

  return { onClose, onItemCount, onActivateMode, openUploadDialog, mockNavigate, ...result };
}

// Seed recent items into the mutable array, then clean up.
function setRecentItems(items: RecentItem[]) {
  mockItems.splice(0, mockItems.length, ...items);
}

function clearRecentItems() {
  mockItems.splice(0, mockItems.length);
}

const sampleEntity: RecentItem = {
  id: 'ent-1',
  type: 'entity',
  title: 'Test Entity',
  subtitle: 'entity subtitle',
  icon: '🔵',
  timestamp: 1000,
};

const sampleSource: RecentItem = {
  id: 'src-1',
  type: 'source',
  title: 'Test Source',
  subtitle: 'source subtitle',
  icon: '📄',
  timestamp: 2000,
};

const sampleChat: RecentItem = {
  id: 'chat-1',
  type: 'chat',
  title: 'Test Chat',
  subtitle: 'chat subtitle',
  icon: '💬',
  timestamp: 3000,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('StateZero', () => {
  beforeEach(() => {
    clearRecentItems();
    mockNavigate.mockReset();
  });

  afterEach(() => {
    clearRecentItems();
  });

  // -------------------------------------------------------------------------
  // Rendering — quick actions always present
  // -------------------------------------------------------------------------

  describe('quick actions section', () => {
    it('always renders Quick Actions heading', () => {
      renderStateZero();
      expect(screen.getByText('Quick Actions')).toBeInTheDocument();
    });

    it('renders New Chat quick action', () => {
      renderStateZero();
      expect(screen.getByText('New Chat')).toBeInTheDocument();
    });

    it('renders Import Source quick action', () => {
      renderStateZero();
      expect(screen.getByText('Import Source')).toBeInTheDocument();
    });

    it('renders Explore Graph quick action', () => {
      renderStateZero();
      expect(screen.getByText('Explore Graph')).toBeInTheDocument();
    });

    it('shows the / hint for New Chat', () => {
      renderStateZero();
      expect(screen.getByText('/')).toBeInTheDocument();
    });

    it('shows the G hint for Explore Graph', () => {
      renderStateZero();
      expect(screen.getByText('G')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Rendering — empty recent items
  // -------------------------------------------------------------------------

  describe('with no recent items', () => {
    it('does not render the Recent section heading', () => {
      renderStateZero();
      expect(screen.queryByText('Recent')).not.toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Rendering — with recent items
  // -------------------------------------------------------------------------

  describe('with recent items', () => {
    beforeEach(() => {
      setRecentItems([sampleEntity, sampleSource, sampleChat]);
    });

    it('renders the Recent heading', () => {
      renderStateZero();
      expect(screen.getByText('Recent')).toBeInTheDocument();
    });

    it('renders recent item titles', () => {
      renderStateZero();
      expect(screen.getByText('Test Entity')).toBeInTheDocument();
      expect(screen.getByText('Test Source')).toBeInTheDocument();
      expect(screen.getByText('Test Chat')).toBeInTheDocument();
    });

    it('renders the item type as the hint label', () => {
      renderStateZero();
      expect(screen.getByText('entity')).toBeInTheDocument();
      expect(screen.getByText('source')).toBeInTheDocument();
      expect(screen.getByText('chat')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // onItemCount reporting
  // -------------------------------------------------------------------------

  describe('onItemCount callback', () => {
    it('reports 3 (just quick actions) when no recent items', async () => {
      vi.useFakeTimers();
      const onItemCount = vi.fn<(count: number) => void>();
      renderStateZero({ onItemCount });
      // onItemCount is called via requestAnimationFrame — flush via fake timers
      await act(async () => { vi.runAllTimers(); });
      vi.useRealTimers();
      // 0 recent + 3 quick actions = 3
      expect(onItemCount).toHaveBeenCalledWith(3);
    });

    it('reports recent + quick-action count together', async () => {
      vi.useFakeTimers();
      setRecentItems([sampleEntity, sampleSource]);
      const onItemCount = vi.fn<(count: number) => void>();
      renderStateZero({ onItemCount });
      await act(async () => { vi.runAllTimers(); });
      vi.useRealTimers();
      // 2 recent + 3 quick actions = 5
      expect(onItemCount).toHaveBeenCalledWith(5);
    });
  });

  // -------------------------------------------------------------------------
  // selectedIndex highlight
  // -------------------------------------------------------------------------

  describe('selectedIndex highlighting', () => {
    it('marks the row at selectedIndex=0 as data-selected', () => {
      setRecentItems([sampleEntity]);
      renderStateZero({ selectedIndex: 0 });
      // index 0 = first recent item row → should have data-selected attribute
      // The CommandRow Box sets data-selected={isSelected || undefined}
      const selectedRows = document.querySelectorAll('[data-selected]');
      expect(selectedRows.length).toBe(1);
    });

    it('marks the correct quick-action row when selectedIndex points past recents', () => {
      setRecentItems([sampleEntity]);
      // index 0 = sampleEntity, index 1 = New Chat (first quick action)
      renderStateZero({ selectedIndex: 1 });
      const selectedRows = document.querySelectorAll('[data-selected]');
      expect(selectedRows.length).toBe(1);
    });

    it('no row is marked data-selected when selectedIndex is out of range', () => {
      renderStateZero({ selectedIndex: 99 });
      const selectedRows = document.querySelectorAll('[data-selected]');
      expect(selectedRows.length).toBe(0);
    });
  });

  // -------------------------------------------------------------------------
  // Clicking recent items
  // -------------------------------------------------------------------------

  describe('clicking recent items', () => {
    it('navigates to /nodes/:id and calls onClose for entity type', () => {
      setRecentItems([sampleEntity]);
      const { onClose } = renderStateZero();
      fireEvent.click(screen.getByText('Test Entity'));
      expect(mockNavigate).toHaveBeenCalledWith('/nodes/ent-1');
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('navigates to /sources/:id and calls onClose for source type', () => {
      setRecentItems([sampleSource]);
      const { onClose } = renderStateZero();
      fireEvent.click(screen.getByText('Test Source'));
      expect(mockNavigate).toHaveBeenCalledWith('/sources/src-1');
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('navigates to /chat/:id and calls onClose for chat type', () => {
      setRecentItems([sampleChat]);
      const { onClose } = renderStateZero();
      fireEvent.click(screen.getByText('Test Chat'));
      expect(mockNavigate).toHaveBeenCalledWith('/chat/chat-1');
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  // -------------------------------------------------------------------------
  // Clicking quick actions
  // -------------------------------------------------------------------------

  describe('clicking quick actions', () => {
    it('calls onActivateMode("/") when New Chat is clicked', () => {
      const { onActivateMode, onClose } = renderStateZero();
      fireEvent.click(screen.getByText('New Chat'));
      expect(onActivateMode).toHaveBeenCalledWith('/');
      // onClose is NOT called for mode activation — the mode takes over
      expect(onClose).not.toHaveBeenCalled();
    });

    it('opens upload dialog and calls onClose when Import Source is clicked', () => {
      const { openUploadDialog, onClose } = renderStateZero();
      fireEvent.click(screen.getByText('Import Source'));
      expect(openUploadDialog).toHaveBeenCalledTimes(1);
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('navigates to /graph and calls onClose when Explore Graph is clicked', () => {
      const { onClose } = renderStateZero();
      fireEvent.click(screen.getByText('Explore Graph'));
      expect(mockNavigate).toHaveBeenCalledWith('/graph');
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  // -------------------------------------------------------------------------
  // Keyboard Enter handling
  // -------------------------------------------------------------------------

  describe('keyboard Enter on selected item', () => {
    it('executes the selected recent item on Enter', () => {
      setRecentItems([sampleEntity]);
      const { onClose } = renderStateZero({ selectedIndex: 0 });
      fireEvent.keyDown(window, { key: 'Enter' });
      expect(mockNavigate).toHaveBeenCalledWith('/nodes/ent-1');
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('executes the selected quick action on Enter (New Chat at index 0 with no recents)', () => {
      const { onActivateMode } = renderStateZero({ selectedIndex: 0 });
      fireEvent.keyDown(window, { key: 'Enter' });
      expect(onActivateMode).toHaveBeenCalledWith('/');
    });

    it('executes Import Source quick action via Enter (index 1 with no recents)', () => {
      const { openUploadDialog, onClose } = renderStateZero({ selectedIndex: 1 });
      fireEvent.keyDown(window, { key: 'Enter' });
      expect(openUploadDialog).toHaveBeenCalledTimes(1);
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('executes Explore Graph quick action via Enter (index 2 with no recents)', () => {
      const { onClose } = renderStateZero({ selectedIndex: 2 });
      fireEvent.keyDown(window, { key: 'Enter' });
      expect(mockNavigate).toHaveBeenCalledWith('/graph');
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('does nothing when a non-Enter key is pressed', () => {
      setRecentItems([sampleEntity]);
      const { onClose } = renderStateZero({ selectedIndex: 0 });
      fireEvent.keyDown(window, { key: 'ArrowDown' });
      expect(onClose).not.toHaveBeenCalled();
      expect(mockNavigate).not.toHaveBeenCalled();
    });

    it('executes the right quick action when Enter is pressed after recent items (offset index)', () => {
      setRecentItems([sampleEntity]);
      // index 0 = entity (recent), index 1 = New Chat (quick action)
      const { onActivateMode } = renderStateZero({ selectedIndex: 1 });
      fireEvent.keyDown(window, { key: 'Enter' });
      expect(onActivateMode).toHaveBeenCalledWith('/');
    });
  });

  // -------------------------------------------------------------------------
  // Upload dialog context: graceful fallback when context is null
  // -------------------------------------------------------------------------

  describe('without UploadDialogContext', () => {
    it('renders without crashing when context is null', () => {
      // Render outside the provider — context defaults to null
      const { container } = render(
        <MemoryRouter>
          <ThemeProvider theme={theme}>
            <StateZero
              onClose={vi.fn<() => void>()}
              selectedIndex={0}
              onItemCount={vi.fn<(count: number) => void>()}
              onActivateMode={vi.fn<(prefix: string) => void>()}
            />
          </ThemeProvider>
        </MemoryRouter>,
      );
      expect(container).toBeTruthy();
      expect(screen.getByText('Import Source')).toBeInTheDocument();
    });

    it('clicking Import Source without context does not throw', () => {
      render(
        <MemoryRouter>
          <ThemeProvider theme={theme}>
            <StateZero
              onClose={vi.fn<() => void>()}
              selectedIndex={0}
              onItemCount={vi.fn<(count: number) => void>()}
              onActivateMode={vi.fn<(prefix: string) => void>()}
            />
          </ThemeProvider>
        </MemoryRouter>,
      );
      expect(() => fireEvent.click(screen.getByText('Import Source'))).not.toThrow();
    });
  });
});
