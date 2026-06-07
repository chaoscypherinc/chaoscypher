// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Compact tool call display with collapsible raw I/O.
 *
 * Renders as a row with a teal dot indicator, monospace tool name,
 * compact arguments, optional duration, and an expand arrow to reveal
 * the full JSON input/output.
 */

import { useState } from 'react';
import {
  Box,
  Typography,
  Collapse,
} from '@mui/material';
import ExpandIcon from '@mui/icons-material/ExpandMore';
import { CardColors, hexToRgba } from '../../../theme/cardStyles';
import { ChatTheme } from '../../../theme/chatTheme';
import type { ToolCall, ToolResultLike } from './message-utils';

interface ToolCallItemProps {
  /** Display name of the tool function. */
  toolName: string;
  /** Pre-formatted compact argument string. */
  compactArgs: string;
  /** Raw tool call data for input display. */
  call: ToolCall;
  /** Matching tool result message, if available. */
  matchingResult: ToolResultLike | undefined;
  /** Whether this is the last item (controls bottom border). */
  isLast: boolean;
  /** Execution duration in milliseconds. */
  durationMs?: number;
}

export default function ToolCallItem({ toolName, compactArgs, call, matchingResult, isLast, durationMs }: ToolCallItemProps) {
  const [outputExpanded, setOutputExpanded] = useState(false);

  return (
    <Box sx={{
      py: 0.5,
      ...(!isLast && { borderBottom: `1px solid ${hexToRgba(CardColors.info, 0.15)}` }),
    }}>
      {/* Summary row */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          cursor: matchingResult ? 'pointer' : 'default',
          borderRadius: 0.5,
          px: 0.5,
          '&:hover': matchingResult ? {
            backgroundColor: hexToRgba(CardColors.info, 0.06),
          } : {},
          '&:hover .expand-arrow': { opacity: 1 },
        }}
        onClick={() => matchingResult && setOutputExpanded(!outputExpanded)}
      >
        {/* Teal dot indicator */}
        <Box sx={{
          width: 6, height: 6, borderRadius: '50%',
          backgroundColor: matchingResult ? CardColors.info : 'text.disabled',
          flexShrink: 0,
        }} />
        <Typography
          variant="caption"
          sx={{ fontFamily: 'monospace', fontWeight: 600, color: CardColors.info, whiteSpace: 'nowrap' }}
        >
          {toolName}
        </Typography>
        <Typography
          variant="caption"
          sx={{ fontFamily: 'monospace', color: 'text.secondary', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        >
          {compactArgs}
        </Typography>
        {durationMs != null && (
          <Typography
            variant="caption"
            sx={{
              fontFamily: 'monospace',
              color: 'text.disabled',
              ml: 'auto',
              flexShrink: 0,
              whiteSpace: 'nowrap',
            }}
          >
            {(durationMs / 1000).toFixed(1)}s
          </Typography>
        )}
        {matchingResult && (
          <ExpandIcon className="expand-arrow" sx={{
            fontSize: '0.85rem',
            color: 'text.disabled',
            ...(durationMs == null && { ml: 'auto' }),
            flexShrink: 0,
            opacity: outputExpanded ? 1 : 0,
            transform: outputExpanded ? 'rotate(180deg)' : 'none',
            transition: 'transform 0.2s, opacity 0.2s',
          }} />
        )}
        {!matchingResult && durationMs == null && (
          <Typography
            variant="caption"
            sx={{
              color: "text.disabled",
              fontStyle: 'italic',
              ml: 'auto'
            }}>
            Pending...
          </Typography>
        )}
      </Box>
      {/* Collapsible raw I/O */}
      <Collapse in={outputExpanded}>
        <Box sx={{
          mt: 0.5, ml: 2.5, p: 1,
          backgroundColor: ChatTheme.tools.outputBg,
          borderRadius: 1,
        }}>
          {/* Raw input */}
          <Typography
            variant="caption"
            sx={{
              color: "text.disabled",
              fontSize: '0.65rem'
            }}>
            Input:
          </Typography>
          <pre style={{ margin: 0, fontSize: '0.7rem', overflow: 'auto', maxHeight: '150px', opacity: 0.7 }}>
            {(() => {
              const args = call.function?.arguments;
              if (!args) return '{}';
              try {
                const parsed = typeof args === 'string' ? JSON.parse(args) : args;
                return JSON.stringify(parsed, null, 2);
              } catch {
                return String(args);
              }
            })()}
          </pre>
          {/* Raw output */}
          <Typography
            variant="caption"
            sx={{
              color: "text.disabled",
              fontSize: '0.65rem',
              mt: 0.5,
              display: 'block'
            }}>
            Output:
          </Typography>
          {matchingResult ? (
            <pre style={{ margin: 0, fontSize: '0.7rem', overflow: 'auto', maxHeight: '250px', backgroundColor: ChatTheme.tools.outputSuccessBg, padding: '4px', borderRadius: '4px' }}>
              {(() => {
                const resultContent = matchingResult.content;
                if (!resultContent) return 'No output';
                try {
                  const parsed = typeof resultContent === 'string' ? JSON.parse(resultContent) : resultContent;
                  return JSON.stringify(parsed, null, 2);
                } catch {
                  return String(resultContent);
                }
              })()}
            </pre>
          ) : (
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                fontStyle: 'italic'
              }}>
              Pending...
            </Typography>
          )}
        </Box>
      </Collapse>
    </Box>
  );
}
