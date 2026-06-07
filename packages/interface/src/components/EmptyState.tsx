// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * EmptyState Component
 * Reusable empty state for pages and sections with no data
 */

import { Box, Paper, Typography, Button } from '@mui/material';
import { Overlays } from '../theme/overlays';
import { ghostButtonSx } from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';
import React from 'react';

interface EmptyStateProps {
  /** Message to display */
  message: string;
  /** Optional action button label */
  actionLabel?: string;
  /** Optional action button click handler */
  onAction?: () => void;
  /** Optional icon to display above message */
  icon?: React.ReactNode;
  /** Optional secondary message */
  secondaryMessage?: string;
}

/**
 * Empty state component with message and optional action button
 *
 * @example
 * if (nodes.length === 0) {
 *   return (
 *     <EmptyState
 *       message="No items yet."
 *       actionLabel="Create Item"
 *       onAction={handleCreate}
 *     />
 *   );
 * }
 */
export function EmptyState({
  message,
  actionLabel,
  onAction,
  icon,
  secondaryMessage,
}: EmptyStateProps) {
  return (
    <Paper
      sx={{
        p: 4,
        textAlign: 'center',
        backgroundColor: (theme) =>
          theme.palette.mode === 'dark'
            ? Overlays.subtle.dark
            : Overlays.subtle.light,
      }}
    >
      {icon && (
        <Box
          sx={{
            mb: 2,
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            fontSize: 48,
            color: 'text.disabled',
          }}
        >
          {icon}
        </Box>
      )}
      <Typography
        variant="h6"
        sx={{
          color: "text.secondary",
          mb: secondaryMessage ? 1 : 2
        }}>
        {message}
      </Typography>
      {secondaryMessage && (
        <Typography
          variant="body2"
          sx={{
            color: "text.disabled",
            mb: 2
          }}>
          {secondaryMessage}
        </Typography>
      )}
      {actionLabel && onAction && (
        <Button variant="outlined" onClick={onAction} sx={{ ...ghostButtonSx(ChaosCypherPalette.primary), mt: 1 }}>
          {actionLabel}
        </Button>
      )}
    </Paper>
  );
}
