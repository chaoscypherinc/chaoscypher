// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ChunkFilteredItems } from '../ChunkFilteredItems';
import type { FilteringLog } from '../../../../../types';

const log: FilteringLog = {
  version: 1,
  total_removed: 4,
  stages: [
    {
      stage: 'structural_entity_filter',
      input_count: 10,
      removed_count: 2,
      items: [
        { item_type: 'entity', name: 'Chapter 4', entity_type: 'document_structure', reason: 'match' },
        { item_type: 'entity', name: 'Page 12', entity_type: 'document_structure', reason: 'match' },
      ],
    },
    {
      stage: 'relationship_index_validation',
      input_count: 50,
      removed_count: 2,
      items: [
        { item_type: 'relationship', name: 'a → b', entity_type: '', reason: 'oob' },
        { item_type: 'relationship', name: 'c → d', entity_type: '', reason: 'oob' },
      ],
    },
  ],
};

describe('ChunkFilteredItems', () => {
  it('renders header counts: N entities + M relationships', () => {
    render(<ChunkFilteredItems filteringLog={log} />);
    expect(screen.getByText(/2 entities \+ 2 relationships/i)).toBeInTheDocument();
  });

  it('renders FilterStageItems body', () => {
    render(<ChunkFilteredItems filteringLog={log} />);
    expect(screen.getByText('Chapter 4')).toBeInTheDocument();
    expect(screen.getByText('a → b')).toBeInTheDocument();
  });

  it('renders nothing when log is null', () => {
    const { container } = render(<ChunkFilteredItems filteringLog={null} />);
    expect(container.firstChild).toBeNull();
  });
});
