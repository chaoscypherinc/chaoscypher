// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Settings Page Actions Hook
 *
 * Encapsulates database management, import/export operations, and
 * settings persistence for the SettingsPage orchestrator.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { settingsApi } from '../../services/api/settings';
import { dataApi } from '../../services/api/data';
import { databaseApi } from '../../services/api/databases';
import { useSettings } from '../../contexts/useSettings';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { LLM_HEALTH_KEY } from '../../hooks/useLLMHealth';
import { getApiErrorMessage } from '../../utils/errors';
import { logger } from '../../utils/logger';
import type { Settings, DatabaseInfo } from '../../types';

/** Export options for data export. */
interface ExportOptions {
  includeTemplates: boolean;
  includeKnowledge: boolean;
  includeLenses: boolean;
  includeWorkflows: boolean;
  includeSources: boolean;
  includeEmbeddings: boolean;
}

/** Return type for the useSettingsActions hook. */
interface UseSettingsActionsReturn {
  // Settings state
  settings: Settings | null;
  setSettings: (settings: Settings) => void;
  loading: boolean;
  saving: boolean;
  success: boolean;

  // Import/export state
  importing: boolean;
  exporting: boolean;
  importSuccess: boolean;
  importError: string | null;
  setImportError: (error: string | null) => void;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  exportOptions: ExportOptions;
  setExportOptions: (options: ExportOptions) => void;

  // Database state
  databases: DatabaseInfo[];
  currentDatabase: string;
  newDatabaseName: string;
  setNewDatabaseName: (name: string) => void;
  creatingDatabase: boolean;

  // Alert dialog
  alertDialog: { open: boolean; title: string; message: string };
  closeAlert: () => void;

  // Confirm dialog (for reset/delete confirmations)
  confirmDialog: ReturnType<typeof useConfirmDialog<{ title: string; message: string; onConfirm: () => void }>>;

  // Actions
  handleSave: () => Promise<void>;
  handleReset: () => void;
  handleExport: () => Promise<void>;
  handleImport: (event: React.ChangeEvent<HTMLInputElement>) => Promise<void>;
  handleCreateDatabase: () => Promise<boolean>;
  handleDeleteDatabase: (name: string) => void;
  handleSwitchDatabase: (dbName: string) => Promise<void>;
  formatBytes: (bytes: number) => string;
}

/**
 * Hook managing all settings page actions including database operations,
 * import/export, and settings persistence.
 */
export function useSettingsActions(): UseSettingsActionsReturn {
  const { refreshSettings } = useSettings();

  // Settings state
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);

  // Import/export state
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [importSuccess, setImportSuccess] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [exportOptions, setExportOptions] = useState<ExportOptions>({
    includeTemplates: true,
    includeKnowledge: true,
    includeLenses: true,
    includeWorkflows: true,
    includeSources: true,
    includeEmbeddings: false,
  });

  const queryClient = useQueryClient();

  // Database state
  const [databases, setDatabases] = useState<DatabaseInfo[]>([]);
  const [currentDatabase, setCurrentDatabase] = useState<string>('default');
  const [newDatabaseName, setNewDatabaseName] = useState<string>('');
  const [creatingDatabase, setCreatingDatabase] = useState(false);

  // Alert dialog (informational, single OK button)
  const [alertDialog, setAlertDialog] = useState<{ open: boolean; title: string; message: string }>({
    open: false,
    title: '',
    message: '',
  });

  const closeAlert = useCallback(() => {
    setAlertDialog(prev => ({ ...prev, open: false }));
  }, []);

  const showAlert = useCallback((title: string, message: string) => {
    setAlertDialog({ open: true, title, message });
  }, []);

  // Confirm dialog for destructive actions
  const confirmDialog = useConfirmDialog<{ title: string; message: string; onConfirm: () => void }>();

  const loadSettings = useCallback(async () => {
    try {
      setLoading(true);
      const [settingsData, databasesData] = await Promise.all([
        settingsApi.get(),
        databaseApi.list(),
      ]);
      setSettings(settingsData);
      setDatabases(Array.isArray(databasesData) ? databasesData : []);
      setCurrentDatabase(settingsData.current_database || 'default');
    } catch (error) {
      logger.error('Failed to load settings:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  // Load settings on mount
  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  const handleSave = async () => {
    if (!settings) return;

    try {
      setSaving(true);
      setSuccess(false);
      const response = await settingsApi.update(settings);

      if (response.settings) {
        setSettings(response.settings as Settings);
      }

      const warningMessages = response.warnings?.filter(w => w.severity === 'warning') || [];
      if (warningMessages.length > 0) {
        showAlert(
          'Settings Saved with Warnings',
          warningMessages.map(w => w.message).join('\n\n')
        );
      }

      setSuccess(true);
      await refreshSettings();
      // Force the LLM health banner to re-evaluate immediately rather
      // than waiting up to 30s for the useLLMHealth refetch interval —
      // if the user just fixed their Ollama URL or pasted a real key,
      // the banner should clear on save, not later.
      await queryClient.invalidateQueries({ queryKey: LLM_HEALTH_KEY });
      setTimeout(() => setSuccess(false), 3000);
    } catch (error) {
      logger.error('Failed to save settings:', error);
      showAlert('Error', 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    confirmDialog.open({
      title: 'Reset Settings',
      message: 'Are you sure you want to reset all settings to defaults?',
      onConfirm: async () => {
        try {
          setSaving(true);
          const data = await settingsApi.reset();
          setSettings(data);
          await refreshSettings();
        } catch (error) {
          logger.error('Failed to reset settings:', error);
          showAlert('Error', 'Failed to reset settings');
        } finally {
          setSaving(false);
        }
      },
    });
  };

  const handleExport = async () => {
    // Validate required metadata before queueing.
    const packageName = (settings?.export?.export_package_name ?? '').trim();
    if (!packageName) {
      setImportError(
        'Package Name is required. Set it under Settings -> Export Defaults before exporting.',
      );
      return;
    }
    try {
      setExporting(true);
      setImportError(null);

      const blob = await dataApi.export(exportOptions);

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `knowledge_export_${new Date().toISOString().split('T')[0]}.ccx`;
      document.body.appendChild(a);
      a.click();

      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      logger.error('Export failed:', error);
      setImportError('Export failed. Please try again.');
    } finally {
      setExporting(false);
    }
  };

  const handleImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      setImporting(true);
      setImportSuccess(false);
      setImportError(null);

      const result = await dataApi.import(file, false);

      if (result.success) {
        setImportSuccess(true);
        setTimeout(() => {
          setImportSuccess(false);
          window.location.reload();
        }, 2000);
      } else {
        setImportError(`Import failed: ${result.errors.join(', ')}`);
      }
    } catch (error) {
      logger.error('Import failed:', error);
      setImportError('Import failed. Please check the file format.');
    } finally {
      setImporting(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleCreateDatabase = async (): Promise<boolean> => {
    if (!newDatabaseName.trim()) {
      showAlert('Validation Error', 'Please enter a database name');
      return false;
    }

    const dbName = newDatabaseName.trim();

    try {
      setCreatingDatabase(true);
      await databaseApi.create({ name: dbName });
      setNewDatabaseName('');

      await databaseApi.switch(dbName);
      window.location.reload();

      return true;
    } catch (error) {
      logger.error('Failed to create database:', error);
      showAlert('Error', getApiErrorMessage(error) || 'Failed to create database');
      return false;
    } finally {
      setCreatingDatabase(false);
    }
  };

  const handleDeleteDatabase = (name: string) => {
    confirmDialog.open({
      title: 'Delete Database',
      message: `Are you sure you want to delete database "${name}"? This action cannot be undone.`,
      onConfirm: async () => {
        try {
          await databaseApi.delete(name);
          await loadSettings();
          window.dispatchEvent(new CustomEvent('databaseListChanged'));
        } catch (error) {
          logger.error('Failed to delete database:', error);
          showAlert('Error', getApiErrorMessage(error) || 'Failed to delete database');
        }
      },
    });
  };

  const handleSwitchDatabase = async (dbName: string) => {
    if (dbName === currentDatabase) return;

    try {
      await databaseApi.switch(dbName);
      window.location.reload();
    } catch (error) {
      logger.error('Failed to switch database:', error);
      showAlert('Error', getApiErrorMessage(error) || 'Failed to switch database');
    }
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
  };

  return {
    settings,
    setSettings,
    loading,
    saving,
    success,
    importing,
    exporting,
    importSuccess,
    importError,
    setImportError,
    fileInputRef,
    exportOptions,
    setExportOptions,
    databases,
    currentDatabase,
    newDatabaseName,
    setNewDatabaseName,
    creatingDatabase,
    alertDialog,
    closeAlert,
    confirmDialog,
    handleSave,
    handleReset,
    handleExport,
    handleImport,
    handleCreateDatabase,
    handleDeleteDatabase,
    handleSwitchDatabase,
    formatBytes,
  };
}
