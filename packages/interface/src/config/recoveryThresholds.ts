// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Recovery thresholds — sourced from backend `/api/v1/settings/public`
 * via `useAppConfig`. Do NOT use literal exports from this file in new
 * code; this module exists only as a hook now.
 *
 * The values mirror:
 * - `recovery_warn_threshold`: SourceRecoverySettings.recovery_warn_threshold
 * - `recovery_max_attempts`: SourceRecoverySettings.max_recovery_attempts
 *
 * Both come from the live `/api/v1/settings/public` payload, which is
 * bootstrapped on app load and refreshed if the operator changes settings.
 */
import { useAppConfig } from '../contexts/useAppConfig';

interface RecoveryThresholds {
  /** Number of recovery attempts before showing a warning to the operator. */
  warnThreshold: number;
  /** Hard cap on recovery attempts before the source is marked errored. */
  maxAttempts: number;
}

/**
 * Read the operator-configured recovery thresholds.
 *
 * Must be called from a React function component or hook (uses
 * useContext under the hood). Module-init code should fall back to
 * `DEFAULT_PUBLIC_SETTINGS.recovery_*` instead.
 */
export function useRecoveryThresholds(): RecoveryThresholds {
  const config = useAppConfig();
  return {
    warnThreshold: config.recovery_warn_threshold,
    maxAttempts: config.recovery_max_attempts,
  } as const;
}
