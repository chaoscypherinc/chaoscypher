// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Node property state management hook.
 *
 * Synchronizes local editing state (title, properties, tags) with the
 * selected graph node data and handles template loading for property
 * field definitions.
 */

import { useState, useEffect, useCallback } from 'react';
import type { GraphNodeData } from '../types';
import { useTemplate } from '../../../services/api/useTemplates';
import { useNode } from '../../../services/api/useNodes';
import type { Template } from '../../../types';

interface UseNodePropertyStateReturn {
  /** Current node title in the editor. */
  nodeTitle: string;
  /** Setter for the node title. */
  setNodeTitle: (title: string) => void;
  /** Current node properties in the editor. */
  nodeProperties: Record<string, unknown>;
  /** Update a single property by name. */
  handlePropertyChange: (propName: string, value: unknown) => void;
  /** Current node tags in the editor. */
  nodeTags: string[];
  /** Replace the full tags array. */
  setNodeTags: (tags: string[]) => void;
  /** New tag input value. */
  newTag: string;
  /** Setter for new tag input. */
  setNewTag: (tag: string) => void;
  /** Add the current newTag to nodeTags. */
  handleAddTag: () => void;
  /** Remove a tag by value. */
  handleDeleteTag: (tag: string) => void;
  /** Whether the editor has unsaved changes. */
  hasChanges: boolean;
  /** Mark changes as saved. */
  clearChanges: () => void;
  /** Mark that a change was made. */
  markChanged: () => void;
  /** Loaded template for the current node, or null. */
  template: Template | null;
  /** Whether the template is currently loading. */
  loadingTemplate: boolean;
}

/**
 * Hook that manages all editable state for a selected graph node.
 *
 * Resets local state whenever `selectedNodeData` changes, loads the
 * template for template-driven property fields, and tracks whether
 * unsaved changes exist.
 *
 * @param selectedNodeData - The currently selected node's data, or null.
 */
export function useNodePropertyState(
  selectedNodeData: GraphNodeData | null,
): UseNodePropertyStateReturn {
  const [nodeTitle, setNodeTitle] = useState('');
  const [nodeProperties, setNodeProperties] = useState<Record<string, unknown>>({});
  const [nodeTags, setNodeTags] = useState<string[]>([]);
  const [newTag, setNewTag] = useState('');
  const [hasChanges, setHasChanges] = useState(false);

  // Template (for property field definitions) and full node data (canvas
  // payload is minimal) are now server state via TanStack Query. Both stay
  // disabled until the relevant id is present.
  const templateId = selectedNodeData?.templateId;
  const nodeId = selectedNodeData?.nodeId;
  const { data: templateData, isFetching: loadingTemplate } = useTemplate(templateId);
  const { data: fullNode } = useNode(nodeId);
  const template: Template | null = templateData ?? null;

  // Reset the editor to the (minimal) canvas data whenever the selected node
  // changes. Rich properties/tags from the full-node query are layered on by
  // the effect below once they resolve.
  useEffect(() => {
    if (selectedNodeData) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setNodeTitle(selectedNodeData.title || '');
      setNodeProperties(selectedNodeData.content || {});
      setNodeTags(selectedNodeData.tags || []);
      setHasChanges(false);
    }
  }, [selectedNodeData]);

  // When the full node resolves for the current selection, overlay its richer
  // properties/tags onto the editor (the canvas payload only carries minimal
  // data). Guarded on the resolved node's id so a stale cached node from the
  // previous selection never bleeds into the new one. Mirrors the legacy
  // fetch-then-setState behaviour.
  useEffect(() => {
    if (!fullNode || fullNode.id !== nodeId) return;
    if (fullNode.properties && Object.keys(fullNode.properties).length > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setNodeProperties(fullNode.properties);
    }
    if (fullNode.tags && fullNode.tags.length > 0) {
      setNodeTags(fullNode.tags);
    }
  }, [fullNode, nodeId]);

  const handlePropertyChange = useCallback((propName: string, value: unknown) => {
    setNodeProperties(prev => ({ ...prev, [propName]: value }));
    setHasChanges(true);
  }, []);

  const handleAddTag = useCallback(() => {
    if (newTag.trim() && !nodeTags.includes(newTag.trim())) {
      setNodeTags(prev => [...prev, newTag.trim()]);
      setNewTag('');
      setHasChanges(true);
    }
  }, [newTag, nodeTags]);

  const handleDeleteTag = useCallback((tagToDelete: string) => {
    setNodeTags(prev => prev.filter(tag => tag !== tagToDelete));
    setHasChanges(true);
  }, []);

  const clearChanges = useCallback(() => setHasChanges(false), []);
  const markChanged = useCallback(() => setHasChanges(true), []);

  return {
    nodeTitle,
    setNodeTitle,
    nodeProperties,
    handlePropertyChange,
    nodeTags,
    setNodeTags,
    newTag,
    setNewTag,
    handleAddTag,
    handleDeleteTag,
    hasChanges,
    clearChanges,
    markChanged,
    template,
    loadingTemplate,
  };
}
