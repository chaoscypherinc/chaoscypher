// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  IconButton,
  Popover,
  Box,
  Typography,
  LinearProgress,
  Tooltip,
} from '@mui/material';
import InfoIcon from '@mui/icons-material/Info';
import type { ContextInfo } from '../../types';

interface ContextInfoButtonProps {
  contextInfo: ContextInfo | null;
}

export default function ContextInfoButton({ contextInfo }: ContextInfoButtonProps) {
  const [open, setOpen] = useState(false);
  // Use a state setter as the ref callback so MUI Popover gets a stable
  // anchorEl from React state instead of reading anchorRef.current during
  // render (which trips React Compiler's `refs` rule).
  const [anchorEl, setAnchorEl] = useState<HTMLButtonElement | null>(null);

  const handleMouseEnter = () => {
    setOpen(true);
  };

  const handleMouseLeave = () => {
    setOpen(false);
  };

  if (!contextInfo) {
    return (
      <Tooltip title="Context info will appear after sending a message">
        <span style={{ display: 'flex', alignItems: 'center' }}>
          <IconButton aria-label="Context info will appear after sending a message" size="small" disabled sx={{ opacity: 0.5 }}>
            <InfoIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
    );
  }

  const usagePercent = Math.round((contextInfo.tokens_used / contextInfo.context_window) * 100);
  const messagesOutOfContext = contextInfo.total_messages - contextInfo.messages_in_context;

  return (
    <Box
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      sx={{ display: 'flex', alignItems: 'center' }}
    >
      <IconButton
        aria-label="Show context info"
        ref={setAnchorEl}
        size="small"
        sx={{
          color: usagePercent > 80 ? 'warning.main' : 'text.secondary',
        }}
      >
        <InfoIcon fontSize="small" />
      </IconButton>
      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={() => setOpen(false)}
        anchorOrigin={{
          vertical: 'top',
          horizontal: 'center',
        }}
        transformOrigin={{
          vertical: 'bottom',
          horizontal: 'center',
        }}
        disableRestoreFocus
        sx={{
          pointerEvents: 'none',
          '& .MuiPopover-paper': {
            pointerEvents: 'auto',
          },
        }}
      >
        <Box
          sx={{ p: 2, minWidth: 280, maxWidth: 320 }}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          <Typography variant="subtitle2" gutterBottom sx={{
            fontWeight: "bold"
          }}>
            Context Window Status
          </Typography>

          <Box sx={{ mb: 2 }}>
            <Typography variant="caption" sx={{
              color: "text.secondary"
            }}>
              Provider: <strong>{contextInfo.provider}</strong> ({contextInfo.model})
            </Typography>
          </Box>

          <Box sx={{ mb: 2 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
              <Typography variant="caption">Context Usage</Typography>
              <Typography variant="caption" sx={{
                fontWeight: "bold"
              }}>
                {usagePercent}%
              </Typography>
            </Box>
            <LinearProgress
              variant="determinate"
              value={usagePercent}
              sx={{
                height: 8,
                borderRadius: 4,
                backgroundColor: 'action.hover',
                '& .MuiLinearProgress-bar': {
                  backgroundColor: usagePercent > 80 ? 'warning.main' : 'primary.main',
                  borderRadius: 4,
                },
              }}
            />
          </Box>

          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, mb: 1 }}>
            <Box>
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  display: "block"
                }}>
                Messages in context
              </Typography>
              <Typography variant="body2" sx={{
                fontWeight: "medium"
              }}>
                {contextInfo.messages_in_context} of {contextInfo.total_messages}
              </Typography>
            </Box>
            <Box>
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  display: "block"
                }}>
                Tokens used
              </Typography>
              <Typography variant="body2" sx={{
                fontWeight: "medium"
              }}>
                {contextInfo.tokens_used.toLocaleString()}
              </Typography>
            </Box>
          </Box>

          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1 }}>
            <Box>
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  display: "block"
                }}>
                Context window
              </Typography>
              <Typography variant="body2" sx={{
                fontWeight: "medium"
              }}>
                {contextInfo.context_window.toLocaleString()}
              </Typography>
            </Box>
            <Box>
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  display: "block"
                }}>
                Reserved for response
              </Typography>
              <Typography variant="body2" sx={{
                fontWeight: "medium"
              }}>
                {Math.round(contextInfo.context_window * 0.5).toLocaleString()}
              </Typography>
            </Box>
          </Box>

          {messagesOutOfContext > 0 && (
            <Box sx={{ mt: 2, p: 1, bgcolor: 'action.hover', borderRadius: 1 }}>
              <Typography variant="caption" sx={{
                color: "text.secondary"
              }}>
                {messagesOutOfContext} message{messagesOutOfContext > 1 ? 's' : ''} greyed out
                (outside context window)
              </Typography>
            </Box>
          )}
        </Box>
      </Popover>
    </Box>
  );
}
