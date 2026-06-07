// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Omnibar React context — shared between OmnibarProvider and useOmnibar.
 */
import { createContext } from 'react';
import type { OmnibarContextValue } from './types';

export const OmnibarContext = createContext<OmnibarContextValue | null>(null);
