// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Settings Page
 *
 * Tab orchestrator for all application settings. Database management,
 * import/export, and dialog state are delegated to useSettingsActions.
 */

import { useEffect, useState, useMemo } from 'react';
import {
  Box,
  Typography,
  Paper,
  Button,
  Alert,
  Tabs,
  Tab,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Chip,
  IconButton,
  Tooltip,
} from '@mui/material';
import DatabaseIcon from '@mui/icons-material/Storage';
import AddIcon from '@mui/icons-material/Add';
import CheckIcon from '@mui/icons-material/Check';
import DeleteIcon from '@mui/icons-material/Delete';
import ArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import SettingsIcon from '@mui/icons-material/Settings';
import ModelsIcon from '@mui/icons-material/SmartToy';
import SearchTabIcon from '@mui/icons-material/Search';
import MaintenanceIcon from '@mui/icons-material/Build';
import BackupTabIcon from '@mui/icons-material/Backup';
import LogsIcon from '@mui/icons-material/Terminal';
import {
  ghostDialogPaperSx,
  ghostButtonSx,
  ghostCancelBtnSx,
  ghostTabsSx,
  ghostSuccessAlertSx,
} from '../theme/ghostStyles';
import { LoadingState } from '../components/LoadingState';
import GeneralSettingsTab from './settings/GeneralSettingsTab';
import LLMProviderTab from './settings/LLMProviderTab';
import SearchTab from './settings/SearchTab';
import DatabaseResetTab from './settings/DatabaseResetTab';
import BackupTab from './settings/BackupTab';
import LogsTab from './settings/LogsTab';
import CreateDatabaseDialog from './settings/CreateDatabaseDialog';
import { useSettingsActions } from './settings/useSettingsActions';
import { useSearchParams } from 'react-router';
import { ChaosCypherPalette } from '../theme/palette';

/** Map of tab query param values to tab names. */
const TAB_PARAM_MAP: Record<string, string> = {
  general: 'general',
  models: 'models',
  search: 'search',
  maintenance: 'maintenance',
  backup: 'backup',
  logs: 'logs',
  // Legacy params (backwards compat for bookmarks/links)
  llm: 'models',
  reset: 'maintenance',
  // Account + API keys now live in the General tab (deep-linked via ?section=).
  // The Omnibar's "Access Settings" command and old links use ?tab=access.
  access: 'general',
};

/** Deep-link sub-targets within the General tab (user dropdown jumps here). */
type FocusSection = 'account' | 'api-keys' | null;
function resolveSection(params: URLSearchParams): FocusSection {
  const section = params.get('section');
  return section === 'account' || section === 'api-keys' ? section : null;
}

export default function SettingsPage() {
  const actions = useSettingsActions();
  const [searchParams] = useSearchParams();

  // Database menu state
  const [dbMenuAnchor, setDbMenuAnchor] = useState<null | HTMLElement>(null);
  const [createDbDialogOpen, setCreateDbDialogOpen] = useState(false);

  // Build tab index mapping
  const tabMapping = useMemo(() => {
    let idx = 0;
    const mapping: Record<string, number> = {};
    mapping.general = idx++;
    mapping.models = idx++;
    mapping.search = idx++;
    mapping.maintenance = idx++;
    mapping.backup = idx++;
    mapping.logs = idx++;
    return mapping;
  }, []);

  // Resolve tab from URL ?tab= param
  const resolveTab = (params: URLSearchParams): number => {
    const tabParam = params.get('tab');
    if (tabParam) {
      const resolved = TAB_PARAM_MAP[tabParam];
      if (resolved && tabMapping[resolved] !== undefined) {
        return tabMapping[resolved];
      }
    }
    return 0;
  };

  // Tab state
  const [activeTab, setActiveTab] = useState(() => resolveTab(searchParams));

  // Deep-link sub-target within the General tab. Derived from the URL so a
  // fresh navigation from the user dropdown (?section=...) opens + scrolls to
  // the matching accordion.
  const focusSection = resolveSection(searchParams);

  // Sync tab when URL search params change (e.g. navigating from omnibar)
  useEffect(() => {
    const tab = resolveTab(searchParams);
    setActiveTab(tab);
  }, [searchParams, tabMapping]);

  // `settings.export` is a required field, but check it explicitly so a
  // malformed response (server error, empty body, mock in tests) falls into
  // the loading state instead of crashing GeneralSettingsTab downstream.
  if (actions.loading || !actions.settings || !actions.settings.export) {
    return <LoadingState message="Loading settings..." fullPage />;
  }

  const handleSwitchDatabase = async (dbName: string) => {
    setDbMenuAnchor(null);
    await actions.handleSwitchDatabase(dbName);
  };

  const handleOpenCreateDialog = () => {
    setDbMenuAnchor(null);
    actions.setNewDatabaseName('');
    setCreateDbDialogOpen(true);
  };

  return (
    <Box>
      {/* Header with Database Selector */}
      <Box
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 2,
          justifyContent: 'space-between',
          alignItems: { xs: 'flex-start', sm: 'center' },
          mb: 2,
        }}
      >
        <Typography variant="h4">
          Settings
        </Typography>

        {/* Database Selector */}
        <Tooltip title="Switch between databases or create a new one. Each database has its own knowledge graph, sources, and settings.">
          <Button
            variant="outlined"
            startIcon={<DatabaseIcon sx={{ fontSize: 24 }} />}
            endIcon={<ArrowDownIcon />}
            onClick={(e) => setDbMenuAnchor(e.currentTarget)}
            sx={{
              textTransform: 'none',
              minWidth: 200,
              px: 2.5,
              py: 1.5,
              gap: 1.5,
            }}
          >
            <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', flex: 1 }}>
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  lineHeight: 1.2,
                  mb: 0.25
                }}>
                Database
              </Typography>
              <Typography
                variant="body1"
                sx={{
                  fontWeight: 500,
                  lineHeight: 1.2
                }}>
                {actions.currentDatabase}
              </Typography>
            </Box>
          </Button>
        </Tooltip>

        <Menu
          anchorEl={dbMenuAnchor}
          open={Boolean(dbMenuAnchor)}
          onClose={() => setDbMenuAnchor(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
          transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        >
          {actions.databases.map((db) => (
            <MenuItem
              key={db.name}
              onClick={() => handleSwitchDatabase(db.name)}
              selected={db.name === actions.currentDatabase}
              sx={{ pr: 1 }}
            >
              <ListItemIcon>
                {db.name === actions.currentDatabase ? <CheckIcon fontSize="small" color="primary" /> : <DatabaseIcon fontSize="small" />}
              </ListItemIcon>
              <ListItemText
                primary={db.name}
                secondary={actions.formatBytes(db.size)}
              />
              {db.name === 'default' && (
                <Chip label="default" size="small" variant="outlined" sx={{ ml: 1 }} />
              )}
              {db.name !== actions.currentDatabase && db.name !== 'default' && (
                <IconButton
                  aria-label="Delete database"
                  size="small"
                  color="error"
                  onClick={(e) => {
                    e.stopPropagation();
                    setDbMenuAnchor(null);
                    actions.handleDeleteDatabase(db.name);
                  }}
                  sx={{ ml: 1 }}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              )}
            </MenuItem>
          ))}
          <Divider />
          <MenuItem onClick={handleOpenCreateDialog}>
            <ListItemIcon>
              <AddIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText primary="Create new database" />
          </MenuItem>
        </Menu>

        <CreateDatabaseDialog
          open={createDbDialogOpen}
          onClose={() => setCreateDbDialogOpen(false)}
          databaseName={actions.newDatabaseName}
          onDatabaseNameChange={actions.setNewDatabaseName}
          onCreateDatabase={actions.handleCreateDatabase}
          creating={actions.creatingDatabase}
        />
      </Box>
      {actions.success && (
        <Alert severity="success" sx={{ mb: 2, ...ghostSuccessAlertSx }}>
          Settings saved successfully!
        </Alert>
      )}
      <Paper sx={{ mt: 2 }}>
        <Tabs
          value={activeTab}
          onChange={(_, newValue) => setActiveTab(newValue)}
          variant="scrollable"
          scrollButtons="auto"
          sx={{ borderBottom: 1, borderColor: 'divider', ...ghostTabsSx }}
        >
          <Tab icon={<SettingsIcon />} iconPosition="start" label="General" />
          <Tab icon={<ModelsIcon />} iconPosition="start" label="Models" />
          <Tab icon={<SearchTabIcon />} iconPosition="start" label="Search" />
          <Tab icon={<MaintenanceIcon />} iconPosition="start" label="Maintenance" />
          <Tab icon={<BackupTabIcon />} iconPosition="start" label="Backup" />
          <Tab icon={<LogsIcon />} iconPosition="start" label="Logs" />
        </Tabs>

        {(() => {
          let idx = 0;
          const tabGeneral = idx++;
          const tabModels = idx++;
          const tabSearch = idx++;
          const tabMaintenance = idx++;
          const tabBackup = idx++;
          const tabLogs = idx++;

          return (
            <>
              {activeTab === tabGeneral && (
                <GeneralSettingsTab
                  settings={actions.settings}
                  setSettings={actions.setSettings}
                  focusSection={focusSection}
                  importing={actions.importing}
                  exporting={actions.exporting}
                  importSuccess={actions.importSuccess}
                  importError={actions.importError}
                  setImportError={actions.setImportError}
                  fileInputRef={actions.fileInputRef}
                  handleExport={actions.handleExport}
                  handleImport={actions.handleImport}
                  exportOptions={actions.exportOptions}
                  setExportOptions={actions.setExportOptions}
                />
              )}

              {activeTab === tabModels && (
                <LLMProviderTab settings={actions.settings} setSettings={actions.setSettings} />
              )}

              {activeTab === tabSearch && (
                <SearchTab settings={actions.settings} setSettings={actions.setSettings} />
              )}

              {activeTab === tabMaintenance && <DatabaseResetTab />}

              {activeTab === tabBackup && (
                <BackupTab settings={actions.settings} setSettings={actions.setSettings} />
              )}

              {activeTab === tabLogs && (
                <LogsTab settings={actions.settings} setSettings={actions.setSettings} />
              )}
            </>
          );
        })()}

        {/* Single Save/Reset Button at Bottom */}
        <Box
          sx={{
            p: { xs: 2, md: 3 },
            borderTop: 1,
            borderColor: 'divider',
            display: 'flex',
            flexWrap: 'wrap',
            gap: 2,
          }}
        >
          <Button
            variant="outlined"
            onClick={actions.handleSave}
            disabled={actions.saving}
            size="large"
            sx={ghostButtonSx(ChaosCypherPalette.primary)}
          >
            {actions.saving ? 'Saving...' : 'Save Settings'}
          </Button>
          <Button
            variant="outlined"
            onClick={actions.handleReset}
            disabled={actions.saving}
            size="large"
            sx={ghostButtonSx(ChaosCypherPalette.warning)}
          >
            Reset to Defaults
          </Button>
        </Box>
      </Paper>
      {/* Alert Dialog */}
      <Dialog open={actions.alertDialog.open} onClose={actions.closeAlert} slotProps={{
        paper: { sx: ghostDialogPaperSx }
      }}>
        <DialogTitle>{actions.alertDialog.title}</DialogTitle>
        <DialogContent>
          <Typography sx={{ whiteSpace: 'pre-line' }}>{actions.alertDialog.message}</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={actions.closeAlert} autoFocus sx={ghostButtonSx(ChaosCypherPalette.primary)}>
            OK
          </Button>
        </DialogActions>
      </Dialog>
      {/* Confirm Dialog */}
      <Dialog open={actions.confirmDialog.isOpen} onClose={actions.confirmDialog.close} slotProps={{
        paper: { sx: ghostDialogPaperSx }
      }}>
        <DialogTitle>{actions.confirmDialog.data?.title}</DialogTitle>
        <DialogContent>
          <Typography>{actions.confirmDialog.data?.message}</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={actions.confirmDialog.close} sx={ghostCancelBtnSx}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              const onConfirm = actions.confirmDialog.data?.onConfirm;
              actions.confirmDialog.close();
              onConfirm?.();
            }}
            variant="outlined"
            autoFocus
            sx={ghostButtonSx(ChaosCypherPalette.warning)}
          >
            Confirm
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
