// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { ChaosCypherPalette } from '../../../theme/palette';

/** Service name to brand color mapping (shared with LogsTab and ServiceStatusBar). */
export const SERVICE_COLORS: Record<string, string> = {
  cortex: ChaosCypherPalette.primary,
  neuron: ChaosCypherPalette.warning,
  nginx: ChaosCypherPalette.purple,
  valkey: ChaosCypherPalette.accent,
};
