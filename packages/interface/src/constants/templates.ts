// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * System template constants and helper functions.
 * Centralizes hardcoded template IDs used throughout the app.
 *
 * This eliminates duplicate hardcoded strings like 'system_workflow',
 * 'system_workflow_step', 'system_lens' scattered across components.
 */

/**
 * System template IDs (infrastructure, not user knowledge)
 */
const SYSTEM_TEMPLATES = {
  WORKFLOW: 'system_workflow',
  WORKFLOW_STEP: 'system_workflow_step',
  LENS: 'system_lens',
} as const;

/**
 * Array of all system template IDs for easy checking
 */
const SYSTEM_TEMPLATE_IDS = Object.values(SYSTEM_TEMPLATES) as string[];

/**
 * Check if a template ID represents a system template
 * @param templateId Template ID to check
 * @returns true if it's a system infrastructure template
 */
export const isSystemTemplate = (templateId: string): boolean => {
  return SYSTEM_TEMPLATE_IDS.includes(templateId);
};

/**
 * Check if a node is a knowledge node (not system infrastructure)
 * @param templateId Template ID to check
 * @returns true if it's a knowledge node
 */
const isKnowledgeNode = (templateId: string): boolean => {
  return !isSystemTemplate(templateId);
};

/**
 * Filter nodes to only knowledge nodes (exclude system infrastructure)
 * @param nodes Array of nodes with template_id field
 * @returns Filtered array of knowledge nodes only
 */
export const filterKnowledgeNodes = <T extends { template_id: string }>(
  nodes: T[]
): T[] => {
  return nodes.filter((node) => isKnowledgeNode(node.template_id));
};

/**
 * Filter templates to exclude system templates
 * @param templates Array of templates with id field
 * @returns Filtered array excluding system templates
 */
export const filterNonSystemTemplates = <T extends { id: string }>(
  templates: T[]
): T[] => {
  return templates.filter((template) => !isSystemTemplate(template.id));
};

