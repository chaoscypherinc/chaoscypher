// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Command mode for the omnibar — prefix >.
 * Fuzzy-matches commands from the registry and executes them.
 */
import { useEffect, useMemo, useCallback, useContext } from 'react';
import { Box, Typography } from '@mui/material';
import { useNavigate } from 'react-router';
import { buildCommandRegistry, matchCommand } from '../commands/registry';
import { UploadDialogContext } from '../../../contexts/UploadDialogContext';
import { useNotification } from '../../../contexts/useNotification';
import { searchApi } from '../../../services/api/search';
import type { ModeResultsProps } from '../types';
import { ChaosCypherPalette, ChaosCypherNeutrals } from '../../../theme/palette';

const COLOR = ChaosCypherPalette.warning;

function highlightMatch(text: string, query: string): React.ReactNode {
  if (!query || !text) return text;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return text;
  return (
    <>
      {text.slice(0, idx)}
      <Box
        component="mark"
        sx={{ bgcolor: `${COLOR}33`, color: COLOR, px: 0.25, borderRadius: '2px' }}
      >
        {text.slice(idx, idx + query.length)}
      </Box>
      {text.slice(idx + query.length)}
    </>
  );
}

export function CommandMode({ query, selectedIndex, onClose, onItemCount }: ModeResultsProps) {
  const navigate = useNavigate();
  const uploadCtx = useContext(UploadDialogContext);
  const { notify } = useNotification();

  const commands = useMemo(
    () =>
      buildCommandRegistry({
        navigate: (path) => {
          navigate(path);
          onClose();
        },
        openUploadDialog: () => {
          uploadCtx?.openUploadDialog();
          onClose();
        },
        notify: (msg, severity) => notify(msg, severity as 'success' | 'error' | 'info' | 'warning'),
        rebuildIndexes: () => searchApi.rebuildIndexes(),
      }),
    [navigate, onClose, uploadCtx, notify],
  );

  const filtered = useMemo(() => {
    return commands
      .map((cmd) => ({ cmd, score: matchCommand(cmd, query) }))
      .filter((entry) => entry.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((entry) => entry.cmd);
  }, [commands, query]);

  useEffect(() => {
    onItemCount(filtered.length);
  }, [filtered.length, onItemCount]);

  const handleExecute = useCallback(
    (index: number) => {
      const cmd = filtered[index];
      if (cmd) cmd.action();
    },
    [filtered],
  );

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && filtered.length > 0) {
        e.preventDefault();
        handleExecute(selectedIndex);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedIndex, filtered.length, handleExecute]);

  if (filtered.length === 0) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <Typography sx={{ color: ChaosCypherNeutrals.textMuted, fontSize: 13 }}>
          No commands matching &ldquo;{query}&rdquo;
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ px: 2.5, py: 1.5 }}>
      {filtered.map((cmd, i) => {
        const isSelected = i === selectedIndex;
        return (
          <Box
            key={cmd.id}
            data-selected={isSelected || undefined}
            onClick={() => handleExecute(i)}
            sx={{
              display: 'flex',
              alignItems: 'center',
              p: '10px 12px',
              borderRadius: '8px',
              mb: 0.5,
              gap: 1.5,
              cursor: 'pointer',
              bgcolor: isSelected ? `${COLOR}14` : 'transparent',
              border: isSelected ? `1px solid ${COLOR}26` : '1px solid transparent',
              '&:hover': { bgcolor: `${COLOR}14` },
            }}
          >
            <Box
              sx={{
                width: 32,
                height: 32,
                borderRadius: '6px',
                bgcolor: isSelected ? `${COLOR}1F` : ChaosCypherNeutrals.surfaceRaised,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 16,
              }}
            >
              {cmd.icon}
            </Box>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography sx={{ color: 'text.primary', fontSize: 14 }}>
                {highlightMatch(cmd.label, query)}
              </Typography>
              <Typography sx={{ color: 'text.disabled', fontSize: 12 }}>{cmd.description}</Typography>
            </Box>
            {cmd.destructive && (
              <Typography sx={{ color: 'error.main', fontSize: 11 }}>⚠ destructive</Typography>
            )}
          </Box>
        );
      })}
    </Box>
  );
}
