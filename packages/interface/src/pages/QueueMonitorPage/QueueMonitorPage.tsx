// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * QueueMonitorPage — Monitor and manage background task queues.
 */
import { useState } from 'react';
import {
  Box,
  Typography,
  Alert,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';
import {
  useQueueStats,
  useQueueTasks,
  useCancelTask,
  useCancelTasks,
  useClearTaskHistory,
} from '../../services/api/useQueue';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { useNotification } from '../../contexts/useNotification';
import { ghostDialogPaperSx, ghostButtonSx, ghostCancelBtnSx, ghostErrorAlertSx } from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';
import { LoadingState } from '../../components/LoadingState';
import { QueueToolbar } from './components/QueueToolbar';
import { QueueStatsCards } from './components/QueueStatsCards';
import { TaskTable } from './components/TaskTable';
import { sortTasks } from './utils';
import { logger } from '../../utils/logger';

const PAGE_SIZE = 50;

/** Queue monitor page — stats, toolbar, task list with confirmation dialogs. */
export default function QueueMonitorPage() {
  const { notify } = useNotification();
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshInterval, setRefreshInterval] = useState(5000);
  const [currentPage, setCurrentPage] = useState(1);

  const pollInterval: number | false = autoRefresh ? refreshInterval : false;

  const statsQuery = useQueueStats({ refetchInterval: pollInterval });
  const tasksQuery = useQueueTasks(
    { page: currentPage, page_size: PAGE_SIZE },
    { refetchInterval: pollInterval },
  );

  const cancelTaskMutation = useCancelTask();
  const cancelTasksMutation = useCancelTasks();
  const clearHistoryMutation = useClearTaskHistory();

  // Confirmation dialogs
  const cancelTaskDialog = useConfirmDialog<string>();
  const cancelAllDialog = useConfirmDialog<void>();
  const clearHistoryDialog = useConfirmDialog<void>();

  // Derived data
  const tasks = tasksQuery.data?.data ?? [];
  const pagination = tasksQuery.data?.pagination ?? null;
  const totalInQueue = tasksQuery.data?.total_in_queue ?? 0;
  const stats = statsQuery.data?.queues ?? [];

  const activeTasks = tasks.filter((t) => t.status === 'queued' || t.status === 'running');
  const finishedTasks = tasks.filter((t) =>
    t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled',
  );
  const sortedTasks = sortTasks(tasks);
  const totalQueued = stats.reduce((sum, q) => sum + (q.queued ?? 0), 0);
  const runningTasks = tasks.filter((t) => t.status === 'running').length;
  const failedTasks = tasks.filter((t) => t.status === 'failed').length;
  const displayedTasks = tasks.length;
  const totalTasks = totalInQueue > 0 ? totalInQueue : displayedTasks;
  const totalPages = pagination?.total_pages ?? 0;

  // Handlers
  const handleRefresh = () => {
    void statsQuery.refetch();
    void tasksQuery.refetch();
  };

  const handleCancelTaskClick = (taskId: string) => {
    cancelTaskDialog.open(taskId);
  };

  const handleCancelTaskConfirm = async () => {
    await cancelTaskDialog.confirm(async () => {
      try {
        await cancelTaskMutation.mutateAsync(cancelTaskDialog.data!);
      } catch (error) {
        logger.error('Failed to cancel task:', error);
        notify('Failed to cancel task', 'error');
      }
    });
  };

  const handleCancelAllClick = () => {
    if (activeTasks.length === 0) {
      notify('No active tasks to cancel', 'info');
      return;
    }
    cancelAllDialog.open(undefined as void);
  };

  const handleCancelAllConfirm = async () => {
    await cancelAllDialog.confirm(async () => {
      try {
        const taskIds = activeTasks.map((t) => t.task_id);
        const result = await cancelTasksMutation.mutateAsync(taskIds);
        notify(
          `Successfully cancelled ${result.cancelled_count} of ${result.requested_count} tasks`,
          'success',
        );
      } catch (error) {
        logger.error('Failed to cancel all tasks:', error);
        notify(`Failed to cancel all tasks: ${error}`, 'error');
      }
    });
  };

  const handleClearHistoryClick = () => {
    if (finishedTasks.length === 0) {
      notify('No completed tasks to clear', 'info');
      return;
    }
    clearHistoryDialog.open(undefined as void);
  };

  const handleClearHistoryConfirm = async () => {
    await clearHistoryDialog.confirm(async () => {
      try {
        const result = await clearHistoryMutation.mutateAsync();
        notify(`Successfully cleared ${result.cleared} task(s) from history`, 'success');
      } catch (error) {
        logger.error('Failed to clear history:', error);
        notify('Failed to clear history', 'error');
      }
    });
  };

  const initialLoading = statsQuery.isPending && tasksQuery.isPending;
  if (initialLoading) {
    return <LoadingState message="Loading queue status..." fullPage />;
  }

  const statsError = statsQuery.error;
  const tasksError = tasksQuery.error;

  return (
    <Box>
      <QueueToolbar
        autoRefresh={autoRefresh}
        onAutoRefreshChange={setAutoRefresh}
        refreshInterval={refreshInterval}
        onRefreshIntervalChange={setRefreshInterval}
        onRefresh={handleRefresh}
        onCancelAll={handleCancelAllClick}
        cancellingAll={cancelAllDialog.isConfirming}
        hasActiveTasks={activeTasks.length > 0}
      />

      {statsError && (
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
          {statsError instanceof Error ? statsError.message : String(statsError)}
        </Alert>
      )}
      {tasksError && (
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
          {tasksError instanceof Error ? tasksError.message : String(tasksError)}
        </Alert>
      )}

      <QueueStatsCards
        stats={stats}
        totalTasks={totalTasks}
        displayedTasks={displayedTasks}
        totalQueued={totalQueued}
        runningTasks={runningTasks}
        failedTasks={failedTasks}
      />

      <TaskTable
        tasks={sortedTasks}
        onCancelTask={handleCancelTaskClick}
        onClearHistory={handleClearHistoryClick}
        clearingHistory={clearHistoryDialog.isConfirming}
        hasFinishedTasks={finishedTasks.length > 0}
        currentPage={currentPage}
        totalPages={totalPages}
        totalTasks={totalTasks}
        pageSize={PAGE_SIZE}
        onPageChange={setCurrentPage}
      />

      {/* Cancel Task Confirmation Dialog */}
      <Dialog
        open={cancelTaskDialog.isOpen}
        onClose={cancelTaskDialog.close}
        maxWidth="sm"
        fullWidth
        slotProps={{ paper: { sx: ghostDialogPaperSx } }}
      >
        <DialogTitle sx={{ color: 'text.primary' }}>Cancel Task</DialogTitle>
        <DialogContent>
          <Typography sx={{ color: 'text.secondary' }}>
            Are you sure you want to cancel this task?
          </Typography>
          {cancelTaskDialog.data && (
            <Typography
              sx={{ mt: 1, fontFamily: 'monospace', color: 'text.disabled', fontSize: 13 }}
            >
              Task ID: {cancelTaskDialog.data.slice(0, 16)}...
            </Typography>
          )}
          <Typography sx={{ mt: 1, fontWeight: 'bold', color: 'error.main', fontSize: 13 }}>
            This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={cancelTaskDialog.close} sx={ghostCancelBtnSx}>
            Keep Task
          </Button>
          <Button
            variant="outlined"
            onClick={handleCancelTaskConfirm}
            sx={ghostButtonSx(ChaosCypherPalette.error)}
          >
            Cancel Task
          </Button>
        </DialogActions>
      </Dialog>

      {/* Cancel All Confirmation Dialog */}
      <Dialog
        open={cancelAllDialog.isOpen}
        onClose={cancelAllDialog.close}
        maxWidth="sm"
        fullWidth
        slotProps={{ paper: { sx: ghostDialogPaperSx } }}
      >
        <DialogTitle sx={{ color: 'text.primary' }}>Cancel All Active Tasks</DialogTitle>
        <DialogContent>
          <Typography sx={{ color: 'text.secondary' }}>
            Are you sure you want to cancel ALL {activeTasks.length} active tasks?
          </Typography>
          <Typography sx={{ mt: 1, fontWeight: 'bold', color: 'error.main', fontSize: 13 }}>
            This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={cancelAllDialog.close} sx={ghostCancelBtnSx}>
            Keep Tasks
          </Button>
          <Button
            variant="outlined"
            onClick={handleCancelAllConfirm}
            sx={ghostButtonSx(ChaosCypherPalette.error)}
          >
            Cancel All Tasks
          </Button>
        </DialogActions>
      </Dialog>

      {/* Clear History Confirmation Dialog */}
      <Dialog
        open={clearHistoryDialog.isOpen}
        onClose={clearHistoryDialog.close}
        maxWidth="sm"
        fullWidth
        slotProps={{ paper: { sx: ghostDialogPaperSx } }}
      >
        <DialogTitle sx={{ color: 'text.primary' }}>Clear Task History</DialogTitle>
        <DialogContent>
          <Typography sx={{ color: 'text.secondary' }}>
            Are you sure you want to clear task history?
          </Typography>
          <Typography sx={{ mt: 1, color: 'text.disabled', fontSize: 13 }}>
            This will remove all completed, failed, and cancelled tasks from the display.
          </Typography>
          <Typography sx={{ mt: 1, fontWeight: 'bold', color: 'warning.main', fontSize: 13 }}>
            This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={clearHistoryDialog.close} sx={ghostCancelBtnSx}>
            Keep History
          </Button>
          <Button
            variant="outlined"
            onClick={handleClearHistoryConfirm}
            sx={ghostButtonSx(ChaosCypherPalette.warning)}
          >
            Clear History
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
