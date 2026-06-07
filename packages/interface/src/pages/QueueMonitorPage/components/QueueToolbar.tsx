// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * QueueToolbar — Page header with auto-refresh controls and bulk actions.
 */

import {
  alpha,
  Box,
  Typography,
  IconButton,
  FormControlLabel,
  Switch,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Tooltip,
  Button,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import ClearAllIcon from '@mui/icons-material/ClearAll';
import {
  ghostInputSx,
  ghostButtonSx,
  ghostSwitchSx,
} from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';

interface QueueToolbarProps {
  /** Whether auto-refresh is currently on. */
  autoRefresh: boolean;
  /** Callback to toggle auto-refresh. */
  onAutoRefreshChange: (enabled: boolean) => void;
  /** Current refresh interval in milliseconds. */
  refreshInterval: number;
  /** Callback to change the refresh interval. */
  onRefreshIntervalChange: (ms: number) => void;
  /** Trigger an immediate manual refresh. */
  onRefresh: () => void;
  /** Trigger the "cancel all" confirmation flow. */
  onCancelAll: () => void;
  /** Whether a bulk cancel is in progress. */
  cancellingAll: boolean;
  /** Whether the cancel-all button should be disabled (no active tasks). */
  hasActiveTasks: boolean;
}

/** Page header: title, auto-refresh toggle, interval selector, refresh + cancel-all buttons. */
export function QueueToolbar({
  autoRefresh,
  onAutoRefreshChange,
  refreshInterval,
  onRefreshIntervalChange,
  onRefresh,
  onCancelAll,
  cancellingAll,
  hasActiveTasks,
}: QueueToolbarProps) {
  return (
    <Box
      sx={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 2,
        justifyContent: 'space-between',
        alignItems: { xs: 'flex-start', sm: 'center' },
        mb: 3,
      }}
    >
      <Typography variant="h4">Queues</Typography>

      <Box sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 2 }}>
        <FormControlLabel
          control={
            <Switch
              checked={autoRefresh}
              onChange={(e) => onAutoRefreshChange(e.target.checked)}
              sx={ghostSwitchSx}
            />
          }
          label="Auto-refresh"
          sx={{ '& .MuiFormControlLabel-label': { fontSize: 13, color: 'text.secondary' } }}
        />

        <FormControl size="small" sx={{ minWidth: 120, ...ghostInputSx }}>
          <InputLabel sx={{ color: 'text.disabled' }}>Interval</InputLabel>
          <Select
            value={refreshInterval}
            label="Interval"
            onChange={(e) => onRefreshIntervalChange(Number(e.target.value))}
          >
            <MenuItem value={1000}>1s</MenuItem>
            <MenuItem value={5000}>5s</MenuItem>
            <MenuItem value={10000}>10s</MenuItem>
            <MenuItem value={30000}>30s</MenuItem>
          </Select>
        </FormControl>

        <Tooltip title="Refresh now">
          <IconButton
            aria-label="Refresh now"
            onClick={onRefresh}
            sx={{
              color: 'primary.main',
              '&:hover': { bgcolor: alpha(ChaosCypherPalette.primary, 0.08) },
            }}
          >
            <RefreshIcon />
          </IconButton>
        </Tooltip>

        <Tooltip title="Cancel all active tasks">
          <span>
            <Button
              variant="outlined"
              startIcon={<ClearAllIcon />}
              onClick={onCancelAll}
              disabled={cancellingAll || !hasActiveTasks}
              size="small"
              sx={ghostButtonSx(ChaosCypherPalette.error)}
            >
              {cancellingAll ? 'Cancelling...' : 'Cancel All'}
            </Button>
          </span>
        </Tooltip>
      </Box>
    </Box>
  );
}
