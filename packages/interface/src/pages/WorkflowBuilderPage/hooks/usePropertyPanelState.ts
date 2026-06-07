// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * usePropertyPanelState: Local state management for the properties panel
 *
 * Encapsulates localData, dirty tracking, JSON editor toggle, and filter rules
 * state. Syncs local state when the selected node changes and provides handlers
 * for field changes, filter changes, and applying updates.
 */

import { useState, useEffect, useMemo, useCallback } from 'react';
import type { Node } from '@xyflow/react';
import type {
  WorkflowStepNodeData,
  TriggerNodeData,
  EventTriggerNodeData,
  ConditionalNodeData,
} from '../types';
import { jsonToCondition } from '../components/forms/conditionTypes';
import { filtersToJson, jsonToFilters } from '../components/forms/filterTypes';

/** Return type of the usePropertyPanelState hook. */
interface PropertyPanelState {
  /** Local copy of the selected node's data, edited in-place. */
  localData: Record<string, unknown>;
  /** Whether local edits differ from the persisted node data. */
  isDirty: boolean;
  /** Whether the JSON editor is shown instead of the visual form. */
  showJsonEditor: boolean;
  /** Toggle between JSON and visual editor. */
  toggleJsonEditor: () => void;
  /** Local filter rules (separate state to preserve incomplete filters). */
  localFilterRules: ReturnType<typeof jsonToFilters>;
  /** Parsed condition group for the ConditionBuilder. */
  conditionGroup: ReturnType<typeof jsonToCondition>;
  /** Update a single field in localData. */
  handleChange: (field: string, value: unknown) => void;
  /** Update filter rules (separate from localData). */
  handleFilterChange: (rules: ReturnType<typeof jsonToFilters>) => void;
  /** Persist local changes back to the workflow graph. */
  handleApply: () => void;
  /** Prompt user for a name and save the current node config as a template. */
  handleSaveAsTemplate: () => void;
}

/**
 * Manages local editing state for the properties panel.
 *
 * When a different node is selected the hook seeds local state from the node's
 * data.  All edits stay local until `handleApply` is called, which merges the
 * local data (including filter rules) and pushes it upstream.
 */
export function usePropertyPanelState(
  selectedNode: Node | null,
  onNodeUpdate: (nodeId: string, data: Partial<WorkflowStepNodeData>) => void,
  onSaveAsTemplate?: (name: string, nodeData: WorkflowStepNodeData) => void,
): PropertyPanelState {
  const [localData, setLocalData] = useState<Record<string, unknown>>({});
  const [isDirty, setIsDirty] = useState(false);
  const [showJsonEditor, setShowJsonEditor] = useState(false);
  // Separate state for filter rules to preserve incomplete filters during editing
  const [localFilterRules, setLocalFilterRules] = useState<ReturnType<typeof jsonToFilters>>([]);

  // Sync local state with selected node. Intentional setState-in-effect:
  // when a different node is selected, we need to seed the local form
  // state from the new node's data.
  useEffect(() => {
    if (selectedNode) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLocalData(selectedNode.data || {});
      // Initialize filter rules from the node data
      const nodeData = selectedNode.data as TriggerNodeData | EventTriggerNodeData | undefined;
      const filters = nodeData?.filters as Record<string, unknown> | undefined;
      setLocalFilterRules(jsonToFilters(filters || null));
      setIsDirty(false);
      setShowJsonEditor(false);
    }
  }, [selectedNode]);

  /** Update a single field in local data. */
  const handleChange = useCallback((field: string, value: unknown) => {
    setLocalData((prev) => ({ ...prev, [field]: value }));
    setIsDirty(true);
  }, []);

  /** Update filter rules (kept separate to preserve incomplete filters). */
  const handleFilterChange = useCallback((rules: ReturnType<typeof jsonToFilters>) => {
    setLocalFilterRules(rules);
    setIsDirty(true);
  }, []);

  /** Toggle between JSON and visual editor. */
  const toggleJsonEditor = useCallback(() => {
    setShowJsonEditor((prev) => !prev);
  }, []);

  /** Persist local changes back to the workflow graph. */
  const handleApply = useCallback(() => {
    if (selectedNode) {
      // Merge localData with converted filter rules
      const dataToSave = {
        ...localData,
        // Convert filter rules to JSON format only when saving
        filters: filtersToJson(localFilterRules),
      };
      onNodeUpdate(selectedNode.id, dataToSave as Partial<WorkflowStepNodeData>);
      setIsDirty(false);
    }
  }, [selectedNode, localData, localFilterRules, onNodeUpdate]);

  /** Prompt user for a name and save the current node config as a template. */
  const handleSaveAsTemplate = useCallback(() => {
    if (!selectedNode || !onSaveAsTemplate) return;
    const data = localData as unknown as WorkflowStepNodeData;
    const templateName = prompt('Enter template name:', data.name || 'My Template');
    if (templateName) {
      onSaveAsTemplate(templateName, data);
    }
  }, [selectedNode, localData, onSaveAsTemplate]);

  /** Parse condition for ConditionBuilder. */
  const conditionGroup = useMemo(() => {
    const nodeData = localData as unknown as ConditionalNodeData;
    const condition = nodeData.condition as unknown as Record<string, unknown>;
    return jsonToCondition(condition || null);
  }, [localData]);

  return {
    localData,
    isDirty,
    showJsonEditor,
    toggleJsonEditor,
    localFilterRules,
    conditionGroup,
    handleChange,
    handleFilterChange,
    handleApply,
    handleSaveAsTemplate,
  };
}
