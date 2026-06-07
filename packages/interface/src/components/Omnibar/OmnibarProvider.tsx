// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * OmnibarProvider — context provider for omnibar open/close state.
 * Registers the global Cmd+K / Ctrl+K keyboard shortcut.
 * Does NOT render the Omnibar Dialog — that is rendered by the consumer
 * (Layout) to ensure it has access to all required contexts.
 */
import {
  useState,
  useCallback,
  useEffect,
  type ReactNode,
} from 'react';
import type { OmnibarOpenOptions } from './types';
import { OmnibarContext } from './OmnibarContext';

export function OmnibarProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [openOptions, setOpenOptions] = useState<OmnibarOpenOptions>({});
  const [openKey, setOpenKey] = useState(0);
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);

  const open = useCallback((options?: OmnibarOpenOptions) => {
    setOpenOptions(options ?? {});
    setIsOpen(true);
    setOpenKey((k) => k + 1);
  }, []);

  const close = useCallback(() => {
    setIsOpen(false);
    setOpenOptions({});
  }, []);

  // Global Cmd+K / Ctrl+K listener
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setIsOpen((prev) => {
          if (prev) return false;
          setOpenOptions({});
          setOpenKey((k) => k + 1);
          return true;
        });
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return (
    <OmnibarContext.Provider value={{ open, close, isOpen, openKey, openOptions, anchorEl, setAnchorEl }}>
      {children}
    </OmnibarContext.Provider>
  );
}
