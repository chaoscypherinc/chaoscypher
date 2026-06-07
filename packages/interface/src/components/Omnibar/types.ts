// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Type definitions for the Omnibar component system.
 * Defines the mode interface contract, recent items, commands, and context.
 */
/** Interface contract for omnibar mode handlers. */
export interface OmnibarMode {
  /** Prefix that activates this mode (">" for commands, "/" for chat, "?" for help, "" for search). */
  prefix: string;
  /** Display label shown in the mode indicator pill. */
  label: string;
  /** Accent color for this mode. */
  color: string;
  /** Placeholder text for the input when this mode is active. */
  placeholder: string;
}

/** Props passed to each mode's result renderer component. */
export interface ModeResultsProps {
  query: string;
  selectedIndex: number;
  onExecute: (index: number) => void;
  onClose: () => void;
  onItemCount: (count: number) => void;
}

/** A recent item displayed in state zero. */
export interface RecentItem {
  id: string;
  type: 'entity' | 'source' | 'chat';
  title: string;
  subtitle: string;
  icon: string;
  timestamp: number;
}

/** A command in the command registry. */
export interface OmnibarCommand {
  id: string;
  label: string;
  description: string;
  keywords: string[];
  icon: string;
  category: 'navigation' | 'action';
  destructive?: boolean;
  action: () => void;
}

/** Options for programmatically opening the omnibar. */
export interface OmnibarOpenOptions {
  initialQuery?: string;
  initialMode?: string;
}

/** Context value exposed by OmnibarProvider. */
export interface OmnibarContextValue {
  open: (options?: OmnibarOpenOptions) => void;
  close: () => void;
  isOpen: boolean;
  /** Incremented each time the omnibar opens — used as React key to force re-mount. */
  openKey: number;
  /** Options passed to the most recent open() call. */
  openOptions: OmnibarOpenOptions;
  /** Anchor element for positioning the dropdown. Set by the trigger. */
  anchorEl: HTMLElement | null;
  setAnchorEl: (el: HTMLElement | null) => void;
}
