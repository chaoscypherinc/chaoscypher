// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Workflows Page
 *
 * Card grid displaying all workflows with filtering, execution,
 * and management capabilities.
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router';
import {
  Box,
  Typography,
  Button,
  Alert,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import {
  ghostButtonSx,
  ghostErrorAlertSx,
  ghostSuccessAlertSx,
  ghostInfoAlertSx,
} from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';
import { LoadingState } from '../components/LoadingState';
import { useConfirmDialog } from '../hooks/useConfirmDialog';
import ConfirmDialog from '../components/ConfirmDialog';
import SearchFilterBar from '../components/SearchFilterBar';
import { getApiErrorMessage } from '../utils/errors';
import WorkflowExecuteDialog from './WorkflowExecuteDialog';
import WorkflowStepsDialog from './WorkflowStepsDialog';
import WorkflowCard from './WorkflowCard';
import {
  useWorkflows,
  useWorkflowSteps,
  useDeleteWorkflow,
  useDuplicateWorkflow,
  useExecuteWorkflow,
  useUpdateWorkflow,
} from '../services/api/useWorkflows';
import type { Workflow } from '../services/api/workflows';

const CYAN = ChaosCypherPalette.primary;

interface JsonSchema {
  type?: string;
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
  required?: string[];
  default?: unknown;
  description?: string;
  [key: string]: unknown;
}

const WorkflowsPage: React.FC = () => {
  const navigate = useNavigate();

  // Filters
  const [filterCategory, setFilterCategory] = useState('all');
  const [filterSystem, setFilterSystem] = useState<boolean | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  const listParams = {
    ...(filterCategory !== 'all' ? { category: filterCategory } : {}),
    ...(filterSystem !== null ? { is_system: filterSystem } : {}),
  };
  const workflowsQuery = useWorkflows(listParams);
  const workflows = workflowsQuery.data ?? [];

  // Selected workflow drives both dialogs and the steps query.
  const [selectedWorkflow, setSelectedWorkflow] = useState<Workflow | null>(null);
  const stepsQuery = useWorkflowSteps(selectedWorkflow?.id ?? null);

  // Dialog state
  const [executeDialogOpen, setExecuteDialogOpen] = useState(false);
  const [stepsDialogOpen, setStepsDialogOpen] = useState(false);
  const deleteDialog = useConfirmDialog<string>();

  // Form / result state
  const [executionInputs, setExecutionInputs] = useState('{}');
  const [executionResult, setExecutionResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const executeMutation = useExecuteWorkflow();
  const duplicateMutation = useDuplicateWorkflow();
  const deleteMutation = useDeleteWorkflow();
  const updateMutation = useUpdateWorkflow();

  const handleExecuteWorkflow = () => {
    if (!selectedWorkflow) return;
    let inputs: Record<string, unknown>;
    try {
      inputs = JSON.parse(executionInputs);
    } catch (err) {
      setError(getApiErrorMessage(err));
      return;
    }
    executeMutation.mutate(
      { workflowId: selectedWorkflow.id, inputs },
      {
        onSuccess: (data) => {
          // The execute endpoint returns { execution_id, status, message };
          // surface a success/error banner based on the status string.
          setExecutionResult({
            success: data.status !== 'failed',
            error: data.status === 'failed' ? data.message : undefined,
          });
          setExecuteDialogOpen(false);
        },
        onError: (err) => setError(getApiErrorMessage(err)),
      },
    );
  };

  const handleDuplicateWorkflow = (workflowId: string) => {
    duplicateMutation.mutate(workflowId, {
      onError: (err) => setError(getApiErrorMessage(err)),
    });
  };

  const handleDeleteWorkflow = (workflowId: string) => {
    deleteDialog.open(workflowId);
  };

  const handleConfirmDeleteWorkflow = () => {
    void deleteDialog.confirm(async () => {
      await new Promise<void>((resolve) => {
        deleteMutation.mutate(deleteDialog.data!, {
          onSuccess: () => {
            if (selectedWorkflow?.id === deleteDialog.data) {
              setSelectedWorkflow(null);
            }
            resolve();
          },
          onError: (err) => {
            setError(getApiErrorMessage(err));
            resolve();
          },
        });
      });
    });
  };

  const handleToggleActive = (workflow: Workflow) => {
    updateMutation.mutate(
      { id: workflow.id, patch: { is_active: !workflow.is_active } },
      {
        onError: (err) => setError(getApiErrorMessage(err)),
      },
    );
  };

  const openExecuteDialog = (workflow: Workflow) => {
    setSelectedWorkflow(workflow);
    const exampleInputs = generateExampleInputs(workflow.input_schema as JsonSchema);
    setExecutionInputs(JSON.stringify(exampleInputs, null, 2));
    setExecuteDialogOpen(true);
  };

  const openStepsDialog = (workflow: Workflow) => {
    setSelectedWorkflow(workflow);
    setStepsDialogOpen(true);
  };

  const generateExampleInputs = (schema: JsonSchema | undefined): Record<string, unknown> => {
    if (!schema || !schema.properties) return {};

    const example: Record<string, unknown> = {};
    for (const [key, prop] of Object.entries(schema.properties)) {
      if (prop.type === 'string') {
        example[key] = prop.default || prop.description || '';
      } else if (prop.type === 'number' || prop.type === 'integer') {
        example[key] = prop.default || 0;
      } else if (prop.type === 'boolean') {
        example[key] = prop.default || false;
      } else if (prop.type === 'object') {
        example[key] = {};
      } else if (prop.type === 'array') {
        example[key] = [];
      }
    }
    return example;
  };

  const filteredWorkflows = workflows.filter(w => {
    const matchesSearch = w.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (w.description && w.description.toLowerCase().includes(searchQuery.toLowerCase()));
    return matchesSearch;
  });

  const categories = ['all', ...new Set(workflows.map(w => w.category).filter((c): c is string => Boolean(c)))];

  const queryError = workflowsQuery.error;
  const surfacedError =
    error ?? (queryError instanceof Error ? queryError.message : null);

  return (
    <Box sx={{ maxWidth: 'xl', mx: 'auto', mt: { xs: 2, md: 4 }, mb: { xs: 2, md: 4 }, px: { xs: 1, md: 3 } }}>
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
        <Box>
          <Typography variant="h4">Workflows</Typography>
          <Typography
            variant="body2"
            sx={{
              color: "text.secondary",
              mt: 0.5
            }}>
            Build and manage automated processing pipelines with drag-and-drop workflow builder.
          </Typography>
        </Box>
        <Button
          variant="outlined"
          startIcon={<AddIcon />}
          onClick={() => navigate('/automations/builder')}
          sx={ghostButtonSx(CYAN)}
        >
          New Workflow
        </Button>
      </Box>
      {surfacedError && (
        <Alert
          severity="error"
          sx={{ mb: 2, ...ghostErrorAlertSx }}
          onClose={() => setError(null)}
        >
          {surfacedError}
        </Alert>
      )}
      {executionResult && (
        <Alert
          severity={executionResult.success ? 'success' : 'error'}
          sx={{
            mb: 2,
            ...(executionResult.success ? ghostSuccessAlertSx : ghostErrorAlertSx),
          }}
          onClose={() => setExecutionResult(null)}
        >
          {executionResult.success ? 'Workflow executed successfully!' : `Execution failed: ${executionResult.error}`}
        </Alert>
      )}
      {/* Filters */}
      <SearchFilterBar
        searchLabel="Search workflows"
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        filters={[
          {
            label: 'Category',
            value: filterCategory,
            options: categories.map(cat => ({ value: cat, label: cat === 'all' ? 'All' : cat })),
            onChange: setFilterCategory,
          },
          {
            label: 'Type',
            value: filterSystem === null ? 'all' : filterSystem ? 'system' : 'user',
            options: [
              { value: 'all', label: 'All' },
              { value: 'system', label: 'System' },
              { value: 'user', label: 'User' },
            ],
            onChange: (val) => setFilterSystem(val === 'all' ? null : val === 'system'),
          },
        ]}
      />
      {/* Workflows Grid */}
      {workflowsQuery.isPending ? (
        <LoadingState message="Loading workflows..." />
      ) : (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
          {filteredWorkflows.map(workflow => (
            <WorkflowCard
              key={workflow.id}
              workflow={workflow}
              onExecute={() => openExecuteDialog(workflow)}
              onViewSteps={() => openStepsDialog(workflow)}
              onEdit={() => navigate(`/automations/builder/${workflow.id}`)}
              onHistory={() => navigate(`/automations/${workflow.id}/history`)}
              onDuplicate={() => handleDuplicateWorkflow(workflow.id)}
              onDelete={() => handleDeleteWorkflow(workflow.id)}
              onToggleActive={() => handleToggleActive(workflow)}
            />
          ))}

          {filteredWorkflows.length === 0 && (
            <Box sx={{ width: '100%' }}>
              <Alert
                severity="info"
                sx={ghostInfoAlertSx}
              >
                No workflows found. Click &quot;New Workflow&quot; to create one.
              </Alert>
            </Box>
          )}
        </Box>
      )}
      {/* Execute Dialog */}
      <WorkflowExecuteDialog
        open={executeDialogOpen}
        onClose={() => setExecuteDialogOpen(false)}
        workflowName={selectedWorkflow?.name}
        workflowDescription={selectedWorkflow?.description}
        executionInputs={executionInputs}
        onExecutionInputsChange={setExecutionInputs}
        onExecute={handleExecuteWorkflow}
        executing={executeMutation.isPending}
      />
      {/* Steps Dialog */}
      <WorkflowStepsDialog
        open={stepsDialogOpen}
        onClose={() => setStepsDialogOpen(false)}
        workflowName={selectedWorkflow?.name}
        steps={stepsQuery.data ?? []}
      />
      {/* History dialog removed - now navigates to /automations/{id}/history */}
      <ConfirmDialog
        open={deleteDialog.isOpen}
        title="Confirm Delete"
        message="Are you sure you want to delete this workflow?"
        onConfirm={handleConfirmDeleteWorkflow}
        onCancel={deleteDialog.close}
      />
    </Box>
  );
};

export default WorkflowsPage;
