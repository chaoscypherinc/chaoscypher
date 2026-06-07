// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  Typography,
  Alert,
  Button,
} from '@mui/material';
import type { ChatError } from '../../types';
import type { ExtendedChatMessage } from './types';

interface ContextPanelProps {
  /** Current error state to display */
  error: ChatError | null;
  /** Clear the current error */
  onClearError: () => void;
  /** Messages array (used to find last user message for retry) */
  messages: ExtendedChatMessage[];
  /** Called to populate the input with a message for retry */
  onRetry: (message: string) => void;
}

/**
 * Contextual information panel displayed above the message list.
 * Shows error alerts with retry capability and suggested actions.
 *
 * This component consolidates the error/context display logic that
 * was previously inline in the ChatPage layout.
 */
export default function ContextPanel({
  error,
  onClearError,
  messages,
  onRetry,
}: ContextPanelProps) {
  if (!error) return null;

  return (
    <Alert
      severity="error"
      onClose={onClearError}
      sx={{ m: 2 }}
      action={
        error.details.is_retryable ? (
          <Button
            color="inherit"
            size="small"
            onClick={() => {
              onClearError();
              const lastUserMsg = messages.filter(m => m.role === 'user').pop();
              if (lastUserMsg) {
                onRetry(lastUserMsg.content);
              }
            }}
          >
            Retry
          </Button>
        ) : undefined
      }
    >
      <Box>
        <Typography variant="body2" sx={{
          fontWeight: "medium"
        }}>
          {error.message}
        </Typography>
        {error.details.suggested_action && (
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              display: 'block',
              mt: 0.5
            }}>
            {error.details.suggested_action}
          </Typography>
        )}
        {error.details.retry_after && (
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              display: 'block'
            }}>
            Retry available in {error.details.retry_after} seconds
          </Typography>
        )}
      </Box>
    </Alert>
  );
}
