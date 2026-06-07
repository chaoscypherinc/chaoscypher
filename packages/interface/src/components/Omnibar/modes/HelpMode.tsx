// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Help mode for the omnibar — prefix ?.
 * Static reference card showing modes, keyboard shortcuts, and search tips.
 */
import { useEffect } from 'react';
import { Box, Typography } from '@mui/material';
import type { ModeResultsProps } from '../types';
import { ChaosCypherPalette, ChaosCypherNeutrals } from '../../../theme/palette';

const MODES_INFO = [
  { prefix: 'abc', color: ChaosCypherPalette.primary, name: 'Search', desc: 'Search entities, sources, and chunks across the knowledge graph' },
  { prefix: '> ...', color: ChaosCypherPalette.warning, name: 'Commands', desc: 'Navigate pages, trigger actions, manage the system' },
  { prefix: '/ ...', color: ChaosCypherPalette.success, name: 'Chat', desc: 'Start a new AI conversation with your question' },
  { prefix: '?', color: ChaosCypherPalette.purple, name: 'Help', desc: 'You are here — view all modes, shortcuts, and tips' },
];

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/.test(navigator.userAgent);
const metaKey = isMac ? '⌘K' : 'Ctrl+K';

const SHORTCUTS = [
  { key: metaKey, label: 'Open omnibar' },
  { key: 'Esc', label: 'Close omnibar' },
  { key: '↑ ↓', label: 'Navigate results' },
  { key: 'Enter', label: 'Open selected item or run command' },
  { key: 'Backspace', label: 'Clear prefix to return to search mode' },
];

const TIPS = [
  'Search is hybrid — it tries semantic matching first, falls back to keyword if needed',
  'Results are grouped by type: entities, sources, and document chunks',
  'Commands are fuzzy-matched — "set" matches "Settings", "Reset", etc.',
];

export function HelpMode({ onItemCount }: ModeResultsProps) {
  useEffect(() => {
    onItemCount(0);
  }, [onItemCount]);

  return (
    <Box sx={{ px: 2.5, py: 2 }}>
      {/* Modes */}
      <Box sx={{ mb: 2.5 }}>
        <Typography
          sx={{
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            color: ChaosCypherNeutrals.textMuted,
            mb: 1.5,
          }}
        >
          Modes
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '8px 16px', px: 0.5 }}>
          {MODES_INFO.map((mode) => (
            <Box key={mode.prefix} sx={{ display: 'contents' }}>
              <Box sx={{ display: 'flex', alignItems: 'center' }}>
                <Typography
                  sx={{
                    bgcolor: ChaosCypherNeutrals.surfaceRaised,
                    color: mode.color,
                    px: 1.25,
                    py: 0.25,
                    borderRadius: '4px',
                    fontFamily: 'monospace',
                    fontSize: 13,
                    minWidth: 60,
                    textAlign: 'center',
                  }}
                >
                  {mode.prefix}
                </Typography>
              </Box>
              <Box>
                <Typography sx={{ color: 'text.primary', fontSize: 13 }}>{mode.name}</Typography>
                <Typography sx={{ color: 'text.disabled', fontSize: 12 }}>{mode.desc}</Typography>
              </Box>
            </Box>
          ))}
        </Box>
      </Box>

      {/* Keyboard Shortcuts */}
      <Box sx={{ mb: 2.5 }}>
        <Typography
          sx={{
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            color: ChaosCypherNeutrals.textMuted,
            mb: 1.5,
          }}
        >
          Keyboard Shortcuts
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '6px 16px', px: 0.5 }}>
          {SHORTCUTS.map((sc) => (
            <Box key={sc.key} sx={{ display: 'contents' }}>
              <Typography
                sx={{
                  bgcolor: ChaosCypherNeutrals.surfaceRaised,
                  color: 'text.secondary',
                  px: 1,
                  py: 0.25,
                  borderRadius: '4px',
                  fontFamily: 'monospace',
                  fontSize: 12,
                  textAlign: 'center',
                }}
              >
                {sc.key}
              </Typography>
              <Typography sx={{ color: 'text.secondary', fontSize: 13, alignSelf: 'center' }}>
                {sc.label}
              </Typography>
            </Box>
          ))}
        </Box>
      </Box>

      {/* Search Tips */}
      <Box>
        <Typography
          sx={{
            fontSize: 11,
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            color: ChaosCypherNeutrals.textMuted,
            mb: 1.5,
          }}
        >
          Search Tips
        </Typography>
        <Box sx={{ px: 0.5, display: 'flex', flexDirection: 'column', gap: 1 }}>
          {TIPS.map((tip) => (
            <Box key={tip} sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.25 }}>
              <Typography sx={{ color: 'primary.main', fontSize: 12, mt: 0.25 }}>▸</Typography>
              <Typography sx={{ color: 'text.secondary', fontSize: 13 }}>{tip}</Typography>
            </Box>
          ))}
        </Box>
      </Box>
    </Box>
  );
}
