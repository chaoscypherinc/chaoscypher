// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared presentation logic for the merged Active / Search-status chip.
 *
 * Used by both the sources-list row (SourceStatusCell.ActiveStatus) and
 * the source detail page header (SourcePageHeader). The chip carries
 * two signals at once: source visibility (Active vs. Disabled) and
 * vector-search index health (pending / indexed / degraded / failed),
 * collapsing what used to be two separate chips.
 *
 * Decision matrix (see internal docs for rationale):
 *
 *   ┌──────────┬────────────────┬─────────────────┬──────────┐
 *   │ enabled  │ vector status  │ chip label      │ colour   │
 *   ├──────────┼────────────────┼─────────────────┼──────────┤
 *   │ false    │ any            │ Disabled        │ default  │
 *   │ true     │ degraded       │ Search retrying │ warning  │
 *   │ true     │ failed         │ Search failed   │ error    │
 *   │ true     │ pending        │ Active          │ success  │
 *   │ true     │ indexed        │ Active          │ success  │
 *   │ true     │ unknown/missing│ Active          │ success  │
 *   └──────────┴────────────────┴─────────────────┴──────────┘
 *
 * Disabled wins because a hidden source isn't searchable anyway, so
 * the index health is irrelevant to the operator. Unknown future
 * statuses fall back to "Active" so a newer backend can introduce a
 * state without crashing or inventing an alarming chip the UI hasn't
 * been taught yet.
 */

import CheckIcon from '@mui/icons-material/Check';
import ErrorOutlinedIcon from '@mui/icons-material/ErrorOutlined';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import type { SvgIconComponent } from '@mui/icons-material';

interface MergedChipState {
  label: string;
  color: 'default' | 'success' | 'warning' | 'error';
  Icon: SvgIconComponent;
  tooltip: string;
}

/**
 * Derive the merged status-chip presentation.
 *
 * @param isEnabled       Whether the source is visible in graph + search.
 * @param vectorStatus    Value of QualityMetrics.vector_indexing_status.
 * @param vectorIndexedAt ISO-8601 timestamp from
 *                        QualityMetrics.vector_indexed_at — used to add
 *                        a "last indexed at …" line to the tooltip when
 *                        the index is current.
 */
export function deriveMergedChipState(
  isEnabled: boolean,
  vectorStatus: string | null | undefined,
  vectorIndexedAt: string | null | undefined,
): MergedChipState {
  if (!isEnabled) {
    return {
      label: 'Disabled',
      color: 'default',
      Icon: VisibilityOffIcon,
      tooltip: 'Hidden from knowledge graph and search.',
    };
  }
  if (vectorStatus === 'degraded') {
    return {
      label: 'Search retrying',
      color: 'warning',
      Icon: WarningAmberIcon,
      tooltip:
        'Source is active, but the initial vector indexing failed. A retry is queued; the chip will clear once the retry succeeds.',
    };
  }
  if (vectorStatus === 'failed') {
    return {
      label: 'Search failed',
      color: 'error',
      Icon: ErrorOutlinedIcon,
      tooltip:
        'Source is active, but vector indexing failed after all retries. Re-extract the source to rebuild the search index.',
    };
  }
  let indexLine = '';
  if (vectorStatus === 'indexed') {
    indexLine = vectorIndexedAt
      ? ` Vector index is current (last indexed ${new Date(vectorIndexedAt).toLocaleString()}).`
      : ' Vector index is current.';
  } else if (vectorStatus === 'pending') {
    indexLine = ' The vector index is being built; search will be ready shortly.';
  }
  return {
    label: 'Active',
    color: 'success',
    Icon: CheckIcon,
    tooltip: `Visible in knowledge graph and search.${indexLine}`,
  };
}
