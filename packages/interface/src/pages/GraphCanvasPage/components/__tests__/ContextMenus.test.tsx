// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { NodeContextMenu } from '../ContextMenus';
import type { NodeContextMenuController } from '../contextMenuHooks';
import type { GraphNodeData } from '../../types';
import { unresolvedBucketId } from '../../types';

/** The hub for unresolved nodes with no known source. */
const UNRESOLVED_BUCKET_ID = unresolvedBucketId(null);

function openMenu(nodeId: string): NodeContextMenuController {
  const data: GraphNodeData = {
    nodeId, title: nodeId, content: {}, templateId: 't', tags: [], createdAt: '', updatedAt: '',
  };
  return {
    state: { position: { mouseX: 10, mouseY: 10 }, props: { nodeId, data } },
    show: vi.fn(),
    close: vi.fn(),
  };
}

function renderMenu(nodeId: string, unresolvedExpanded = false) {
  const handlers = {
    onEdit: vi.fn(),
    onDelete: vi.fn(),
    onDuplicate: vi.fn(),
    onCopyId: vi.fn(),
    onToggleUnresolved: vi.fn(),
  };
  const menuState = openMenu(nodeId);
  render(
    <NodeContextMenu
      {...handlers}
      menuState={menuState}
      isUnresolvedExpanded={() => unresolvedExpanded}
    />,
  );
  return { handlers, menuState };
}

describe('NodeContextMenu', () => {
  it('offers only show/hide on the Unresolved hub, nothing that edits it', () => {
    const { handlers, menuState } = renderMenu(UNRESOLVED_BUCKET_ID);
    expect(screen.getAllByRole('menuitem')).toHaveLength(1);
    expect(screen.queryByText('Edit Properties')).not.toBeInTheDocument();
    expect(screen.queryByText('Delete Item')).not.toBeInTheDocument();
    expect(screen.queryByText('Duplicate Item')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('menuitem', { name: 'Show Unresolved Nodes' }));
    expect(handlers.onToggleUnresolved).toHaveBeenCalledWith(UNRESOLVED_BUCKET_ID);
    expect(menuState.close).toHaveBeenCalled();
  });

  it('toggles the hub it was opened on (a source\'s own hub)', () => {
    const { handlers } = renderMenu(unresolvedBucketId('src-1'));
    expect(screen.queryByText('Edit Properties')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Show Unresolved Nodes' }));
    expect(handlers.onToggleUnresolved).toHaveBeenCalledWith(unresolvedBucketId('src-1'));
  });

  it('names the action by the current state', () => {
    renderMenu(UNRESOLVED_BUCKET_ID, true);
    expect(screen.getByRole('menuitem', { name: 'Hide Unresolved Nodes' })).toBeInTheDocument();
  });

  it('keeps the full menu for a real node', () => {
    renderMenu('n1');
    expect(screen.getByRole('menuitem', { name: 'Edit Properties' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Delete Item' })).toBeInTheDocument();
    expect(screen.queryByText('Show Unresolved Nodes')).not.toBeInTheDocument();
  });
});
