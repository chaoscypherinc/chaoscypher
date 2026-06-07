// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Chat mode for the omnibar — prefix /.
 * Quick-launches a new AI chat thread with the user's question.
 */
import { useEffect, useCallback } from 'react';
import { Box, Typography } from '@mui/material';
import { useNavigate } from 'react-router';
import { CHAT_STARTERS } from '../../../constants/chatStarters';
import type { ModeResultsProps } from '../types';
import { ChaosCypherNeutrals } from '../../../theme/palette';

export function ChatMode({ query, onClose, onItemCount }: ModeResultsProps) {
  const navigate = useNavigate();

  useEffect(() => {
    onItemCount(0);
  }, [onItemCount]);

  const handleSend = useCallback(
    (message?: string) => {
      const text = (message ?? query).trim();
      if (!text) return;

      navigate('/chat', { state: { initialMessage: text } });
      onClose();
    },
    [query, navigate, onClose],
  );

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleSend();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleSend]);

  if (query.trim()) {
    return (
      <Box sx={{ px: 2.5, py: 1.5, textAlign: 'center' }}>
        <Typography sx={{ color: ChaosCypherNeutrals.textMuted, fontSize: 13 }}>
          Press{' '}
          <Box
            component="span"
            sx={{
              bgcolor: ChaosCypherNeutrals.surfaceRaised,
              px: 1,
              py: 0.25,
              borderRadius: '4px',
              fontFamily: 'monospace',
              mx: 0.25,
            }}
          >
            Enter
          </Box>{' '}
          to start chatting
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ px: 2.5, py: 1.5 }}>
      <Typography sx={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.1em', color: ChaosCypherNeutrals.textMuted, px: 1, mb: 0.75 }}>
        Try asking
      </Typography>
      {CHAT_STARTERS.map((starter) => (
        <Box
          key={starter.prompt}
          onClick={() => handleSend(starter.prompt)}
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1.25,
            px: 1.25,
            py: 0.75,
            borderRadius: '6px',
            cursor: 'pointer',
            '&:hover': { bgcolor: 'rgba(29, 233, 182, 0.08)' },
          }}
        >
          <Typography sx={{ fontSize: 14, lineHeight: 1, width: 20, textAlign: 'center' }}>
            {starter.icon}
          </Typography>
          <Typography sx={{ color: 'text.primary', fontSize: 13 }}>
            {starter.label}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
