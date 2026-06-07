// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowBuilderContent: Thin orchestrator for the visual workflow editor.
 *
 * Composes the `useWorkflowBuilder` hook with presentational sub-components
 * (toolbar, canvas, palette drawer, properties panel, modals) without
 * containing any business logic of its own.
 */

import React from 'react';
import { Box, CircularProgress, Drawer, Snackbar } from '@mui/material';
import '@xyflow/react/dist/style.css';
import './WorkflowBuilderPage.css';
import ConfirmDialog from '../../components/ConfirmDialog';

// Sub-components
import { WorkflowToolbar } from './WorkflowToolbar';
import { WorkflowPropertiesPanel } from './WorkflowPropertiesPanel';
import { WorkflowCanvas } from './components/WorkflowCanvas';
import { ToolPalette } from './components/panels/ToolPalette';
import { StepTemplatePanel } from './components/panels/StepTemplatePanel';
import { TestExecutionModal } from './components/modals/TestExecutionModal';
import { WorkflowSettingsModal } from './components/modals/WorkflowSettingsModal';

// State hook
import { useWorkflowBuilder } from './hooks/useWorkflowBuilder';

/** Width of the tool-palette drawer in pixels. */
const PALETTE_WIDTH = 280;

/**
 * Main content component for the workflow builder page.
 *
 * Delegates all state management to `useWorkflowBuilder` and all
 * rendering to focused sub-components. This file acts purely as the
 * composition root that wires props between the hook and the UI.
 */
export const WorkflowBuilderContent: React.FC = () => {
  const builder = useWorkflowBuilder();

  // Loading state
  if (builder.isLoading) {
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: 'calc(100vh - 64px)',
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box className="workflow-builder-container">
      {/* Toolbar */}
      <WorkflowToolbar
        workflow={builder.workflow}
        isDirty={builder.isDirty}
        isSaving={builder.isSaving}
        canUndo={builder.canUndo}
        canRedo={builder.canRedo}
        onBack={builder.handleBack}
        onUndo={builder.handleUndo}
        onRedo={builder.handleRedo}
        onAutoLayout={builder.handleAutoLayout}
        onOpenSettings={() => builder.setIsSettingsModalOpen(true)}
        onOpenTemplates={() => builder.setIsTemplatesPanelOpen(true)}
        onTestExecution={builder.handleTestExecution}
        onSave={builder.handleSave}
      />

      {/* Main content area */}
      <Box sx={{ display: 'flex', flex: 1, overflow: 'hidden', minHeight: 0 }}>
        {/* Tool Palette (Left Sidebar) */}
        <Drawer
          variant="persistent"
          anchor="left"
          open={builder.isPaletteOpen}
          sx={{
            width: builder.isPaletteOpen ? PALETTE_WIDTH : 0,
            flexShrink: 0,
            '& .MuiDrawer-paper': {
              width: PALETTE_WIDTH,
              position: 'relative',
              borderRight: 1,
              borderColor: 'divider',
              height: '100%',
              overflow: 'hidden',
            },
          }}
        >
          <ToolPalette onClose={() => builder.setIsPaletteOpen(false)} />
        </Drawer>

        {/* Canvas */}
        <WorkflowCanvas
          nodes={builder.nodes}
          edges={builder.edges}
          onNodesChange={builder.handleNodesChange}
          onEdgesChange={builder.handleEdgesChange}
          onConnect={builder.onConnect}
          onNodeClick={builder.handleNodeClick}
          onEdgeClick={builder.handleEdgeClick}
          onPaneClick={builder.handlePaneClick}
          isDragOver={builder.isDragOver}
          onDrop={builder.onDrop}
          onDragOver={builder.onDragOver}
          onDragLeave={builder.onDragLeave}
          isPaletteOpen={builder.isPaletteOpen}
          onOpenPalette={() => builder.setIsPaletteOpen(true)}
          error={builder.error}
          onClearError={() => builder.setError(null)}
        />

        {/* Properties Panel (Right Sidebar) */}
        <WorkflowPropertiesPanel
          isOpen={builder.isPropertiesPanelOpen}
          selectedNode={builder.selectedNode}
          selectedEdge={builder.selectedEdge}
          onNodeUpdate={builder.handleNodeUpdate}
          onDeleteNode={builder.deleteSelectedNode}
          onDeleteEdge={builder.deleteSelectedEdge}
          onClose={builder.handlePaneClick}
          onSaveAsTemplate={builder.handleSaveAsTemplate}
          toolSchema={builder.selectedToolSchema}
          toolOutputSchema={builder.selectedToolOutputSchema}
          upstreamFields={builder.upstreamFields}
        />
      </Box>

      {/* Success snackbar */}
      <Snackbar
        open={!!builder.successMessage}
        autoHideDuration={3000}
        onClose={() => builder.setSuccessMessage(null)}
        message={builder.successMessage}
      />

      {/* Test Execution Modal */}
      {builder.workflow && (
        <TestExecutionModal
          open={builder.isTestModalOpen}
          onClose={() => builder.setIsTestModalOpen(false)}
          workflowId={builder.workflow.id}
          workflowName={builder.workflow.name}
          inputSchema={builder.workflow.input_schema || {}}
        />
      )}

      {/* Workflow Settings Modal */}
      <WorkflowSettingsModal
        open={builder.isSettingsModalOpen}
        onClose={() => builder.setIsSettingsModalOpen(false)}
        workflow={builder.workflow}
        onSave={builder.handleSettingsSave}
        isNewWorkflow={!builder.workflow}
      />

      {/* Step Templates Panel */}
      <StepTemplatePanel
        open={builder.isTemplatesPanelOpen}
        onClose={() => builder.setIsTemplatesPanelOpen(false)}
        onApplyTemplate={builder.handleApplyTemplate}
      />

      {/* Unsaved Changes Confirmation */}
      <ConfirmDialog
        open={builder.confirmLeaveOpen}
        title="Unsaved Changes"
        message="You have unsaved changes. Are you sure you want to leave?"
        confirmLabel="Leave"
        confirmColor="warning"
        onConfirm={builder.handleConfirmLeave}
        onCancel={() => builder.setConfirmLeaveOpen(false)}
      />
    </Box>
  );
};
