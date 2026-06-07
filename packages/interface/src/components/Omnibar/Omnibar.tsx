// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Omnibar shell — Popover dropdown anchored to the trigger pill with backdrop blur.
 * Delegates result rendering to mode-specific components.
 */
import { useState, useRef, useEffect, useCallback } from 'react';
import { Modal, Box, InputBase, Typography, Chip } from '@mui/material';
import { Search as SearchIcon } from 'lucide-react';
import { SearchMode } from './modes/SearchMode';
import { CommandMode } from './modes/CommandMode';
import { ChatMode } from './modes/ChatMode';
import { HelpMode } from './modes/HelpMode';
import { StateZero } from './StateZero';
import type { OmnibarMode, ModeResultsProps } from './types';
import { ChaosCypherPalette, ChaosCypherBackground, ChaosCypherNeutrals } from '../../theme/palette';

const MODES: OmnibarMode[] = [
  { prefix: '>', label: 'COMMAND', color: ChaosCypherPalette.warning, placeholder: 'Type a command...' },
  { prefix: '/', label: 'CHAT', color: ChaosCypherPalette.success, placeholder: 'Ask a question...' },
  { prefix: '?', label: 'HELP', color: ChaosCypherPalette.purple, placeholder: 'Filter help topics...' },
];

const HINT_STORAGE_KEY = 'chaoscypher-omnibar-hint-count';
const HINT_MAX_SHOWS = 3;

interface OmnibarProps {
  isOpen: boolean;
  onClose: () => void;
  initialQuery?: string;
  initialMode?: string;
  openKey: number;
  anchorEl: HTMLElement | null;
}

function getHintCount(): number {
  try {
    return parseInt(localStorage.getItem(HINT_STORAGE_KEY) ?? '0', 10);
  } catch {
    return 0;
  }
}

function incrementHintCount(): void {
  try {
    localStorage.setItem(HINT_STORAGE_KEY, String(getHintCount() + 1));
  } catch { /* ignore */ }
}

function isHintDismissed(): boolean {
  try {
    return localStorage.getItem(HINT_STORAGE_KEY) === 'dismissed';
  } catch {
    return false;
  }
}

function dismissHint(): void {
  try {
    localStorage.setItem(HINT_STORAGE_KEY, 'dismissed');
  } catch { /* ignore */ }
}

const MODE_COMPONENTS: Record<string, React.ComponentType<ModeResultsProps>> = {
  '>': CommandMode,
  '/': ChatMode,
  '?': HelpMode,
};

export function Omnibar({ isOpen, onClose, initialQuery, initialMode, openKey, anchorEl }: OmnibarProps) {
  if (!isOpen || !anchorEl) return null;

  return (
    <OmnibarContent
      key={openKey}
      onClose={onClose}
      anchorEl={anchorEl}
      initialQuery={initialQuery}
      initialMode={initialMode}
    />
  );
}

function OmnibarContent({
  onClose,
  anchorEl,
  initialQuery,
  initialMode,
}: Pick<OmnibarProps, 'onClose' | 'anchorEl' | 'initialQuery' | 'initialMode'>) {
  // Mode is tracked as state — prefix is consumed on activation, not kept in query
  const [activeMode, setActiveMode] = useState<OmnibarMode | null>(() => {
    if (initialMode) return MODES.find((m) => m.prefix === initialMode) ?? null;
    return null;
  });
  const [query, setQuery] = useState(initialQuery ?? '');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const itemCountRef = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  const [showHint, setShowHint] = useState(() => {
    if (isHintDismissed()) return false;
    const count = getHintCount();
    if (count < HINT_MAX_SHOWS) {
      incrementHintCount();
      return true;
    }
    return false;
  });

  useEffect(() => {
    const timer = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(timer);
  }, []);

  // Scroll selected item into view when arrow keys change selection
  useEffect(() => {
    const container = contentRef.current;
    if (!container) return;
    const selected = container.querySelector('[data-selected="true"]') as HTMLElement | null;
    if (selected) {
      selected.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedIndex]);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value;
      // Detect mode prefix typed into empty input (no mode active yet)
      if (!activeMode && query === '') {
        const mode = MODES.find((m) => val === m.prefix);
        if (mode) {
          setActiveMode(mode);
          setQuery('');
          setSelectedIndex(0);
          return;
        }
      }
      setQuery(val);
      setSelectedIndex(0);
    },
    [activeMode, query],
  );

  const activateMode = useCallback((prefix: string) => {
    const mode = MODES.find((m) => m.prefix === prefix);
    if (mode) {
      setActiveMode(mode);
      setQuery('');
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const count = itemCountRef.current;
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          setSelectedIndex((prev) => (count > 0 ? (prev + 1) % count : 0));
          break;
        case 'ArrowUp':
          e.preventDefault();
          setSelectedIndex((prev) => (count > 0 ? (prev - 1 + count) % count : 0));
          break;
        case 'Backspace':
          if (activeMode && query === '') {
            e.preventDefault();
            setActiveMode(null);
          }
          break;
        case 'Escape':
          e.preventDefault();
          onClose();
          break;
      }
    },
    [activeMode, query, onClose],
  );

  const handleItemCount = useCallback((count: number) => {
    const prev = itemCountRef.current;
    itemCountRef.current = count;
    if (prev !== count && count > 0) {
      setSelectedIndex((si) => (si >= count ? count - 1 : si));
    }
  }, []);

  const isStateZero = !activeMode && !query;

  const ModeComponent = activeMode
    ? MODE_COMPONENTS[activeMode.prefix] ?? null
    : query
      ? SearchMode
      : null;

  const modeResultsProps: ModeResultsProps = {
    query,
    selectedIndex,
    onExecute: () => {},
    onClose,
    onItemCount: handleItemCount,
  };

  // Footer hints — only shown in non-state-zero modes
  const footerHints = activeMode
    ? activeMode.prefix === '>'
      ? [
          { key: '↑↓', label: 'navigate' },
          { key: '↵', label: 'run' },
          { key: '⌫', label: 'back' },
        ]
      : activeMode.prefix === '/'
        ? [
            { key: '↵', label: 'send' },
            { key: '⌫', label: 'back' },
          ]
        : [
            { key: 'Esc', label: 'close' },
            { key: '⌫', label: 'back' },
          ]
    : isStateZero
      ? null // No footer in state zero — keep it tight
      : [
          { key: '↑↓', label: 'navigate' },
          { key: '↵', label: 'open' },
        ];

  // Compute position from anchor element
  const anchorRect = anchorEl?.getBoundingClientRect();
  const dropdownStyle = anchorRect
    ? { top: anchorRect.top, left: anchorRect.left, width: anchorRect.width }
    : { top: 64, left: '50%', width: 640 };

  return (
    <Modal
      open
      onClose={onClose}
      disableAutoFocus
      disableEnforceFocus
      slotProps={{
        backdrop: {
          sx: {
            backgroundColor: 'rgba(0, 0, 0, 0.6)',
            backdropFilter: 'blur(4px)',
          },
        },
      }}
    >
      <Box
        sx={{
          position: 'absolute',
          top: dropdownStyle.top,
          left: dropdownStyle.left,
          width: dropdownStyle.width,
          maxHeight: '60vh',
          bgcolor: ChaosCypherBackground.dark.default,
          borderRadius: '8px',
          border: `1px solid ${ChaosCypherNeutrals.surfaceRaised}`,
          overflow: 'hidden',
          outline: 'none',
        }}
      >
      {/* Inline hint banner */}
      {showHint && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            px: 2,
            py: 0.75,
            bgcolor: 'rgba(0, 229, 255, 0.04)',
            borderBottom: '1px solid rgba(0, 229, 255, 0.08)',
            gap: 1,
          }}
        >
          <Typography sx={{ fontSize: 11, color: 'text.disabled', flex: 1 }}>
            Type to search ·{' '}
            <Box component="span" sx={{ color: 'warning.main' }}>&gt;</Box> commands ·{' '}
            <Box component="span" sx={{ color: 'success.main' }}>/</Box> chat ·{' '}
            <Box component="span" sx={{ color: ChaosCypherPalette.purple }}>?</Box> help
          </Typography>
          <Typography
            sx={{ fontSize: 11, color: ChaosCypherNeutrals.borderDivider, cursor: 'pointer', '&:hover': { color: 'text.disabled' } }}
            onClick={() => { dismissHint(); setShowHint(false); }}
          >
            ✕
          </Typography>
        </Box>
      )}

      {/* Input area */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          px: 2,
          py: 1.25,
          borderBottom: `1px solid ${ChaosCypherNeutrals.surfaceRaised}`,
          gap: 1.25,
        }}
      >
        {activeMode ? (
          <Chip
            label={activeMode.label}
            size="small"
            sx={{
              bgcolor: `${activeMode.color}22`,
              color: activeMode.color,
              fontWeight: 600,
              fontSize: 11,
              height: 22,
            }}
          />
        ) : (
          <SearchIcon size={18} color={ChaosCypherPalette.primary} />
        )}
        <InputBase
          inputRef={inputRef}
          value={query}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          placeholder={activeMode?.placeholder ?? 'Search, > command, / chat...'}
          fullWidth
          sx={{
            fontSize: 14,
            color: 'text.primary',
            '& input::placeholder': { color: ChaosCypherNeutrals.textMuted, opacity: 1 },
          }}
        />
        <Typography
          sx={{
            bgcolor: ChaosCypherNeutrals.surfaceRaised,
            color: 'text.disabled',
            px: 0.75,
            py: 0.15,
            borderRadius: '3px',
            fontSize: 10,
            fontFamily: 'monospace',
            whiteSpace: 'nowrap',
          }}
        >
          ESC
        </Typography>
      </Box>

      {/* Content area */}
      <Box ref={contentRef} sx={{ maxHeight: 350, overflowY: 'auto' }}>
        {isStateZero && (
          <StateZero
            onClose={onClose}
            selectedIndex={selectedIndex}
            onItemCount={handleItemCount}
            onActivateMode={activateMode}
          />
        )}
        {ModeComponent && <ModeComponent {...modeResultsProps} />}
      </Box>

      {/* Footer — only shown when there are results, not in state zero */}
      {footerHints && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            px: 2,
            py: 0.75,
            borderTop: `1px solid ${ChaosCypherNeutrals.surfaceRaised}`,
            gap: 1.5,
          }}
        >
          {footerHints.map((hint) => (
            <Typography key={hint.key} sx={{ fontSize: 10, color: ChaosCypherNeutrals.borderDivider }}>
              <Box
                component="span"
                sx={{ bgcolor: ChaosCypherNeutrals.surfaceRaised, px: 0.5, py: 0.1, borderRadius: '2px', mr: 0.4 }}
              >
                {hint.key}
              </Box>
              {hint.label}
            </Typography>
          ))}
        </Box>
      )}
      </Box>
    </Modal>
  );
}
