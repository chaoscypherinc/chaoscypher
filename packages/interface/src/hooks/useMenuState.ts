// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * MUI Menu Anchor State Hook
 *
 * Manages the common pattern of binding a MUI Menu to a trigger element
 * via an HTMLElement anchor ref and open/close handlers.
 */

import { useState, useCallback } from 'react';

interface UseMenuStateReturn {
  /** The anchor element that positions the Menu, or null when closed */
  anchorEl: HTMLElement | null;
  /** Whether the menu is currently open */
  isOpen: boolean;
  /** Open the menu, anchored to the event's current target */
  open: (event: React.MouseEvent<HTMLElement>) => void;
  /** Close the menu and clear the anchor */
  close: () => void;
}

/**
 * Hook for managing MUI Menu anchor element state
 *
 * Replaces the repeated inline pattern of `anchorEl` + `handleOpen` +
 * `handleClose` that appears across many components. Memoises both
 * handlers so child components that receive them won't re-render when
 * the parent re-renders for unrelated reasons.
 *
 * @returns Menu anchor state and stable open/close handlers
 *
 * @example
 * ```tsx
 * import { useMenuState } from '@/hooks';
 *
 * function MyComponent() {
 *   const menu = useMenuState();
 *
 *   return (
 *     <>
 *       <IconButton onClick={menu.open}>
 *         <MoreVertIcon />
 *       </IconButton>
 *
 *       <Menu
 *         anchorEl={menu.anchorEl}
 *         open={menu.isOpen}
 *         onClose={menu.close}
 *       >
 *         <MenuItem onClick={() => { handleEdit(); menu.close(); }}>Edit</MenuItem>
 *         <MenuItem onClick={() => { handleDelete(); menu.close(); }}>Delete</MenuItem>
 *       </Menu>
 *     </>
 *   );
 * }
 * ```
 */
export function useMenuState(): UseMenuStateReturn {
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);

  const open = useCallback((event: React.MouseEvent<HTMLElement>) => {
    setAnchorEl(event.currentTarget);
  }, []);

  const close = useCallback(() => {
    setAnchorEl(null);
  }, []);

  return {
    anchorEl,
    isOpen: anchorEl !== null,
    open,
    close,
  };
}
