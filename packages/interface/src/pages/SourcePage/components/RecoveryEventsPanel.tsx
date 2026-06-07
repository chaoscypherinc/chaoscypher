// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import AutorenewIcon from '@mui/icons-material/Autorenew';
import { useSourceRecoveryEvents } from '../../../services/api/sourceRecoveryEvents';

interface RecoveryEventsPanelProps {
  sourceId: string;
  recoveryAttempts: number;
}

/**
 * Collapsible audit-trail panel surfaced under the recovery banner.
 *
 * Lists the most-recent recovery events written by SourceRecovery so
 * operators can see exactly which recoveries fired, when, why, and
 * what action was dispatched — without grepping container logs.
 *
 * The panel is collapsed by default and only fetches when expanded
 * (`enabled` gates the TanStack Query call), so it costs nothing on
 * source pages where the operator isn't investigating.
 */
export function RecoveryEventsPanel({
  sourceId,
  recoveryAttempts,
}: RecoveryEventsPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading, isError } = useSourceRecoveryEvents(sourceId, expanded);

  // Don't render if no recoveries have happened (panel would be empty).
  if (recoveryAttempts === 0) {
    return null;
  }

  const events = data?.events ?? [];

  return (
    <Accordion
      expanded={expanded}
      onChange={(_, e) => setExpanded(e)}
      sx={{ mb: 2 }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <AutorenewIcon fontSize="small" />
          <Typography variant="subtitle2">Recovery events</Typography>
          <Chip
            size="small"
            label={`${recoveryAttempts} attempt${recoveryAttempts === 1 ? '' : 's'}`}
            variant="outlined"
          />
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        {isLoading && (
          <Typography variant="body2" color="text.secondary">
            Loading recovery events…
          </Typography>
        )}
        {isError && (
          <Typography variant="body2" color="error.main">
            Failed to load recovery events. Check container logs.
          </Typography>
        )}
        {!isLoading && !isError && events.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            No recovery events recorded yet. The source-recovery audit
            trail starts collecting from the moment the table was added
            (migration 0016) — older recoveries on this source predate
            the table and are not visible here.
          </Typography>
        )}
        {events.length > 0 && (
          <TableContainer>
            <Table size="small" aria-label="Recovery events">
              <TableHead>
                <TableRow>
                  <TableCell>When</TableCell>
                  <TableCell>From status</TableCell>
                  <TableCell>Action</TableCell>
                  <TableCell>Reason</TableCell>
                  <TableCell align="right">Tasks enqueued</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {events.map((e) => (
                  <TableRow key={e.id} hover>
                    <TableCell>
                      <Box
                        component="time"
                        dateTime={e.attempt_at}
                        title={new Date(e.attempt_at).toISOString()}
                      >
                        {new Date(e.attempt_at).toLocaleString()}
                      </Box>
                    </TableCell>
                    <TableCell>
                      <Chip size="small" label={e.from_status} variant="outlined" />
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                        {e.action_taken}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">
                        {e.reason}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">{e.enqueued_count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </AccordionDetails>
    </Accordion>
  );
}
