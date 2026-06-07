// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { FilterStageItems } from '../FilterStageItems';
import type { FilteringLog } from '../../../../types';

const log: FilteringLog = {
  version: 1,
  total_removed: 3,
  stages: [
    {
      stage: 'structural_entity_filter',
      input_count: 10,
      removed_count: 2,
      items: [
        { item_type: 'entity', name: 'Chapter 4', entity_type: 'document_structure', reason: 'matches structural pattern' },
        { item_type: 'entity', name: 'Page 12', entity_type: 'document_structure', reason: 'matches structural pattern' },
      ],
    },
    {
      stage: 'relationship_index_validation',
      input_count: 50,
      removed_count: 1,
      items: [
        { item_type: 'relationship', name: 'a → b', entity_type: '', reason: 'target out of bounds' },
      ],
    },
  ],
};

describe('FilterStageItems', () => {
  it('renders stage labels via STAGE_META map', () => {
    render(<FilterStageItems filteringLog={log} />);
    expect(screen.getByText('Structural Filter')).toBeInTheDocument();
    expect(screen.getByText('Index Validation')).toBeInTheDocument();
  });

  it('renders each item row', () => {
    render(<FilterStageItems filteringLog={log} />);
    expect(screen.getByText('Chapter 4')).toBeInTheDocument();
    expect(screen.getByText('Page 12')).toBeInTheDocument();
    expect(screen.getByText('a → b')).toBeInTheDocument();
  });

  it('renders nothing when log is null', () => {
    const { container } = render(<FilterStageItems filteringLog={null} />);
    expect(container.firstChild).toBeNull();
  });
});
