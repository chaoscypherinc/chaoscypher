// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Pure helpers used by the model-config sub-components.
 *
 * Lives in its own file so ModelConfig.tsx is Fast-Refresh-clean
 * (only-export-components rule).
 */
import { formatCompactNumber } from '../../../utils/formatters';

/** Helper to format large numbers as K/M. */
export const formatTokens = formatCompactNumber;

/** Shared style for number inputs that hides browser spin buttons. */
export const numberInputSx = {
  '& input::-webkit-outer-spin-button, & input::-webkit-inner-spin-button': { display: 'none' },
  '& input[type=number]': { MozAppearance: 'textfield' },
};
