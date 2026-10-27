// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { GraphNodeData } from '../../types';
import { SOURCE_GROUP_PREFIX, unresolvedBucketId } from '../../types';
import { useKeyboardShortcuts } from '../useKeyboardShortcuts';

/** The hub for unresolved nodes with no known source. */
const UNRESOLVED_BUCKET_ID = unresolvedBucketId(null);

const holder = vi.hoisted(() => ({ sigma: null as unknown }));
vi.mock('@react-sigma/core', () => ({ useSigma: () => holder.sigma }));

function nodeData(nodeId: string): GraphNodeData {
  return {
    nodeId, title: nodeId, content: {}, templateId: 't', tags: [], createdAt: '', updatedAt: '',
  };
}

function renderShortcuts(selectedNodeId: string) {
  holder.sigma = {
    getCamera: () => ({ animatedReset: vi.fn(), animatedZoom: vi.fn(), animatedUnzoom: vi.fn() }),
  };
  const handleNodeDuplicate = vi.fn<(nodeId: string, data: GraphNodeData) => Promise<void>>();
  const view = renderHook(() =>
    useKeyboardShortcuts({
      selectedNodeId,
      selectedNodeData: nodeData(selectedNodeId),
      selectedEdgeId: null,
      clearSelection: vi.fn(),
      handleNodeDelete: vi.fn<(nodeId: string) => Promise<void>>(),
      handleEdgeDelete: vi.fn<(edgeId: string) => Promise<void>>(),
      handleNodeDuplicate,
    }),
  );
  return { view, handleNodeDuplicate };
}

function press(key: string) {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key }));
  });
}

describe('useKeyboardShortcuts', () => {
  it('asks before deleting a selected real node, and duplicates it on "d"', () => {
    const { view, handleNodeDuplicate } = renderShortcuts('n1');
    press('Delete');
    expect(view.result.current.pendingDelete).toMatchObject({ type: 'node', id: 'n1' });
    press('d');
    expect(handleNodeDuplicate).toHaveBeenCalledTimes(1);
  });

  it.each([UNRESOLVED_BUCKET_ID, `${SOURCE_GROUP_PREFIX}s1`])(
    'never offers to delete or duplicate the synthetic node %s',
    (id) => {
      const { view, handleNodeDuplicate } = renderShortcuts(id);
      press('Delete');
      press('Backspace');
      press('d');
      expect(view.result.current.pendingDelete).toBeNull();
      expect(handleNodeDuplicate).not.toHaveBeenCalled();
    },
  );
});
