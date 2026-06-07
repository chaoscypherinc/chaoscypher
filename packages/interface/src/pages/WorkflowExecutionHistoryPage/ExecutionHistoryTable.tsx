// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import React from 'react';
import {
  Box,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  IconButton,
  Tooltip,
  CircularProgress,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  TablePagination,
} from '@mui/material';
import VisibilityIcon from '@mui/icons-material/Visibility';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import CancelIcon from '@mui/icons-material/Cancel';
import { glassPanelSx } from '../../theme/cardStyles';
import {
  ghostInputSx,
  ghostTableHeadCellSx,
  ghostTableRowSx,
} from '../../theme/ghostStyles';
import type { WorkflowExecution } from '../../services/api/workflows';
import { formatDurationMs } from '../../utils/formatters';

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <HourglassEmptyIcon fontSize="small" />,
  running: <PlayArrowIcon fontSize="small" />,
  completed: <CheckCircleIcon fontSize="small" />,
  failed: <ErrorIcon fontSize="small" />,
  cancelled: <CancelIcon fontSize="small" />,
};

const STATUS_COLORS: Record<string, 'default' | 'primary' | 'secondary' | 'error' | 'info' | 'success' | 'warning'> = {
  pending: 'default',
  running: 'info',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
};

interface ExecutionHistoryTableProps {
  executions: WorkflowExecution[];
  statusFilter: string;
  page: number;
  rowsPerPage: number;
  onStatusFilterChange: (status: string) => void;
  onPageChange: (page: number) => void;
  onRowsPerPageChange: (rowsPerPage: number) => void;
  onViewDetails: (executionId: string) => void;
}

export const ExecutionHistoryTable: React.FC<ExecutionHistoryTableProps> = ({
  executions,
  statusFilter,
  page,
  rowsPerPage,
  onStatusFilterChange,
  onPageChange,
  onRowsPerPageChange,
  onViewDetails,
}) => {
  const formatDuration = (ms?: number): string => formatDurationMs(ms ?? null);

  const formatTime = (timestamp?: string): string => {
    if (!timestamp) return '-';
    return new Date(timestamp).toLocaleString();
  };

  const filteredExecutions = statusFilter === 'all'
    ? executions
    : executions.filter(e => e.status === statusFilter);

  const paginatedExecutions = filteredExecutions.slice(
    page * rowsPerPage,
    page * rowsPerPage + rowsPerPage
  );

  return (
    <>
      {/* Filter Controls */}
      <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
        <FormControl size="small" sx={{ minWidth: 150, ...ghostInputSx }}>
          <InputLabel>Status</InputLabel>
          <Select
            value={statusFilter}
            label="Status"
            onChange={(e) => {
              onStatusFilterChange(e.target.value);
              onPageChange(0);
            }}
          >
            <MenuItem value="all">All</MenuItem>
            <MenuItem value="completed">Completed</MenuItem>
            <MenuItem value="failed">Failed</MenuItem>
            <MenuItem value="running">Running</MenuItem>
            <MenuItem value="pending">Pending</MenuItem>
            <MenuItem value="cancelled">Cancelled</MenuItem>
          </Select>
        </FormControl>
      </Box>
      {/* Executions Table */}
      <TableContainer sx={{ ...glassPanelSx, bgcolor: 'transparent', borderRadius: '8px' }}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell sx={ghostTableHeadCellSx}>Status</TableCell>
              <TableCell sx={ghostTableHeadCellSx}>Started</TableCell>
              <TableCell sx={ghostTableHeadCellSx}>Duration</TableCell>
              <TableCell sx={ghostTableHeadCellSx}>Triggered By</TableCell>
              <TableCell sx={ghostTableHeadCellSx}>Execution ID</TableCell>
              <TableCell align="right" sx={ghostTableHeadCellSx}>Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {paginatedExecutions.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} align="center" sx={{ py: 4, borderColor: 'rgba(255, 255, 255, 0.04)' }}>
                  <Typography sx={{
                    color: "text.secondary"
                  }}>
                    No executions found
                  </Typography>
                </TableCell>
              </TableRow>
            ) : (
              paginatedExecutions.map((execution) => (
                <TableRow
                  key={execution.id}
                  sx={{ cursor: 'pointer', ...ghostTableRowSx }}
                  onClick={() => onViewDetails(execution.id)}
                >
                  <TableCell>
                    <Chip
                      icon={execution.status === 'running' ? <CircularProgress size={14} /> : STATUS_ICONS[execution.status] as React.ReactElement}
                      label={execution.status}
                      size="small"
                      color={STATUS_COLORS[execution.status]}
                    />
                  </TableCell>
                  <TableCell>{formatTime(execution.created_at)}</TableCell>
                  <TableCell>{formatDuration(execution.duration_ms)}</TableCell>
                  <TableCell>
                    <Chip label={execution.triggered_by} size="small" variant="outlined" />
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                      {execution.id.substring(0, 8)}...
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Tooltip title="View Details">
                      <IconButton
                        aria-label="View Details"
                        size="small"
                        onClick={(e) => {
                          e.stopPropagation();
                          onViewDetails(execution.id);
                        }}
                        sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}
                      >
                        <VisibilityIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
        <TablePagination
          component="div"
          count={filteredExecutions.length}
          page={page}
          onPageChange={(_, newPage) => onPageChange(newPage)}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={(e) => {
            onRowsPerPageChange(parseInt(e.target.value, 10));
            onPageChange(0);
          }}
          rowsPerPageOptions={[10, 25, 50]}
          sx={{
            borderTop: '1px solid rgba(255, 255, 255, 0.06)',
            color: 'rgba(255, 255, 255, 0.5)',
            '& .MuiTablePagination-selectIcon': { color: 'rgba(255, 255, 255, 0.3)' },
            '& .MuiIconButton-root': { color: 'rgba(255, 255, 255, 0.4)' },
            '& .MuiIconButton-root.Mui-disabled': { color: 'rgba(255, 255, 255, 0.1)' },
          }}
        />
      </TableContainer>
    </>
  );
};
