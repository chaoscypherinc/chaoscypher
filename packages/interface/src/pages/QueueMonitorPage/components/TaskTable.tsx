// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TaskTable — Task list with status chips, duration, actions, and pagination.
 */

import {
  alpha,
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
  Button,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import type { QueueTask } from '../../../services/api/useQueue';
import { glassPanelSx } from '../../../theme/cardStyles';
import {
  ghostButtonSx,
  ghostTableHeadCellSx,
  ghostTableRowSx,
} from '../../../theme/ghostStyles';
import { ChaosCypherPalette, ChaosCypherNeutrals } from '../../../theme/palette';
import GhostPagination from '../../../components/GhostPagination';
import { formatTaskDuration } from '../../../utils/formatters';
import {
  getStatusColor,
  getPriorityColor,
  getTaskDescription,
  getTaskDetails,
} from '../utils';

interface TaskTableProps {
  /** Sorted task list for the current page. */
  tasks: QueueTask[];
  /** Callback when user clicks cancel on a single task. */
  onCancelTask: (taskId: string) => void;
  /** Callback to initiate the clear-history confirmation flow. */
  onClearHistory: () => void;
  /** Whether clearing is in progress. */
  clearingHistory: boolean;
  /** Whether there are any completed/failed/cancelled tasks to clear. */
  hasFinishedTasks: boolean;
  /** 1-based current page index. */
  currentPage: number;
  /** Total pages available. */
  totalPages: number;
  /** Total task count (for pagination label). */
  totalTasks: number;
  /** Page size. */
  pageSize: number;
  /** Callback when user changes page (1-based page number). */
  onPageChange: (page: number) => void;
}

const COLUMN_HEADERS = [
  'Task ID',
  'Queue',
  'Description',
  'Details',
  'Status',
  'Priority',
  'Duration',
  'Attempts',
  '',
] as const;

/** Task table panel with header, rows, and pagination. */
export function TaskTable({
  tasks,
  onCancelTask,
  onClearHistory,
  clearingHistory,
  hasFinishedTasks,
  currentPage,
  totalPages,
  totalTasks,
  pageSize,
  onPageChange,
}: TaskTableProps) {
  return (
    <Box sx={{ ...glassPanelSx, p: 3 }}>
      {/* Section header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h6" sx={{ color: 'text.primary' }}>
          Active Tasks
        </Typography>
        <Tooltip title="Clear completed, failed, and cancelled tasks">
          <span>
            <Button
              variant="outlined"
              startIcon={<DeleteIcon />}
              onClick={onClearHistory}
              disabled={clearingHistory || !hasFinishedTasks}
              size="small"
              sx={ghostButtonSx(ChaosCypherPalette.warning)}
            >
              {clearingHistory ? 'Clearing...' : 'Clear History'}
            </Button>
          </span>
        </Tooltip>
      </Box>

      {/* Table */}
      <TableContainer>
        <Table>
          <TableHead>
            <TableRow>
              {COLUMN_HEADERS.map((h) => (
                <TableCell
                  key={h || 'actions'}
                  align={h === '' ? 'right' : 'left'}
                  sx={ghostTableHeadCellSx}
                >
                  {h || 'Actions'}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {tasks.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={9}
                  align="center"
                  sx={{ borderColor: 'rgba(255, 255, 255, 0.04)' }}
                >
                  <Typography
                    variant="body2"
                    sx={{ color: ChaosCypherNeutrals.textMuted, py: 3 }}
                  >
                    No active tasks
                  </Typography>
                </TableCell>
              </TableRow>
            ) : (
              tasks.map((task) => <TaskRow key={task.task_id} task={task} onCancel={onCancelTask} />)
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* Pagination */}
      {totalPages > 1 && (
        <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
          <GhostPagination
            page={currentPage}
            totalPages={totalPages}
            total={totalTasks}
            pageSize={pageSize}
            onPageChange={onPageChange}
          />
        </Box>
      )}
    </Box>
  );
}

// ── Row sub-component ─────────────────────────────────────────────────────

interface TaskRowProps {
  task: QueueTask;
  onCancel: (taskId: string) => void;
}

/** Single task row with status, priority, duration, and cancel action. */
function TaskRow({ task, onCancel }: TaskRowProps) {
  const details = getTaskDetails(task);
  const isActive = task.status === 'queued' || task.status === 'running';

  return (
    <TableRow sx={ghostTableRowSx}>
      <TableCell>
        <Typography
          variant="body2"
          sx={{ fontFamily: 'monospace', color: 'text.secondary' }}
        >
          {task.task_id.slice(0, 8)}...
        </Typography>
      </TableCell>
      <TableCell>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          {task.queue}
        </Typography>
      </TableCell>
      <TableCell>
        <Typography variant="body2" sx={{ fontWeight: 500, color: 'text.primary' }}>
          {getTaskDescription(task)}
        </Typography>
      </TableCell>
      <TableCell>
        <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
          {details.map((detail) => (
            <Chip
              key={detail}
              label={detail}
              size="small"
              variant="outlined"
              sx={{ borderColor: 'rgba(255, 255, 255, 0.1)', color: 'text.secondary' }}
            />
          ))}
          {task.error && (
            <Tooltip title={task.error}>
              <Chip
                label="Error"
                size="small"
                variant="outlined"
                sx={{
                  borderColor: alpha(ChaosCypherPalette.error, 0.3),
                  color: 'error.main',
                }}
              />
            </Tooltip>
          )}
        </Box>
      </TableCell>
      <TableCell>
        <Chip label={task.status} color={getStatusColor(task.status)} size="small" />
      </TableCell>
      <TableCell>
        <Chip
          label={task.priority}
          color={getPriorityColor(task.priority)}
          size="small"
          variant="outlined"
        />
      </TableCell>
      <TableCell sx={{ color: 'text.secondary' }}>
        {task.started_at ? formatTaskDuration(task.started_at, task.completed_at) : '-'}
      </TableCell>
      <TableCell sx={{ color: 'text.secondary' }}>{task.attempts || 0}</TableCell>
      <TableCell align="right">
        {isActive && (
          <Tooltip title="Cancel task">
            <IconButton
              aria-label="Cancel task"
              size="small"
              onClick={() => onCancel(task.task_id)}
              sx={{
                color: 'error.main',
                '&:hover': { bgcolor: alpha(ChaosCypherPalette.error, 0.08) },
              }}
            >
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
      </TableCell>
    </TableRow>
  );
}
