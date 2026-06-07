// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Fragment, useState } from 'react';
import {
  Box,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import EntitiesIcon from '@mui/icons-material/AccountTree';
import RelationsIcon from '@mui/icons-material/Link';
import TimeIcon from '@mui/icons-material/Timer';

import type { ChunkAttemptSummary, ChunkAttemptDetail } from '../../../services/api/sourceProcessing';
import { fetchChunkAttempt } from '../../../services/api/useChunkRerun';
import { formatDurationMs } from '../../../utils/formatters';
import { ghostTableHeadCellSx, ghostTableRowSx } from '../../../theme/ghostStyles';
import { ParsedLLMResponse } from './ParsedLLMResponse';
import { FilteringLogPanel } from './FilteringLogPanel';

interface ChunkAttemptsListProps {
  sourceId: string;
  chunkIndex: number;
  attempts: ChunkAttemptSummary[];
}

/**
 * Prior-attempt history section inside the expanded chunk panel.
 *
 * Renders one row per snapshotted attempt with headline numbers
 * (entities, relationships, duration, finish_reason). Expand a row to
 * lazy-fetch the full attempt body (input_text, llm_response_json,
 * filtering_log).
 */
export function ChunkAttemptsList({ sourceId, chunkIndex, attempts }: ChunkAttemptsListProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, { loading: boolean; data?: ChunkAttemptDetail }>>({});

  async function toggleExpand(attemptId: string) {
    if (expanded === attemptId) {
      setExpanded(null);
      return;
    }
    setExpanded(attemptId);
    if (!details[attemptId]?.data) {
      setDetails((d) => ({ ...d, [attemptId]: { loading: true } }));
      try {
        const full = await fetchChunkAttempt(sourceId, chunkIndex, attemptId);
        setDetails((d) => ({ ...d, [attemptId]: { loading: false, data: full } }));
      } catch {
        setDetails((d) => ({ ...d, [attemptId]: { loading: false } }));
      }
    }
  }

  if (attempts.length === 0) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          No prior attempts. This chunk has not been rerun.
        </Typography>
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>
        Prior attempts ({attempts.length})
      </Typography>
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell width={40} sx={ghostTableHeadCellSx}></TableCell>
              <TableCell sx={ghostTableHeadCellSx}>Attempt</TableCell>
              <TableCell sx={ghostTableHeadCellSx}>Duration</TableCell>
              <TableCell sx={ghostTableHeadCellSx}>Result</TableCell>
              <TableCell sx={ghostTableHeadCellSx}>Finish</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {attempts.map((a) => (
              <Fragment key={a.id}>
                <TableRow
                  hover
                  onClick={() => toggleExpand(a.id)}
                  sx={{ ...ghostTableRowSx, cursor: 'pointer' }}
                >
                  <TableCell>
                    <IconButton aria-label={expanded === a.id ? "Collapse" : "Expand"} size="small" sx={{ pointerEvents: 'none' }}>
                      {expanded === a.id ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                    </IconButton>
                  </TableCell>
                  <TableCell>Attempt {a.attempt_number}</TableCell>
                  <TableCell>
                    <Tooltip title="LLM call duration" arrow>
                      <Chip
                        icon={<TimeIcon sx={{ fontSize: 13 }} />}
                        label={formatDurationMs(a.llm_duration_ms ?? 0)}
                        size="small"
                        variant="outlined"
                      />
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    <Box sx={{ display: 'flex', gap: 0.5 }}>
                      <Chip
                        icon={<EntitiesIcon sx={{ fontSize: 13 }} />}
                        label={a.entity_count}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        icon={<RelationsIcon sx={{ fontSize: 13 }} />}
                        label={a.relationship_count}
                        size="small"
                        variant="outlined"
                      />
                    </Box>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                      {a.finish_reason ?? '—'}
                    </Typography>
                  </TableCell>
                </TableRow>
                <TableRow key={`${a.id}-d`}>
                  <TableCell colSpan={5} sx={{ p: 0, borderBottom: 'none' }}>
                    <Collapse in={expanded === a.id}>
                      <Box sx={{ p: 2 }}>
                        {details[a.id]?.loading ? (
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <CircularProgress size={14} />
                            <Typography variant="caption">Loading…</Typography>
                          </Box>
                        ) : details[a.id]?.data ? (
                          <AttemptDetailContent detail={details[a.id]!.data!} />
                        ) : (
                          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                            Detail unavailable.
                          </Typography>
                        )}
                      </Box>
                    </Collapse>
                  </TableCell>
                </TableRow>
              </Fragment>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}

function AttemptDetailContent({ detail }: { detail: ChunkAttemptDetail }) {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      {detail.input_text && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Input text</Typography>
          <Box
            sx={{
              p: 1.5,
              maxHeight: 240,
              overflow: 'auto',
              background: 'rgba(0, 0, 0, 0.3)',
              border: '1px solid rgba(255, 255, 255, 0.06)',
              borderRadius: 1.5,
            }}
          >
            <Typography
              variant="body2"
              sx={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: '0.85rem' }}
            >
              {detail.input_text}
            </Typography>
          </Box>
        </Box>
      )}
      {detail.llm_response_json && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 0.5 }}>LLM response</Typography>
          <ParsedLLMResponse jsonString={detail.llm_response_json} maxHeight={300} />
        </Box>
      )}
      {detail.filtering_log && (detail.filtering_log as { total_removed?: number }).total_removed !== undefined && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Pipeline filtering</Typography>
          <FilteringLogPanel
            filteringLog={detail.filtering_log as unknown as Parameters<typeof FilteringLogPanel>[0]['filteringLog']}
            compact
          />
        </Box>
      )}
    </Box>
  );
}
