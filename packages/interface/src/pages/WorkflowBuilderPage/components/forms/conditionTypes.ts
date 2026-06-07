// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Condition types and JSON converters for the workflow ConditionBuilder.
 *
 * Lives in its own file so ConditionBuilder.tsx is Fast-Refresh-clean.
 */

/**
 * Single condition rule
 */
export interface ConditionRule {
  id: string;
  field: string;
  operator: ConditionOperator;
  value: string;
  valueType: 'static' | 'reference';
}

/**
 * Condition group (AND/OR)
 */
export interface ConditionGroup {
  logic: 'AND' | 'OR';
  rules: ConditionRule[];
}

/**
 * Available operators
 */
export type ConditionOperator =
  | 'equals'
  | 'not_equals'
  | 'contains'
  | 'not_contains'
  | 'starts_with'
  | 'ends_with'
  | 'greater_than'
  | 'less_than'
  | 'greater_or_equal'
  | 'less_or_equal'
  | 'is_empty'
  | 'is_not_empty'
  | 'matches_regex';

/**
 * Generate unique ID for a new rule.
 */
function generateId(): string {
  return `rule-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Convert ConditionGroup to backend JSON format
 */
export function conditionToJson(condition: ConditionGroup): Record<string, unknown> {
  if (condition.rules.length === 0) {
    return {};
  }

  if (condition.rules.length === 1) {
    const rule = condition.rules[0];
    return {
      field: rule.field,
      operator: rule.operator,
      value: rule.value,
    };
  }

  return {
    [condition.logic.toLowerCase()]: condition.rules.map((rule) => ({
      field: rule.field,
      operator: rule.operator,
      value: rule.value,
    })),
  };
}

/**
 * Convert backend JSON to ConditionGroup
 */
export function jsonToCondition(json: Record<string, unknown> | null): ConditionGroup {
  if (!json || Object.keys(json).length === 0) {
    return { logic: 'AND', rules: [] };
  }

  // Check for AND/OR groups
  if ('and' in json && Array.isArray(json.and)) {
    return {
      logic: 'AND',
      rules: (json.and as Record<string, unknown>[]).map((r) => ({
        id: generateId(),
        field: (r.field as string) || '',
        operator: (r.operator as ConditionOperator) || 'equals',
        value: (r.value as string) || '',
        valueType: 'static' as const,
      })),
    };
  }

  if ('or' in json && Array.isArray(json.or)) {
    return {
      logic: 'OR',
      rules: (json.or as Record<string, unknown>[]).map((r) => ({
        id: generateId(),
        field: (r.field as string) || '',
        operator: (r.operator as ConditionOperator) || 'equals',
        value: (r.value as string) || '',
        valueType: 'static' as const,
      })),
    };
  }

  // Single rule
  if ('field' in json && 'operator' in json) {
    return {
      logic: 'AND',
      rules: [
        {
          id: generateId(),
          field: (json.field as string) || '',
          operator: (json.operator as ConditionOperator) || 'equals',
          value: (json.value as string) || '',
          valueType: 'static' as const,
        },
      ],
    };
  }

  return { logic: 'AND', rules: [] };
}
