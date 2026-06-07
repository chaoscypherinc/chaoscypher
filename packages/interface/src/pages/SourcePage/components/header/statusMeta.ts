// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

type StatusChipColor =
  | 'default'
  | 'primary'
  | 'secondary'
  | 'success'
  | 'error'
  | 'info'
  | 'warning';

/** Maps a source status string to a MUI Chip color. */
export const getStatusColor = (status: string): StatusChipColor => {
  const colors: Record<string, StatusChipColor> = {
    pending: 'default',
    indexing: 'info',
    indexed: 'info',
    extracting: 'warning',
    mcp_extracting: 'warning',
    extracted: 'warning',
    committing: 'secondary',
    committed: 'success',
    error: 'error',
  };
  return colors[status] || 'default';
};

/** Maps a source status string to a human-readable label. */
export const getStatusLabel = (status: string): string => {
  const labels: Record<string, string> = {
    pending: 'Pending',
    indexing: 'Indexing...',
    indexed: 'Indexed',
    extracting: 'Extracting...',
    mcp_extracting: 'MCP Extracting...',
    extracted: 'Extracted',
    committing: 'Committing...',
    committed: 'Committed',
    error: 'Error',
  };
  return labels[status] || status;
};
