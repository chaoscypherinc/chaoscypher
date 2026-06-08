// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Application Layout Shell
 *
 * Thin wrapper that composes the sidebar, app bar, main content area,
 * global upload dialog, and omnibar. All upload state is delegated to
 * {@link useUploadDialogState} and sidebar rendering to {@link Sidebar}.
 */

import { useState, useCallback, ReactNode } from 'react';
import {
  Alert,
  AppBar,
  Box,
  CssBaseline,
  Drawer,
  IconButton,
  Snackbar,
  Toolbar,
} from '@mui/material';
import MenuIcon from '@mui/icons-material/Menu';
import { useLocation } from 'react-router';
import { OmnibarProvider, OmnibarDialog, OmnibarTrigger } from './Omnibar';
import MiniSystemStatus from './MiniSystemStatus';
import { SystemPauseBanner } from './SystemPauseBanner';
import LLMNotConfiguredBanner from './LLMNotConfiguredBanner';
import { PostUpgradeNotice } from './PostUpgradeNotice';
import { useSystemPauseStatus } from '../hooks/useSystemPauseStatus';

import Sidebar from './Sidebar';
import SidebarUser from './SidebarUser';
import SidebarDatabase from './SidebarDatabase';
import { UploadDialog } from './UploadDialog';
import { UploadWizard } from './UploadWizard';
import { UploadDialogContext } from '../contexts/UploadDialogContext';
import { useUploadDialogState } from '../hooks/useUploadDialogState';
import { useDatabaseSelector } from '../hooks/useDatabaseSelector';
import { useEventToasts } from '../hooks/useEventToasts';
import type { SystemEvent } from '../services/api/events';
import type { AlertColor } from '@mui/material';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DRAWER_WIDTH = 240;
const DRAWER_WIDTH_COLLAPSED = 64;

/** Map event type to MUI Alert severity. */
function eventSeverity(type: string): AlertColor {
  switch (type) {
    case 'task_completed':
      return 'success';
    case 'task_failed':
      return 'error';
    case 'pause':
    case 'health_change':
      return 'warning';
    case 'resume':
    case 'recovery':
      return 'info';
    default:
      return 'info';
  }
}

interface ActiveToast {
  key: number;
  message: string;
  severity: AlertColor;
}

interface LayoutProps {
  children: ReactNode;
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

export default function Layout({ children }: LayoutProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [drawerCollapsed, setDrawerCollapsed] = useState(false);
  const location = useLocation();

  // --- System-wide pause state ---
  const { status: systemPauseStatus, resumeSystem, pauseSystem } = useSystemPauseStatus();

  // --- Global Upload Dialog state (hook) ---
  const upload = useUploadDialogState();

  // --- Database selector state + actions ---
  const { databases, currentDatabase, switchDatabase, createDatabase } = useDatabaseSelector();

  // --- Event toast notifications ---
  const [toast, setToast] = useState<ActiveToast | null>(null);

  const handleNewEvent = useCallback((event: SystemEvent) => {
    setToast({
      key: event.id,
      message: event.action,
      severity: eventSeverity(event.type),
    });
  }, []);

  const handleToastClose = useCallback(() => setToast(null), []);

  useEventToasts({ onNewEvent: handleNewEvent });

  // --- Drawer helpers ---
  const handleDrawerToggle = useCallback(() => setMobileOpen((prev) => !prev), []);
  const handleMobileClose = useCallback(() => setMobileOpen(false), []);
  const toggleDrawerCollapse = useCallback(() => setDrawerCollapsed((prev) => !prev), []);

  const isDrawerOpen = !drawerCollapsed;
  const currentDrawerWidth = isDrawerOpen ? DRAWER_WIDTH : DRAWER_WIDTH_COLLAPSED;

  // Chat page manages its own layout
  const isChatPage = location.pathname.startsWith('/chat');

  const sidebarContent = (
    <Sidebar
      isDrawerOpen={isDrawerOpen}
      onToggleCollapse={toggleDrawerCollapse}
      onMobileClose={handleMobileClose}
    />
  );

  return (
    <UploadDialogContext.Provider value={upload.uploadDialogCtx}>
    <OmnibarProvider>
    <Box sx={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <CssBaseline />

      {/* App Bar */}
      <AppBar
        position="fixed"
        sx={{
          width: { sm: `calc(100% - ${currentDrawerWidth}px)` },
          ml: { sm: `${currentDrawerWidth}px` },
          transition: (theme) =>
            theme.transitions.create(['margin', 'width'], {
              easing: theme.transitions.easing.sharp,
              duration: theme.transitions.duration.leavingScreen,
            }),
        }}
      >
        <Toolbar variant="dense" sx={{ minHeight: 64, height: 64 }}>
          <IconButton
            color="inherit"
            aria-label="open drawer"
            edge="start"
            onClick={handleDrawerToggle}
            sx={{ mr: 2, display: { sm: 'none' } }}
          >
            <MenuIcon />
          </IconButton>

          {/* Database Selector */}
          <SidebarDatabase
            databases={databases}
            currentDatabase={currentDatabase}
            onSwitch={switchDatabase}
            onCreate={createDatabase}
          />

          {/* Omnibar Trigger */}
          <Box
            sx={{
              flexGrow: 1,
              display: 'flex',
              justifyContent: 'center',
              pl: { xs: 1, sm: 3 },
              pr: { xs: 1, sm: 2 },
              minWidth: 0,
            }}
          >
            <OmnibarTrigger />
          </Box>

          {/* System Status + User Avatar */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <MiniSystemStatus
              onAddSource={upload.openUploadDialog}
              systemPauseStatus={systemPauseStatus}
              onPauseSystem={pauseSystem}
              onResumeSystem={resumeSystem}
            />
            <SidebarUser isDrawerOpen={false} variant="header" />
          </Box>
        </Toolbar>
      </AppBar>

      {/* Sidebar Drawers */}
      <Box
        component="nav"
        sx={{
          width: { sm: currentDrawerWidth },
          flexShrink: { sm: 0 },
          transition: (theme) =>
            theme.transitions.create('width', {
              easing: theme.transitions.easing.sharp,
              duration: theme.transitions.duration.leavingScreen,
            }),
        }}
      >
        <Drawer
          variant="temporary"
          open={mobileOpen}
          onClose={handleDrawerToggle}
          ModalProps={{ keepMounted: true }}
          sx={{
            display: { xs: 'block', sm: 'none' },
            '& .MuiDrawer-paper': { boxSizing: 'border-box', width: DRAWER_WIDTH },
          }}
        >
          {sidebarContent}
        </Drawer>
        <Drawer
          variant="permanent"
          sx={{
            display: { xs: 'none', sm: 'block' },
            '& .MuiDrawer-paper': {
              boxSizing: 'border-box',
              width: currentDrawerWidth,
              transition: (theme) =>
                theme.transitions.create('width', {
                  easing: theme.transitions.easing.sharp,
                  duration: theme.transitions.duration.leavingScreen,
                }),
              overflowX: 'hidden',
            },
          }}
          open
        >
          {sidebarContent}
        </Drawer>
      </Box>

      {/* Main Content */}
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          width: { sm: `calc(100% - ${currentDrawerWidth}px)` },
          height: '100vh',
          pt: isChatPage ? 7 : 9,
          pl: isChatPage ? 0 : 2,
          pr: isChatPage ? 0 : 3,
          pb: isChatPage ? 0 : 3,
          display: 'flex',
          flexDirection: 'column',
          overflow: isChatPage ? 'hidden' : 'auto',
          transition: (theme) =>
            theme.transitions.create(['margin', 'width'], {
              easing: theme.transitions.easing.sharp,
              duration: theme.transitions.duration.leavingScreen,
            }),
        }}
      >
          <SystemPauseBanner status={systemPauseStatus} onResume={resumeSystem} />
          <LLMNotConfiguredBanner />
          <PostUpgradeNotice />
          {children}
      </Box>

      {/* Global Upload Dialog */}
      <UploadDialog
        open={upload.uploadDialogOpen}
        onClose={upload.closeUploadDialog}
        selectedFiles={upload.uploadHook.selectedFiles}
        analysisDepth={upload.uploadHook.analysisDepth}
        enableNormalization={upload.uploadHook.enableNormalization}
        selectedDomain={upload.uploadHook.selectedDomain}
        availableDomains={upload.domains}
        onFilesSelected={upload.uploadHook.handleFilesSelected}
        onAnalysisDepthChange={upload.uploadHook.setAnalysisDepth}
        onNormalizationChange={upload.uploadHook.setEnableNormalization}
        onDomainChange={upload.uploadHook.setSelectedDomain}
        onConfirm={upload.uploadHook.handleUploadConfirm}
        uploading={upload.uploadHook.uploading}
        onCancelUpload={upload.uploadHook.cancelUpload}
        onClearSelection={upload.uploadHook.clearSelection}
        onRemoveFile={upload.uploadHook.removeFile}
        onUrlImport={upload.uploadHook.handleUrlImport}
        importingUrl={upload.uploadHook.importingUrl}
        extractEntities={upload.uploadHook.extractEntities}
        onExtractEntitiesChange={upload.uploadHook.setExtractEntities}
        enableVision={upload.uploadHook.enableVision}
        onEnableVisionChange={upload.uploadHook.setEnableVision}
        filteringMode={upload.uploadHook.filteringMode}
        onFilteringModeChange={upload.uploadHook.setFilteringMode}
        contentFiltering={upload.uploadHook.contentFiltering}
        onContentFilteringChange={upload.uploadHook.setContentFiltering}
        contextWindow={upload.extractionCapacity.contextWindow}
        groupSize={upload.extractionCapacity.groupSize}
        inputPerChunk={upload.extractionCapacity.inputPerChunk}
        outputPerChunk={upload.extractionCapacity.outputPerChunk}
        skipDuplicates={upload.uploadHook.skipDuplicates}
        onSkipDuplicatesChange={upload.uploadHook.setSkipDuplicates}
      />

      {/* Upfront domain-confirmation wizard (single-file uploads). This
          app-shell entry point owns its own `useSourcesUpload` instance
          (the Sources page has its own); only the instance whose
          handleUploadConfirm ran goes non-idle, the other stays idle and
          renders nothing. */}
      <UploadWizard
        wizard={upload.uploadHook.wizard}
        availableDomains={upload.domains}
        contextWindow={upload.extractionCapacity.contextWindow}
        groupSize={upload.extractionCapacity.groupSize}
        inputPerChunk={upload.extractionCapacity.inputPerChunk}
        outputPerChunk={upload.extractionCapacity.outputPerChunk}
      />

      {/* Upload error snackbar */}
      <Snackbar
        open={!!upload.uploadError}
        autoHideDuration={6000}
        onClose={upload.clearUploadError}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity="error" onClose={upload.clearUploadError} variant="filled">
          {upload.uploadError}
        </Alert>
      </Snackbar>

      {/* System event toasts */}
      <Snackbar
        key={toast?.key}
        open={!!toast}
        autoHideDuration={4000}
        onClose={handleToastClose}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        {toast ? (
          <Alert
            severity={toast.severity}
            onClose={handleToastClose}
            variant="filled"
            sx={{ minWidth: 260 }}
          >
            {toast.message}
          </Alert>
        ) : undefined}
      </Snackbar>

      {/* Omnibar Dialog */}
      <OmnibarDialog />
    </Box>
    </OmnibarProvider>
    </UploadDialogContext.Provider>
  );
}
