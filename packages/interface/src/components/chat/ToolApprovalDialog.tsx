// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tool approval dialog.
 *
 * Rendered when the chat stream emits a `tool_approval_required` SSE event.
 * The backend is blocked for up to 5 minutes waiting for the user to
 * Approve or Reject the tool call. ESC / backdrop close is disabled — the
 * user must make an explicit decision so the stream doesn't silently hang.
 *
 * Props are narrow on purpose: this component does not know about the
 * chat stream or the approval queue. The parent wires the POST back to
 * `/chats/{id}/tool_decision` via `chatApi.decideTool`.
 */

import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from '@mui/material';
import ToolIcon from '@mui/icons-material/Build';
import WriteIcon from '@mui/icons-material/Edit';
import { ChatTheme } from '../../theme/chatTheme';
import { ChaosCypherPalette } from '../../theme/palette';
import { ghostDialogPaperSx, ghostButtonSx, ghostCancelBtnSx } from '../../theme/ghostStyles';

interface ToolApprovalDialogProps {
  /** Whether the dialog is visible. */
  open: boolean;
  /** Tool-call ID the backend is waiting on. */
  toolCallId: string;
  /** Tool name being requested. */
  toolName: string;
  /** Parsed arguments the LLM wants to pass. */
  arguments: Record<string, unknown>;
  /**
   * Callback fired when the user picks a decision. Must resolve once the
   * backend has acknowledged so the dialog stays open (with a spinner)
   * during the round-trip.
   */
  onDecide: (decision: 'approve' | 'reject') => Promise<void>;
  /** Close hook used after `onDecide` resolves. No-op when a decision is in flight. */
  onClose: () => void;
}

/**
 * Heuristic for whether a tool name looks mutating. The backend has the
 * authoritative `mutating_tools` list — this is purely for iconography.
 */
const WRITE_TOOL_PREFIXES = [
  'create_',
  'update_',
  'delete_',
  'add_',
  'remove_',
  'finalize_',
  'submit_',
];

function looksMutating(toolName: string): boolean {
  return WRITE_TOOL_PREFIXES.some((prefix) => toolName.startsWith(prefix));
}

/**
 * Format arguments as pretty-printed JSON for display. Falls back to a
 * string dump if serialization throws (e.g. circular refs — shouldn't
 * happen for LLM-supplied data but cheap to guard).
 */
function formatArguments(args: Record<string, unknown>): string {
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
}

export default function ToolApprovalDialog({
  open,
  toolCallId,
  toolName,
  arguments: args,
  onDecide,
  onClose,
}: ToolApprovalDialogProps) {
  const [deciding, setDeciding] = useState<'approve' | 'reject' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mutating = looksMutating(toolName);

  const handleDecide = async (decision: 'approve' | 'reject') => {
    if (deciding) return;
    setError(null);
    setDeciding(decision);
    try {
      await onDecide(decision);
      onClose();
    } catch (err) {
      // Surface the failure but keep the dialog open so the user can retry.
      // The backend stream is still blocked on us, so closing now would
      // leave it hanging until the 5-minute timeout.
      setError(err instanceof Error ? err.message : 'Failed to record decision. Please try again.');
    } finally {
      setDeciding(null);
    }
  };

  return (
    <Dialog
      open={open}
      // Ignore backdrop + ESC dismissal — user must explicitly Approve/Reject
      // so the server-side stream (blocked up to 5 min) isn't left hanging.
      onClose={(_event, reason) => {
        if (reason === 'backdropClick' || reason === 'escapeKeyDown') return;
        onClose();
      }}
      maxWidth="sm"
      fullWidth
      slotProps={{ paper: { sx: ghostDialogPaperSx } }}
      // Data attribute for test / debug targeting
      data-tool-call-id={toolCallId}
    >
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
        {mutating ? (
          <WriteIcon sx={{ color: ChaosCypherPalette.warning }} />
        ) : (
          <ToolIcon sx={{ color: ChaosCypherPalette.primary }} />
        )}
        <Box>
          <Typography variant="h6" component="div" sx={{ lineHeight: 1.2 }}>
            {mutating ? 'Approve tool call (writes data)' : 'Approve tool call'}
          </Typography>
          <Typography
            variant="caption"
            sx={{
              fontFamily: 'monospace',
              color: 'text.secondary',
              display: 'block',
            }}
          >
            {toolName}
          </Typography>
        </Box>
      </DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" sx={{ mb: 1, color: 'text.secondary' }}>
          The assistant wants to call <strong>{toolName}</strong> with the following arguments:
        </Typography>
        <Box
          sx={{
            p: 1.5,
            backgroundColor: ChatTheme.tools.outputBg,
            borderRadius: 1,
            maxHeight: 360,
            overflow: 'auto',
          }}
        >
          <pre style={{ margin: 0, fontSize: '0.8rem', fontFamily: 'monospace' }}>
            {formatArguments(args)}
          </pre>
        </Box>
        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
        <Typography
          variant="caption"
          sx={{
            display: 'block',
            mt: 2,
            color: 'text.secondary',
            fontStyle: 'italic',
          }}
        >
          You&apos;ll be asked again if the assistant proposes more mutating tool calls.
          Change this default in Settings &rarr; General.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button
          onClick={() => handleDecide('reject')}
          disabled={deciding !== null}
          startIcon={deciding === 'reject' ? <CircularProgress size={16} /> : null}
          sx={ghostCancelBtnSx}
        >
          Reject
        </Button>
        <Button
          onClick={() => handleDecide('approve')}
          disabled={deciding !== null}
          variant="outlined"
          autoFocus
          startIcon={deciding === 'approve' ? <CircularProgress size={16} /> : null}
          sx={ghostButtonSx(mutating ? ChaosCypherPalette.warning : ChaosCypherPalette.primary)}
        >
          Approve
        </Button>
      </DialogActions>
    </Dialog>
  );
}
