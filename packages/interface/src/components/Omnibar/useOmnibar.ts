// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Hook to control the omnibar from any component.
 * Must be used within OmnibarProvider.
 */
import { useContext } from 'react';
import { OmnibarContext } from './OmnibarContext';
import type { OmnibarContextValue } from './types';

export function useOmnibar(): OmnibarContextValue {
  const context = useContext(OmnibarContext);
  if (!context) {
    throw new Error('useOmnibar must be used within OmnibarProvider');
  }
  return context;
}
