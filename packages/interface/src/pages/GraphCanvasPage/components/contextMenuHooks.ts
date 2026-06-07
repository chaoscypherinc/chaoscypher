// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared context-menu state hooks for the graph canvas.
 *
 * Lives in its own file so ContextMenus.tsx can be Fast-Refresh-clean
 * (only-export-components rule).
 */
import { useState, useCallback } from 'react';
import type { GraphNodeData, GraphEdgeData } from '../types';

interface MenuPosition {
  mouseX: number;
  mouseY: number;
}

interface ContextMenuState<T> {
  position: MenuPosition | null;
  props: T | null;
}

export interface ContextMenuController<T> {
  state: ContextMenuState<T>;
  show: (args: { event: MouseEvent; props: T }) => void;
  close: () => void;
}

function useContextMenuState<T>(): ContextMenuController<T> {
  const [state, setState] = useState<ContextMenuState<T>>({
    position: null,
    props: null,
  });

  const show = useCallback(({ event, props }: { event: MouseEvent; props: T }) => {
    event.preventDefault();
    setState({
      position: { mouseX: event.clientX, mouseY: event.clientY },
      props,
    });
  }, []);

  const close = useCallback(() => {
    setState({ position: null, props: null });
  }, []);

  return { state, show, close };
}

export type NodeContextMenuController = ContextMenuController<{
  nodeId: string;
  data: GraphNodeData;
}>;

export type EdgeContextMenuController = ContextMenuController<{
  edgeId: string;
  data: GraphEdgeData;
}>;

export type CanvasContextMenuController = ContextMenuController<{ x: number; y: number }>;

export function useNodeContextMenu(): NodeContextMenuController {
  return useContextMenuState<{ nodeId: string; data: GraphNodeData }>();
}

export function useEdgeContextMenu(): EdgeContextMenuController {
  return useContextMenuState<{ edgeId: string; data: GraphEdgeData }>();
}

export function useCanvasContextMenu(): CanvasContextMenuController {
  return useContextMenuState<{ x: number; y: number }>();
}
