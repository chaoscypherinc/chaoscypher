// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowExecutionHistoryPage: Dedicated page for viewing workflow execution history
 *
 * Displays comprehensive execution history with filtering, detailed views,
 * and the ability to view step-by-step execution details.
 */

import React, { useState, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router';
import {
  Box,
  Alert,
  Button,
} from '@mui/material';
import { useWorkflow } from '../../services/api/useWorkflows';
import {
  useWorkflowExecutions,
  useWorkflowExecution,
  useWorkflowStats,
} from '../../services/api/useWorkflowExecutions';
import { POLLING_INTERVALS } from '../../constants/config';
import { LoadingState } from '../../components/LoadingState';
import { ghostErrorAlertSx } from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';
import { ExecutionDetailDialog } from '../ExecutionDetailDialog';
import { ExecutionHistoryHeader } from './ExecutionHistoryHeader';
import { ExecutionHistoryTable } from './ExecutionHistoryTable';

const WorkflowExecutionHistoryPage: React.FC = () => {
  const { workflowId } = useParams<{ workflowId: string }>();
  const navigate = useNavigate();

  // UI state
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);

  // Detail dialog state
  const [selectedExecutionId, setSelectedExecutionId] = useState<string | null>(null);
  const [isDetailOpen, setIsDetailOpen] = useState(false);

  const workflowQuery = useWorkflow(workflowId);
  // Auto-refresh while an execution is running/pending: the hook drives the
  // polling internally via `refetchInterval` and tears it down on unmount.
  const executionsQuery = useWorkflowExecutions(workflowId, {
    maxItems: 100,
    pollInterval: POLLING_INTERVALS.EXECUTION_HISTORY,
  });
  const statsQuery = useWorkflowStats(workflowId);

  const executions = useMemo(
    () => (Array.isArray(executionsQuery.data) ? executionsQuery.data : []),
    [executionsQuery.data],
  );

  const detailQuery = useWorkflowExecution(workflowId, selectedExecutionId);

  const isLoading = workflowQuery.isLoading || executionsQuery.isLoading || statsQuery.isLoading;
  const isError = workflowQuery.isError || executionsQuery.isError || statsQuery.isError;

  const refetchAll = () => {
    void workflowQuery.refetch();
    void executionsQuery.refetch();
    void statsQuery.refetch();
  };

  // View execution details
  const handleViewDetails = (executionId: string) => {
    if (!workflowId) return;
    setSelectedExecutionId(executionId);
    setIsDetailOpen(true);
  };

  if (isLoading) {
    return (
      <Box sx={{ p: { xs: 2, md: 3 } }}>
        <LoadingState message="Loading execution history..." />
      </Box>
    );
  }

  if (isError) {
    return (
      <Box sx={{ p: { xs: 2, md: 3 } }}>
        <Alert
          severity="error"
          sx={ghostErrorAlertSx}
          action={
            <Button
              size="small"
              onClick={refetchAll}
              sx={{ color: '#ff6b8a', '&:hover': { bgcolor: `${ChaosCypherPalette.error}1A` } }}
            >
              Retry
            </Button>
          }
        >
          Failed to load execution history
        </Alert>
      </Box>
    );
  }

  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <ExecutionHistoryHeader
        workflow={workflowQuery.data ?? null}
        stats={statsQuery.data ?? null}
        onBack={() => navigate('/automations')}
        onEdit={() => navigate(`/automations/builder/${workflowId}`)}
        onRefresh={refetchAll}
      />
      <ExecutionHistoryTable
        executions={executions}
        statusFilter={statusFilter}
        page={page}
        rowsPerPage={rowsPerPage}
        onStatusFilterChange={setStatusFilter}
        onPageChange={setPage}
        onRowsPerPageChange={setRowsPerPage}
        onViewDetails={handleViewDetails}
      />
      {/* Execution Detail Dialog */}
      <ExecutionDetailDialog
        open={isDetailOpen}
        onClose={() => {
          setIsDetailOpen(false);
          setSelectedExecutionId(null);
        }}
        execution={detailQuery.data ?? null}
        isLoading={detailQuery.isLoading && selectedExecutionId != null}
      />
    </Box>
  );
};

export default WorkflowExecutionHistoryPage;
