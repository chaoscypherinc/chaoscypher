// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Confirmation Dialog State Hook
 *
 * Manages the common pattern of a confirmation dialog that is associated
 * with a specific piece of data (e.g., the item about to be deleted) and
 * tracks loading state while the confirmed action executes.
 */

import { useState, useCallback } from 'react';

interface UseConfirmDialogReturn<T> {
  /** Whether the confirmation dialog is currently open */
  isOpen: boolean;
  /** The data associated with the pending confirmation, or null when closed */
  data: T | null;
  /** Whether the confirm callback is currently executing */
  isConfirming: boolean;
  /** Open the dialog with the associated data item */
  open: (item: T) => void;
  /** Close the dialog and clear associated data without confirming */
  close: () => void;
  /**
   * Execute the provided async callback, then close the dialog.
   * Sets `isConfirming` to true for the duration of the callback.
   * Closes the dialog whether the callback resolves or rejects.
   */
  confirm: (callback: () => Promise<void>) => Promise<void>;
}

/**
 * Hook for managing confirmation dialog state with associated data
 *
 * Replaces the repeated inline pattern of `isOpen` + `pendingItem` +
 * `handleOpen` + `handleClose` + loading state that appears across
 * components implementing delete/cancel confirmation flows.
 *
 * @typeParam T - The type of data associated with the confirmation. Use
 *   `void` (default) when no data is needed — `open()` still accepts
 *   no argument in that case via the overloaded call sites.
 * @returns Dialog state, control functions, and confirm executor
 *
 * @example
 * ```tsx
 * import { useConfirmDialog } from '@/hooks';
 *
 * interface Workflow { id: string; name: string; }
 *
 * function WorkflowList() {
 *   const deleteDialog = useConfirmDialog<Workflow>();
 *
 *   const handleDelete = async () => {
 *     await deleteDialog.confirm(async () => {
 *       await api.deleteWorkflow(deleteDialog.data!.id);
 *       refetch();
 *     });
 *   };
 *
 *   return (
 *     <>
 *       {workflows.map(wf => (
 *         <IconButton key={wf.id} onClick={() => deleteDialog.open(wf)}>
 *           <DeleteIcon />
 *         </IconButton>
 *       ))}
 *
 *       <ConfirmDialog
 *         open={deleteDialog.isOpen}
 *         title={`Delete "${deleteDialog.data?.name}"?`}
 *         loading={deleteDialog.isConfirming}
 *         onConfirm={handleDelete}
 *         onCancel={deleteDialog.close}
 *       />
 *     </>
 *   );
 * }
 * ```
 */
export function useConfirmDialog<T = void>(): UseConfirmDialogReturn<T> {
  const [isOpen, setIsOpen] = useState(false);
  const [data, setData] = useState<T | null>(null);
  const [isConfirming, setIsConfirming] = useState(false);

  const open = useCallback((item: T) => {
    setData(item);
    setIsOpen(true);
  }, []);

  const close = useCallback(() => {
    setData(null);
    setIsOpen(false);
  }, []);

  const confirm = useCallback(async (callback: () => Promise<void>) => {
    setIsConfirming(true);
    try {
      await callback();
    } finally {
      setIsConfirming(false);
      setIsOpen(false);
      setData(null);
    }
  }, []);

  return { isOpen, data, isConfirming, open, close, confirm };
}
