// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { NodeTable } from '../NodeTable';
import type { Node } from '../../../types';

function node(id: string, label: string, properties: Record<string, unknown>): Node {
  return {
    id,
    label,
    template_id: 'person',
    properties,
    created_at: '2026-10-07T00:00:00Z',
    updated_at: '2026-10-07T00:00:00Z',
  };
}

function renderTable(nodes: Node[]) {
  return render(
    <NodeTable
      nodes={nodes}
      templates={[]}
      selectedNodes={new Set()}
      page={1}
      totalPages={1}
      total={nodes.length}
      pageSize={50}
      onSelectAll={() => {}}
      onSelectNode={() => {}}
      onViewDetail={() => {}}
      onEdit={() => {}}
      onDelete={() => {}}
      onNavigate={() => {}}
      onPageChange={() => {}}
    />,
  );
}

describe('NodeTable pipeline flag chips', () => {
  it('shows a chip per flag on flagged rows only', () => {
    renderTable([
      node('n1', 'Pierre', { occupation: 'count' }),
      node('n2', 'the count', { descriptor_reference: true, orphan: true }),
      node('n3', 'Natasha', { orphan: 'true' }),
    ]);

    const rows = screen.getAllByRole('row').slice(1); // drop the header row
    const rowFor = (label: string) => rows.find((r) => within(r).queryByText(label))!;

    expect(within(rowFor('the count')).getByText('Unconnected')).toBeInTheDocument();
    expect(within(rowFor('the count')).getByText('Reference')).toBeInTheDocument();
    expect(within(rowFor('Pierre')).queryByText('Unconnected')).toBeNull();
    expect(within(rowFor('Natasha')).queryByText('Unconnected')).toBeNull();
  });
});
