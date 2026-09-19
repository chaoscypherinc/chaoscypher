// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { components } from '../../types/generated/api';

type SourceStatus = components['schemas']['SourceStatus'];

/**
 * Status filter options for the Sources page. Typed against the generated
 * `SourceStatus` schema so a value the API does not know cannot be offered:
 * the value is passed verbatim to `GET /sources?status=` and matched exactly
 * against the row, so a phantom value (this select used to offer `active`
 * and `archived`) always returned an empty list.
 */
export const SOURCE_STATUS_FILTER_OPTIONS: ReadonlyArray<{
  value: SourceStatus;
  label: string;
}> = [
  { value: 'pending', label: 'Pending' },
  { value: 'indexing', label: 'Indexing' },
  { value: 'vision_pending', label: 'Vision pending' },
  { value: 'indexed', label: 'Indexed' },
  { value: 'awaiting_confirmation', label: 'Awaiting confirmation' },
  { value: 'extracting', label: 'Extracting' },
  { value: 'mcp_extracting', label: 'MCP extracting' },
  { value: 'extracted', label: 'Extracted' },
  { value: 'committing', label: 'Committing' },
  { value: 'committed', label: 'Committed' },
  { value: 'error', label: 'Error' },
];
