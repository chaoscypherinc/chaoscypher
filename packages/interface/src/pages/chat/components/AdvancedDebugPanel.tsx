// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Advanced LLM debug information panel.
 *
 * Displays detailed telemetry and debug data for an AI response:
 * - Telemetry HUD with timing stats (total, TTFT, thinking, generation, tokens, speed)
 * - LLM config summary (provider, model, iterations, tool calls)
 * - Collapsible system prompt, initial messages, available tools, and final messages
 */

import { Box, Typography } from '@mui/material';
import type { LLMDebugInfo } from '../../../types';
import type { ToolCall, ToolTimingEntry, DebugMessage } from './message-utils';

interface AdvancedDebugPanelProps {
  /** LLM debug information from the message. */
  llmDebug: LLMDebugInfo;
}

export default function AdvancedDebugPanel({ llmDebug }: AdvancedDebugPanelProps) {
  return (
    <Box sx={{ mt: 2 }}>
      {/* Telemetry HUD -- horizontal stats row */}
      {llmDebug.timing && llmDebug.timing.total_ms && (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0, mb: 2, fontSize: '0.7rem', fontFamily: 'monospace', color: 'rgba(255,255,255,0.6)' }}>
          {[
            llmDebug.timing.total_ms != null && { label: 'Total', value: `${(llmDebug.timing.total_ms / 1000).toFixed(1)}s` },
            llmDebug.timing.time_to_first_token_ms != null && { label: 'TTFT', value: `${(llmDebug.timing.time_to_first_token_ms / 1000).toFixed(1)}s` },
            llmDebug.timing.thinking_ms != null && { label: 'Think', value: `${(llmDebug.timing.thinking_ms / 1000).toFixed(1)}s` },
            llmDebug.timing.generation_ms != null && { label: 'Gen', value: `${(llmDebug.timing.generation_ms / 1000).toFixed(1)}s` },
            llmDebug.timing.output_tokens != null && { label: 'Tokens', value: `~${llmDebug.timing.output_tokens}` },
            llmDebug.timing.tokens_per_sec != null && { label: 'Speed', value: `~${llmDebug.timing.tokens_per_sec} tok/s` },
            llmDebug.timing.tool_calls && llmDebug.timing.tool_calls.length > 0 && { label: 'Tools', value: `${(llmDebug.timing.tool_calls.reduce((s: number, t: ToolTimingEntry) => s + (t.duration_ms || 0), 0) / 1000).toFixed(1)}s` },
          ]
            .filter(
              (stat): stat is { label: string; value: string } => Boolean(stat)
            )
            .map((stat, i, arr) => (
              <Box key={stat.label} sx={{ display: 'flex', alignItems: 'center' }}>
                <Box sx={{ px: 1.5, py: 0.5, textAlign: 'center' }}>
                  <Typography sx={{ fontSize: '0.6rem', color: 'rgba(255,255,255,0.4)', fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: 0.5 }}>{stat.label}</Typography>
                  <Typography sx={{ fontSize: '0.75rem', fontWeight: 600, color: 'secondary.main', fontFamily: 'monospace' }}>{stat.value}</Typography>
                </Box>
                {i < arr.length - 1 && <Box sx={{ width: '1px', height: 24, bgcolor: 'rgba(255,255,255,0.08)' }} />}
              </Box>
            ))}
        </Box>
      )}

      {/* LLM Config -- inline */}
      <Typography sx={{ fontSize: '0.7rem', fontFamily: 'monospace', color: 'rgba(255,255,255,0.5)', mb: 2 }}>
        {llmDebug.provider}/{llmDebug.model} · {llmDebug.iterations} iter · {llmDebug.tool_calls_made} tool calls
      </Typography>

      {/* System Prompt */}
      {llmDebug.initial_messages && llmDebug.initial_messages.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography sx={{ fontSize: '0.65rem', fontFamily: 'monospace', color: 'secondary.main', textTransform: 'uppercase', letterSpacing: 1, mb: 1 }}>
            System Prompt
          </Typography>
          <Box sx={{ background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 1.5, p: 2, maxHeight: 300, overflow: 'auto' }}>
            <pre style={{ margin: 0, fontSize: '0.7rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'rgba(255,255,255,0.6)', fontFamily: 'monospace', lineHeight: 1.6 }}>
              {llmDebug.initial_messages.find((m: DebugMessage) => m.role === 'system')?.content || 'No system prompt'}
            </pre>
          </Box>
        </Box>
      )}

      {/* Faint separator */}
      <Box sx={{ borderBottom: '1px solid rgba(255,255,255,0.06)', my: 2 }} />

      {/* Initial Messages */}
      <Box sx={{ mb: 2 }}>
        <Typography sx={{ fontSize: '0.65rem', fontFamily: 'monospace', color: 'secondary.main', textTransform: 'uppercase', letterSpacing: 1, mb: 1 }}>
          Initial Messages ({llmDebug.initial_messages?.filter((m: DebugMessage) => m.role !== 'system').length || 0})
        </Typography>
        <Box sx={{ background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 1.5, p: 2, maxHeight: 300, overflow: 'auto' }}>
          <pre style={{ margin: 0, fontSize: '0.7rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'rgba(255,255,255,0.6)', fontFamily: 'monospace', lineHeight: 1.6 }}>
            {JSON.stringify(
              llmDebug.initial_messages?.filter((m: DebugMessage) => m.role !== 'system') || [],
              null,
              2
            )}
          </pre>
        </Box>
      </Box>

      {/* Available Tools */}
      {llmDebug.tools && llmDebug.tools.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography sx={{ fontSize: '0.65rem', fontFamily: 'monospace', color: 'secondary.main', textTransform: 'uppercase', letterSpacing: 1, mb: 1 }}>
            Available Tools ({llmDebug.tools.length})
          </Typography>
          <Box sx={{ background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 1.5, p: 2, maxHeight: 300, overflow: 'auto' }}>
            <pre style={{ margin: 0, fontSize: '0.7rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'rgba(255,255,255,0.6)', fontFamily: 'monospace', lineHeight: 1.6 }}>
              {JSON.stringify(
                (llmDebug.tools as ToolCall[]).map((t) => ({
                  name: t.function?.name,
                  description:
                    (t.function?.description ?? '').substring(0, 100) +
                    ((t.function?.description?.length ?? 0) > 100 ? '...' : ''),
                })),
                null,
                2
              )}
            </pre>
          </Box>
        </Box>
      )}

      {/* Final Messages */}
      {llmDebug.final_messages && llmDebug.final_messages.length > (llmDebug.initial_messages?.length ?? 0) && (
        <Box sx={{ mb: 2 }}>
          <Typography sx={{ fontSize: '0.65rem', fontFamily: 'monospace', color: 'secondary.main', textTransform: 'uppercase', letterSpacing: 1, mb: 1 }}>
            Final Messages ({llmDebug.final_messages?.filter((m: DebugMessage) => m.role !== 'system').length || 0})
          </Typography>
          <Box sx={{ background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 1.5, p: 2, maxHeight: 400, overflow: 'auto' }}>
            <pre style={{ margin: 0, fontSize: '0.7rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'rgba(255,255,255,0.6)', fontFamily: 'monospace', lineHeight: 1.6 }}>
              {JSON.stringify(
                llmDebug.final_messages?.filter((m: DebugMessage) => m.role !== 'system') || [],
                null,
                2
              )}
            </pre>
          </Box>
        </Box>
      )}
    </Box>
  );
}
