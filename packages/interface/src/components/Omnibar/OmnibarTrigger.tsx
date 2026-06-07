// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * OmnibarTrigger
 *
 * Slim header pill displayed in the app bar that opens the omnibar when
 * clicked. Shows a keyboard shortcut badge and responsive label text.
 */

import { Box, Typography } from '@mui/material';
import { Search as SearchIcon } from 'lucide-react';
import { ChaosCypherNeutrals } from '../../theme/palette';
import { useOmnibar } from './useOmnibar';

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/.test(navigator.userAgent);

/** Slim header pill that opens the omnibar when clicked. */
export function OmnibarTrigger() {
  const { open, isOpen, setAnchorEl } = useOmnibar();
  return (
    <Box
      ref={(el: HTMLElement | null) => setAnchorEl(el)}
      onClick={() => { if (!isOpen) open(); }}
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        px: { xs: 1.5, sm: 2.5 },
        py: 1,
        borderRadius: '8px',
        border: '1px solid',
        borderColor: isOpen ? 'transparent' : 'rgba(255, 255, 255, 0.05)',
        bgcolor: isOpen ? 'transparent' : 'rgba(15, 15, 20, 0.15)',
        backdropFilter: isOpen ? 'none' : 'blur(12px)',
        WebkitBackdropFilter: isOpen ? 'none' : 'blur(12px)',
        cursor: isOpen ? 'default' : 'pointer',
        visibility: isOpen ? 'hidden' : 'visible',
        width: '100%',
        minWidth: 0,
        transition: 'all 0.2s',
        '&:hover': isOpen ? {} : {
          borderColor: 'rgba(0, 229, 255, 0.2)',
          bgcolor: 'rgba(0, 229, 255, 0.04)',
        },
      }}
    >
      <SearchIcon size={18} color={ChaosCypherNeutrals.textTertiary} />
      <Typography
        noWrap
        sx={{
          color: 'text.disabled',
          fontSize: 14,
          flex: 1,
          minWidth: 0,
          userSelect: 'none',
          textOverflow: 'ellipsis',
          overflow: 'hidden',
          whiteSpace: 'nowrap',
        }}
      >
        {/* Short label on xs screens, full label from sm up */}
        <Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>
          Search, chat, or run a command...
        </Box>
        <Box component="span" sx={{ display: { xs: 'inline', sm: 'none' } }}>
          Search...
        </Box>
      </Typography>
      <Typography
        sx={{
          display: { xs: 'none', md: 'inline-block' },
          bgcolor: 'rgba(255, 255, 255, 0.05)',
          color: ChaosCypherNeutrals.textMuted,
          px: 0.75,
          py: 0.25,
          borderRadius: '4px',
          fontSize: 11,
          fontFamily: 'monospace',
          userSelect: 'none',
          whiteSpace: 'nowrap',
          flexShrink: 0,
          border: '1px solid rgba(255, 255, 255, 0.05)',
        }}
      >
        {isMac ? '\u2318K' : 'Ctrl+K'}
      </Typography>
    </Box>
  );
}
