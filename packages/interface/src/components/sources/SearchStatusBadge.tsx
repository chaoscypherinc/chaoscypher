// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SearchStatusBadge — vector-search indexing state for a single source.
 *
 * Workstream 10 (2026-05-07): the commit pipeline owns four mutually-
 * exclusive states on ``SourceRow.vector_indexing_status``:
 *
 *   * ``pending``   — post-upload default; indexing has not yet
 *                     completed.
 *   * ``indexed``   — both node and chunk vector writes succeeded;
 *                     ``vector_indexed_at`` is timestamped.
 *   * ``degraded``  — at least one indexing call failed at commit
 *                     time and a retry is queued for the orphan-sweep
 *                     worker.
 *   * ``failed``    — the sweep worker exhausted its retry budget; the
 *                     operator should re-extract the source.
 *
 * Each state renders a small, color-coded chip with an icon and a
 * tooltip that explains the state and (for ``failed``) hints at the
 * recommended action. The badge is intentionally compact so it fits
 * inside a table cell as well as the source-detail header strip.
 */

import { Chip, Tooltip } from '@mui/material';
import CheckCircleOutlinedIcon from '@mui/icons-material/CheckCircleOutlined';
import ErrorOutlinedIcon from '@mui/icons-material/ErrorOutlined';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';

interface SearchStatusBadgeProps {
  /** Status string from ``QualityMetrics.vector_indexing_status``. */
  status: string | null | undefined;
  /** ISO-8601 timestamp from ``QualityMetrics.vector_indexed_at``. */
  indexedAt?: string | null;
  /**
   * Render at compact size. Defaults to true (matches table-row use);
   * pass ``false`` for header placement so the chip sits at MUI's
   * default size.
   */
  compact?: boolean;
}

interface StateConfig {
  label: string;
  color: 'default' | 'success' | 'warning' | 'error';
  Icon: typeof CheckCircleOutlinedIcon;
  tooltip: (indexedAt?: string | null) => string;
}

const STATE_CONFIG: Record<string, StateConfig> = {
  pending: {
    label: 'Search pending',
    color: 'default',
    Icon: HourglassEmptyIcon,
    tooltip: () => 'The vector index is being built; search will be ready shortly.',
  },
  indexed: {
    label: 'Search ready',
    color: 'success',
    Icon: CheckCircleOutlinedIcon,
    tooltip: (indexedAt) =>
      indexedAt
        ? `Vector index is current (last indexed ${new Date(indexedAt).toLocaleString()}).`
        : 'Vector index is current.',
  },
  degraded: {
    label: 'Search retrying',
    color: 'warning',
    Icon: WarningAmberIcon,
    tooltip: () =>
      'Initial vector indexing failed. A retry is queued; the badge will clear once the retry succeeds.',
  },
  failed: {
    label: 'Search failed',
    color: 'error',
    Icon: ErrorOutlinedIcon,
    tooltip: () =>
      'Vector indexing failed after all retries. Re-extract the source to rebuild the search index.',
  },
};

/**
 * Render the search-status badge for a source.
 *
 * Defensive: an unrecognised ``status`` (e.g. a future state added by
 * a newer backend) falls back to the ``pending`` styling so the row
 * still renders rather than crashing the table.
 */
export function SearchStatusBadge({ status, indexedAt, compact = true }: SearchStatusBadgeProps) {
  const knownStatus = status && status in STATE_CONFIG ? status : 'pending';
  const cfg = STATE_CONFIG[knownStatus];
  const { label, color, Icon, tooltip } = cfg;

  return (
    <Tooltip title={tooltip(indexedAt)} arrow>
      <Chip
        size={compact ? 'small' : 'medium'}
        label={label}
        color={color}
        variant="outlined"
        icon={<Icon sx={{ fontSize: compact ? 14 : 18 }} />}
        sx={{
          height: compact ? 22 : undefined,
          fontSize: compact ? '0.7rem' : undefined,
          // Hide pending status by collapsing height; the empty default
          // is the most common state and a visible "pending" chip on
          // every fresh source clutters the table. Operators only need
          // to see the badge once it has progressed past pending.
          ...(knownStatus === 'pending' && { display: 'none' }),
        }}
      />
    </Tooltip>
  );
}
