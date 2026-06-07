// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  TextField,
  IconButton,
  Tooltip,
  Typography,
} from '@mui/material';
import EnterIcon from '@mui/icons-material/KeyboardReturn';
import { ContextInfoButton } from '../../components/chat';
import { ChatTheme } from '../../theme/chatTheme';
import type { ContextInfo } from '../../types';

interface ChatInputProps {
  /** Current text input value */
  input: string;
  /** Whether a message is being sent/streamed */
  loading: boolean;
  /** Context window usage information for the info button */
  contextInfo: ContextInfo | null;
  /** Ref for the text input element (used for auto-focus) */
  inputRef: React.RefObject<HTMLInputElement | null>;
  /** Called when the input value changes */
  onInputChange: (value: string) => void;
  /** Called when the user submits a message */
  onSend: () => void;
  /**
   * When set, the input and send button are disabled and the reason
   * surfaces in the placeholder + tooltip. Used to mirror the
   * server-side ``LLM_NOT_VERIFIED`` gate so the user sees the disabled
   * state before clicking Send.
   */
  disabledReason?: string | null;
}

/**
 * Floating glass terminal input for the chat interface.
 *
 * Features frosted glass background, Enter key hint, and context info button.
 */
export default function ChatInput({
  input,
  loading,
  contextInfo,
  inputRef,
  onInputChange,
  onSend,
  disabledReason = null,
}: ChatInputProps) {
  const gated = disabledReason !== null;
  return (
    <Box sx={{ px: 2, pb: 2, pt: 1, flexShrink: 0 }}>
      <Box
        sx={{
          display: 'flex',
          gap: 1,
          alignItems: 'center',
          background: ChatTheme.input.bg,
          backdropFilter: ChatTheme.input.blur,
          WebkitBackdropFilter: ChatTheme.input.blur,
          border: ChatTheme.input.border,
          borderRadius: 2.5,
          px: 2,
          py: 1.25,
        }}
      >
        <ContextInfoButton contextInfo={contextInfo} />
        <Box sx={{ flexGrow: 1, position: 'relative' }}>
          <TextField
            inputRef={inputRef}
            fullWidth
            multiline
            maxRows={4}
            placeholder={gated ? (disabledReason ?? '') : 'Ask me anything about your knowledge graph...'}
            value={input}
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !loading && !gated) {
                e.preventDefault();
                onSend();
              }
            }}
            disabled={gated}
            variant="standard"
            sx={{
              '& .MuiInputBase-root': {
                backgroundColor: 'transparent',
                '&:before, &:after': { display: 'none' },
              },
              '& .MuiInput-underline:before, & .MuiInput-underline:after': {
                display: 'none',
              },
            }}
            slotProps={{
              input: {
                disableUnderline: true,
              },
            }}
          />
          {loading && input.trim() && (
            <Typography
              variant="caption"
              sx={{
                position: 'absolute',
                bottom: -18,
                left: 0,
                color: 'text.secondary',
                fontStyle: 'italic',
                fontSize: '0.65rem',
              }}
            >
              Message will send after AI responds...
            </Typography>
          )}
        </Box>
        {/* Send */}
        <Tooltip title={gated ? (disabledReason ?? '') : 'Send (Enter)'}>
          <span>
            <IconButton
              aria-label={gated ? (disabledReason ?? 'Send disabled') : 'Send (Enter)'}
              onClick={onSend}
              disabled={!input.trim() || loading || gated}
              size="small"
              sx={{
                flexShrink: 0,
                color: input.trim() && !loading && !gated ? 'primary.main' : 'text.disabled',
                transition: 'color 0.15s',
              }}
            >
              <EnterIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>
    </Box>
  );
}
