// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowBuilderPage: Visual drag-and-drop workflow editor
 *
 * Wrapper component that provides ReactFlow context for the workflow canvas.
 * Handles routing for both new workflow creation and editing existing workflows.
 */

import React from 'react';
import { Box, Typography } from '@mui/material';
import { ReactFlowProvider } from '@xyflow/react';
import { WorkflowBuilderContent } from './WorkflowBuilderContent';

const WorkflowBuilderPage: React.FC = () => (
  <>
    {/* Desktop-only message for xs/sm viewports.
        The drag-and-drop workflow canvas (ReactFlow + palette + properties
        panel) assumes ≥1024px for usable interaction. Rather than render
        an unusable canvas on phones, surface a brief notice. */}
    <Box
      sx={{
        display: { xs: 'flex', md: 'none' },
        alignItems: 'center',
        justifyContent: 'center',
        height: 'calc(100vh - 64px)',
        px: 3,
        textAlign: 'center',
      }}
    >
      <Box sx={{ maxWidth: 360 }}>
        <Typography variant="h6" sx={{ mb: 1, color: 'text.primary' }}>
          Desktop only
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          The workflow builder requires a desktop screen (≥1024px) for
          drag-and-drop editing. Other pages remain readable on this device.
        </Typography>
      </Box>
    </Box>
    <Box sx={{ display: { xs: 'none', md: 'block' }, height: '100%' }}>
      <ReactFlowProvider>
        <WorkflowBuilderContent />
      </ReactFlowProvider>
    </Box>
  </>
);

export default WorkflowBuilderPage;
