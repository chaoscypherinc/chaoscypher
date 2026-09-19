// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { describe, it, expect } from 'vitest';

import { SOURCE_STATUS_FILTER_OPTIONS } from '../sourceStatusFilterOptions';

// Mirrors `components['schemas']['SourceStatus']` in the generated API types.
// The option list is typed against that schema (tsc rejects a phantom value);
// this test pins the runtime list so a regenerated schema that drops a status
// is noticed here rather than as an always-empty filter in the UI.
const SOURCE_STATUSES = [
  'pending',
  'indexing',
  'vision_pending',
  'indexed',
  'awaiting_confirmation',
  'extracting',
  'mcp_extracting',
  'extracted',
  'committing',
  'committed',
  'error',
];

describe('SourcesFilters — status options', () => {
  it('offers exactly the SourceStatus values the API matches on', () => {
    // The value is sent verbatim to `GET /sources?status=` and matched
    // exactly against the row; the select used to offer `active` and
    // `archived`, neither of which any row ever carries.
    const values = SOURCE_STATUS_FILTER_OPTIONS.map((o) => o.value);
    expect(values).toEqual(SOURCE_STATUSES);
    expect(values).not.toContain('active');
    expect(values).not.toContain('archived');
  });

  it('gives every option a human label', () => {
    for (const option of SOURCE_STATUS_FILTER_OPTIONS) {
      expect(option.label.trim().length).toBeGreaterThan(0);
    }
  });
});
