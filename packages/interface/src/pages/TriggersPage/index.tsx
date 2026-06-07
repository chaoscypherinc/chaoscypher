// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TriggersPage: Dashboard for viewing and toggling triggers.
 *
 * Displays all triggers with links to edit them in the workflow builder.
 * Users can enable/disable triggers directly from this page.
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router';
import { Box, Alert, Typography } from '@mui/material';
import {
  ghostErrorAlertSx,
  ghostInfoAlertSx,
} from '../../theme/ghostStyles';
import SearchFilterBar from '../../components/SearchFilterBar';
import { LoadingState } from '../../components/LoadingState';
import { TriggerWorkflowGroup } from './TriggerWorkflowGroup';
import { TriggerStatsDialog } from './TriggerStatsDialog';
import {
  useTriggers,
  useTriggerStats,
  useUpdateTrigger,
} from '../../services/api/useTriggers';
import { useWorkflows } from '../../services/api/useWorkflows';
import type { Trigger } from '../../services/api/triggers';

const EVENT_SOURCES = [
  'node.created', 'node.updated', 'node.deleted',
  'edge.created', 'edge.updated', 'edge.deleted',
  'import.completed', 'import.failed',
  'workflow.completed', 'workflow.failed',
];

const TriggersPage: React.FC = () => {
  const navigate = useNavigate();
  const triggersQuery = useTriggers();
  const workflowsQuery = useWorkflows();
  const updateTrigger = useUpdateTrigger();

  const [searchQuery, setSearchQuery] = useState('');
  const [selectedEventSource, setSelectedEventSource] = useState<string>('all');
  const [statsDialogOpen, setStatsDialogOpen] = useState(false);
  const [selectedTrigger, setSelectedTrigger] = useState<Trigger | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const triggers = triggersQuery.data ?? [];
  const workflows = workflowsQuery.data ?? [];
  const triggerStatsQuery = useTriggerStats(selectedTrigger?.id ?? null);

  const queryError = triggersQuery.error;
  const error =
    localError ??
    (queryError instanceof Error ? queryError.message : null);

  const getWorkflowName = (workflowId: string): string => {
    const workflow = workflows.find((w) => w.id === workflowId);
    return workflow?.name || workflowId;
  };

  const openStatsDialog = (trigger: Trigger) => {
    setSelectedTrigger(trigger);
    setStatsDialogOpen(true);
  };

  const handleEditInWorkflow = (workflowId: string) => {
    navigate(`/automations/builder/${workflowId}`);
  };

  const handleToggleEnabled = (trigger: Trigger) => {
    updateTrigger.mutate(
      { id: trigger.id, patch: { enabled: !trigger.enabled } },
      {
        onError: (err) => {
          setLocalError(
            err instanceof Error ? err.message : 'Failed to update trigger',
          );
        },
      },
    );
  };

  const filteredTriggers = triggers.filter((trigger) => {
    const matchesSearch = trigger.name.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesEventSource =
      selectedEventSource === 'all' || trigger.event_source === selectedEventSource;
    return matchesSearch && matchesEventSource;
  });

  const triggersByWorkflow = filteredTriggers.reduce<Record<string, Trigger[]>>((acc, trigger) => {
    const workflowId = trigger.workflow_id;
    if (!acc[workflowId]) acc[workflowId] = [];
    acc[workflowId].push(trigger);
    return acc;
  }, {});

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
          <Typography variant="h4">Event Triggers</Typography>
          <Typography
            variant="body2"
            sx={{
              color: "text.secondary",
              mt: 0.5
            }}>
            Toggle triggers on/off here. Click &ldquo;Edit in Workflow&rdquo; for full configuration.
          </Typography>
        </Box>
      </Box>
      {error && (
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }} onClose={() => setLocalError(null)}>
          {error}
        </Alert>
      )}
      {/* Search and Filter */}
      <SearchFilterBar
        searchLabel="Search triggers"
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        filters={[{
          label: 'Event Source',
          value: selectedEventSource,
          options: [
            { value: 'all', label: 'All Events' },
            ...EVENT_SOURCES.map(source => ({ value: source, label: source })),
          ],
          onChange: setSelectedEventSource,
          minWidth: 200,
        }]}
      />
      {/* Loading */}
      {triggersQuery.isPending && (
        <LoadingState message="Loading triggers..." minHeight="200px" />
      )}
      {/* Triggers List */}
      {!triggersQuery.isPending && (
        <>
          {Object.entries(triggersByWorkflow).map(([workflowId, workflowTriggers]) => (
            <TriggerWorkflowGroup
              key={workflowId}
              workflowId={workflowId}
              workflowName={getWorkflowName(workflowId)}
              triggers={workflowTriggers}
              onToggleEnabled={handleToggleEnabled}
              onOpenStats={openStatsDialog}
              onEditInWorkflow={handleEditInWorkflow}
            />
          ))}

          {filteredTriggers.length === 0 && (
            <Alert severity="info" sx={ghostInfoAlertSx}>
              No triggers found. Triggers are created in the workflow builder — drag an event source onto the canvas to add a trigger.
            </Alert>
          )}
        </>
      )}
      {/* Statistics Dialog */}
      <TriggerStatsDialog
        open={statsDialogOpen}
        triggerName={selectedTrigger?.name}
        stats={triggerStatsQuery.data ?? null}
        onClose={() => setStatsDialogOpen(false)}
        onEditInWorkflow={() => {
          setStatsDialogOpen(false);
          if (selectedTrigger) handleEditInWorkflow(selectedTrigger.workflow_id);
        }}
      />
    </Box>
  );
};

export default TriggersPage;
