// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FileInfoTooltip } from '../FileInfoTooltip';
import type { Source } from '../../../../../types';

function makeSource(overrides: Partial<Source> = {}): Source {
  return {
    id: 's1',
    filename: 'doc.txt',
    upload_options: {
      auto_analyze: true,
      extraction_depth: 'full',
      forced_domain: null,
      enable_normalization: null,
      enable_vision: false,
      content_filtering: false,
      filtering_mode: 'balanced',
    },
    ...overrides,
  } as Source;
}

describe('FileInfoTooltip domain provenance', () => {
  it('shows the domain version when present', () => {
    render(<FileInfoTooltip source={makeSource({ domain_version: '1.9.0' })} />);
    expect(screen.getByText(/v1\.9\.0/)).toBeInTheDocument();
  });

  it('shows the changed-since-extraction warning when stale', () => {
    render(
      <FileInfoTooltip
        source={makeSource({ domain_version: '1.9.0', domain_changed_since_extraction: true })}
      />,
    );
    expect(screen.getByText(/changed since extraction/i)).toBeInTheDocument();
  });

  it('omits version and warning when absent', () => {
    render(<FileInfoTooltip source={makeSource()} />);
    expect(screen.queryByText(/changed since extraction/i)).not.toBeInTheDocument();
  });
});
