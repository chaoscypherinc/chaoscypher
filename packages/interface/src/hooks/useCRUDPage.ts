// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Generic CRUD Page Hook
 * Provides common state and logic for pages with CRUD operations
 */

import { useState, useEffect, useCallback } from 'react';
import { useBulkOperation } from './useBulkOperation';
import { useSelection } from './useSelection';
import { useNotification } from '../contexts/useNotification';
import { logger } from '../utils/logger';

interface UseCRUDPageOptions<T> {
  /** Name of entity for API calls (e.g., 'nodes', 'edges', 'templates') */
  entityName: string;
  /** Display name for user messages (e.g., 'Item', 'Link', 'Template') */
  entityDisplayName: string;
  /** Function to load data */
  loadDataFn: () => Promise<T[]>;
}

interface BulkDeleteConfirm {
  /** Whether the bulk delete confirmation dialog is open */
  open: boolean;
  /** The confirmation message to display */
  message: string;
}

interface UseCRUDPageReturn<T> {
  /** Loaded data */
  data: T[];
  /** Loading state */
  loading: boolean;
  /** Error message if any */
  error: string | null;
  /** Selected item IDs */
  selectedIds: Set<string>;
  /** Dialog open state */
  dialogOpen: boolean;
  /** Currently editing entity (null for create) */
  editingEntity: T | null;
  /** Reload data from source */
  loadData: () => Promise<void>;
  /** Handle select all checkbox */
  handleSelectAll: (event: React.ChangeEvent<HTMLInputElement>) => void;
  /** Handle individual item selection */
  handleSelectItem: (itemId: string) => void;
  /** Handle bulk delete operation - opens confirmation dialog */
  handleBulkDelete: () => void;
  /** Confirm the pending bulk delete */
  confirmBulkDelete: () => Promise<void>;
  /** Cancel the pending bulk delete */
  cancelBulkDelete: () => void;
  /** Bulk delete confirmation state */
  bulkDeleteConfirm: BulkDeleteConfirm;
  /** Handle create (opens dialog) */
  handleCreate: () => void;
  /** Handle edit (opens dialog with entity) */
  handleEdit: (entity: T) => void;
  /** Close dialog */
  handleCloseDialog: () => void;
  /** Set dialog open state */
  setDialogOpen: (open: boolean) => void;
  /** Replace selected IDs */
  setSelectedIds: (ids: Set<string>) => void;
  /** Bulk operation progress dialog component */
  ProgressDialog: React.ComponentType;
}

/**
 * Hook for managing CRUD page state and operations
 *
 * @example
 * const {
 *   data: nodes,
 *   loading,
 *   handleCreate,
 *   handleEdit,
 *   ProgressDialog,
 *   // ... other values
 * } = useCRUDPage({
 *   entityName: 'nodes',
 *   entityDisplayName: 'Item',
 *   loadDataFn: async () => {
 *     const response = await nodeApi.list();
 *     return response;
 *   }
 * });
 */
export function useCRUDPage<T extends { id: string }>({
  entityName,
  entityDisplayName,
  loadDataFn,
}: UseCRUDPageOptions<T>): UseCRUDPageReturn<T> {
  const [data, setData] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const selection = useSelection<string>();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingEntity, setEditingEntity] = useState<T | null>(null);
  const [bulkDeleteConfirm, setBulkDeleteConfirm] = useState<BulkDeleteConfirm>({ open: false, message: '' });

  // Use the bulk operation hook
  const { execute: executeBulkOperation, ProgressDialog } = useBulkOperation();
  const { notify } = useNotification();

  // Load data function - stable reference unless displayName changes
  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await loadDataFn();
      setData(result);
    } catch (err) {
      logger.error(`Failed to load ${entityDisplayName}s:`, err);
      setError(`Failed to load ${entityDisplayName}s`);
    } finally {
      setLoading(false);
    }
  }, [loadDataFn, entityDisplayName]);

  // Load data on mount and when dependencies change
  useEffect(() => {
    loadData();
  }, [loadData]);

  // Handle select all
  const handleSelectAll = useCallback(
    (_event: React.ChangeEvent<HTMLInputElement>) => {
      selection.toggleAll(data.map((item) => item.id));
    },
    [data, selection]
  );

  // Handle select individual item
  const handleSelectItem = useCallback((itemId: string) => {
    selection.toggle(itemId);
  }, [selection]);

  // Handle bulk delete - opens confirmation dialog
  const handleBulkDelete = useCallback(() => {
    if (selection.selectedCount === 0) return;

    const message = `Are you sure you want to delete ${selection.selectedCount} ${entityDisplayName.toLowerCase()}${
      selection.selectedCount !== 1 ? 's' : ''
    }?`;
    setBulkDeleteConfirm({ open: true, message });
  }, [selection.selectedCount, entityDisplayName]);

  // Confirm the pending bulk delete
  const confirmBulkDelete = useCallback(async () => {
    setBulkDeleteConfirm({ open: false, message: '' });

    try {
      const operations = Array.from(selection.selected).map((id) => ({
        operation: 'delete' as const,
        data: { id },
      }));

      const result = await executeBulkOperation(entityName, operations);

      notify(
        `Deleted ${result.success} ${entityDisplayName.toLowerCase()}${result.success !== 1 ? 's' : ''}${result.failed > 0 ? `, ${result.failed} failed` : ''}`,
        result.failed > 0 ? 'warning' : 'success'
      );

      // Clear selection and reload
      selection.clear();
      await loadData();
    } catch (err) {
      logger.error(`Failed to delete ${entityDisplayName}s:`, err);
      notify(`Failed to delete some ${entityDisplayName}s`, 'error');
    }
  }, [selection, entityName, entityDisplayName, loadData, executeBulkOperation, notify]);

  // Cancel the pending bulk delete
  const cancelBulkDelete = useCallback(() => {
    setBulkDeleteConfirm({ open: false, message: '' });
  }, []);

  // Handle create
  const handleCreate = useCallback(() => {
    setEditingEntity(null);
    setDialogOpen(true);
  }, []);

  // Handle edit
  const handleEdit = useCallback((entity: T) => {
    setEditingEntity(entity);
    setDialogOpen(true);
  }, []);

  // Handle close dialog
  const handleCloseDialog = useCallback(() => {
    setDialogOpen(false);
    setEditingEntity(null);
  }, []);

  // Expose setter that accepts a Set for consumers with custom select-all logic
  const setSelectedIds = useCallback(
    (ids: Set<string>) => {
      // Clear and replace: toggleAll would toggle, so we directly set via clear + toggleAll
      // For direct replacement, we need to clear first then select the new set
      selection.clear();
      if (ids.size > 0) {
        selection.toggleAll(Array.from(ids));
      }
    },
    [selection]
  );

  return {
    data,
    loading,
    error,
    selectedIds: selection.selected,
    dialogOpen,
    editingEntity,
    loadData,
    handleSelectAll,
    handleSelectItem,
    handleBulkDelete,
    confirmBulkDelete,
    cancelBulkDelete,
    bulkDeleteConfirm,
    handleCreate,
    handleEdit,
    handleCloseDialog,
    setDialogOpen,
    setSelectedIds,
    ProgressDialog,
  };
}
