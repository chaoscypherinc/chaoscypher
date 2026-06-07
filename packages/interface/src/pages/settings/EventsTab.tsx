// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Box,
  Typography,
  Chip,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Button,
} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutlined';
import { useSystemEvents } from '../../hooks/useSystemEvents';
import { ghostTableHeadCellSx, ghostTableRowSx } from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';

/** Event type filter options. */
const EVENT_FILTERS = [
  { label: 'All', value: undefined },
  { label: 'Started', value: 'task_started' },
  { label: 'Completed', value: 'task_completed' },
  { label: 'Failed', value: 'task_failed' },
  { label: 'Health', value: 'health_change' },
  { label: 'Pause', value: 'pause' },
  { label: 'Recovery', value: 'recovery' },
] as const;

/** Map event types to badge colors. */
const TYPE_COLORS: Record<string, string> = {
  pause: ChaosCypherPalette.orange,
  resume: ChaosCypherPalette.success,
  health_change: ChaosCypherPalette.warning,
  recovery: ChaosCypherPalette.primary,
  task_started: ChaosCypherPalette.primary,
  task_completed: ChaosCypherPalette.success,
  task_failed: ChaosCypherPalette.error,
  file_uploaded: ChaosCypherPalette.primary,
  database_created: ChaosCypherPalette.success,
  database_deleted: ChaosCypherPalette.orange,
  trigger_fired: ChaosCypherPalette.warning,
};

/** Format a timestamp as time + relative hint (e.g., "14:32 (2m ago)"). */
function formatEventTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return timestamp;

  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const diffSec = Math.floor((Date.now() - date.getTime()) / 1000);
  if (diffSec < 60) return `${time} (now)`;
  if (diffSec < 3600) return `${time} (${Math.floor(diffSec / 60)}m)`;
  if (diffSec < 86400) return `${time} (${Math.floor(diffSec / 3600)}h)`;
  if (diffSec < 172800) return `Yesterday ${time}`;
  return `${Math.floor(diffSec / 86400)}d ago ${time}`;
}

export default function EventsTab() {
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);
  const { events, loading, clearEvents } = useSystemEvents(100, typeFilter);
  const [clearing, setClearing] = useState(false);

  const handleClear = async () => {
    setClearing(true);
    try {
      await clearEvents();
    } finally {
      setClearing(false);
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      {/* Filter chips + clear button */}
      <Box sx={{ display: 'flex', gap: 1, mb: 2, flexWrap: 'wrap', alignItems: 'center' }}>
        {EVENT_FILTERS.map((filter) => (
          <Chip
            key={filter.label}
            label={filter.label}
            size="small"
            variant={typeFilter === filter.value ? 'filled' : 'outlined'}
            onClick={() => setTypeFilter(filter.value)}
            sx={{
              borderColor: typeFilter === filter.value
                ? ChaosCypherPalette.primary
                : 'rgba(255,255,255,0.15)',
              color: typeFilter === filter.value
                ? ChaosCypherPalette.primary
                : 'rgba(255,255,255,0.6)',
              bgcolor: typeFilter === filter.value
                ? `${ChaosCypherPalette.primary}18`
                : 'transparent',
              '&:hover': {
                bgcolor: `${ChaosCypherPalette.primary}12`,
              },
            }}
          />
        ))}
        <Box sx={{ flex: 1 }} />
        <Button
          size="small"
          variant="outlined"
          startIcon={clearing ? <CircularProgress size={14} color="inherit" /> : <DeleteOutlineIcon />}
          disabled={clearing || loading || events.length === 0}
          onClick={handleClear}
          sx={{
            textTransform: 'none',
            fontSize: '0.75rem',
            color: 'rgba(255,255,255,0.5)',
            borderColor: 'rgba(255,255,255,0.12)',
            '&:hover': {
              borderColor: ChaosCypherPalette.error,
              color: ChaosCypherPalette.error,
              bgcolor: `${ChaosCypherPalette.error}0a`,
            },
          }}
        >
          Clear Events
        </Button>
      </Box>

      {/* Events table */}
      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress size={28} sx={{ color: ChaosCypherPalette.primary }} />
        </Box>
      ) : events.length === 0 ? (
        <Box sx={{ py: 6, textAlign: 'center' }}>
          <Typography
            variant="body2"
            sx={{ color: 'rgba(255,255,255,0.35)', fontStyle: 'italic' }}
          >
            No events recorded
          </Typography>
        </Box>
      ) : (
        <TableContainer
          sx={{
            maxHeight: 520,
            overflowY: 'auto',
            border: '1px solid rgba(255,255,255,0.06)',
            borderRadius: 1,
            background: 'rgba(0,0,0,0.15)',
          }}
        >
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell sx={{ ...ghostTableHeadCellSx, width: 130 }}>Time</TableCell>
                <TableCell sx={{ ...ghostTableHeadCellSx, width: 110 }}>Type</TableCell>
                <TableCell sx={ghostTableHeadCellSx}>Event</TableCell>
                <TableCell sx={{ ...ghostTableHeadCellSx, width: 120 }}>Source</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {events.map((event) => {
                const typeColor = TYPE_COLORS[event.type] || 'rgba(255,255,255,0.5)';

                return (
                  <TableRow key={event.id} sx={ghostTableRowSx}>
                    <TableCell
                      sx={{
                        color: 'rgba(255,255,255,0.4)',
                        fontSize: '0.75rem',
                        fontFamily: "'SF Mono', 'Cascadia Code', 'Fira Code', monospace",
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {formatEventTime(event.timestamp)}
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={event.type.replace(/_/g, ' ')}
                        size="small"
                        sx={{
                          height: 20,
                          fontSize: '0.65rem',
                          fontWeight: 600,
                          letterSpacing: '0.5px',
                          textTransform: 'uppercase',
                          color: typeColor,
                          bgcolor: `${typeColor}18`,
                          border: `1px solid ${typeColor}30`,
                        }}
                      />
                    </TableCell>
                    <TableCell
                      sx={{
                        color: 'rgba(255,255,255,0.8)',
                        fontSize: '0.8rem',
                      }}
                    >
                      {event.action}
                    </TableCell>
                    <TableCell
                      sx={{
                        color: 'rgba(255,255,255,0.4)',
                        fontSize: '0.75rem',
                        fontFamily: "'SF Mono', 'Cascadia Code', 'Fira Code', monospace",
                      }}
                    >
                      {event.source || '\u2014'}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* Footer count */}
      {!loading && events.length > 0 && (
        <Typography
          variant="caption"
          sx={{ display: 'block', mt: 1.5, color: 'rgba(255,255,255,0.25)' }}
        >
          Showing {events.length} event{events.length !== 1 ? 's' : ''} (auto-pruned server-side)
        </Typography>
      )}
    </Box>
  );
}
