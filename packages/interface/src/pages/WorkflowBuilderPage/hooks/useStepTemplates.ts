// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useStepTemplates: Hook for managing saved step templates
 *
 * Provides CRUD operations for step templates stored in localStorage.
 * Templates can be reused to quickly add pre-configured steps.
 */

import { useState, useEffect, useCallback } from 'react';
import type { StepTemplate, WorkflowStepNodeData } from '../types';
import { logger } from '../../../utils/logger';

const STORAGE_KEY = 'workflow-step-templates';

interface UseStepTemplatesResult {
  templates: StepTemplate[];
  loading: boolean;
  error: string | null;

  // CRUD operations
  saveTemplate: (name: string, data: WorkflowStepNodeData) => StepTemplate;
  deleteTemplate: (templateId: string) => void;
  getTemplate: (templateId: string) => StepTemplate | undefined;

  // Import/Export
  exportTemplates: () => string;
  importTemplates: (json: string) => { success: number; failed: number };

  // Refresh
  refresh: () => void;
}

export function useStepTemplates(): UseStepTemplatesResult {
  const [templates, setTemplates] = useState<StepTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load templates from localStorage
  const loadTemplates = useCallback(() => {
    setLoading(true);
    setError(null);

    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed)) {
          setTemplates(parsed);
        }
      }
    } catch (err) {
      logger.error('Failed to load templates:', err);
      setError('Failed to load templates');
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  // Save templates to localStorage
  const persistTemplates = useCallback((newTemplates: StepTemplate[]) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(newTemplates));
      setTemplates(newTemplates);
    } catch (err) {
      logger.error('Failed to save templates:', err);
      setError('Failed to save templates');
    }
  }, []);

  // Save a new template
  const saveTemplate = useCallback(
    (name: string, data: WorkflowStepNodeData): StepTemplate => {
      const template: StepTemplate = {
        id: `template-${Date.now()}`,
        name,
        description: data.description,
        category: data.toolCategory,
        toolType: data.toolType,
        toolId: data.toolId,
        configuration: data.configuration,
        createdAt: new Date().toISOString(),
      };

      const newTemplates = [...templates, template];
      persistTemplates(newTemplates);

      return template;
    },
    [templates, persistTemplates]
  );

  // Delete a template
  const deleteTemplate = useCallback(
    (templateId: string) => {
      const newTemplates = templates.filter((t) => t.id !== templateId);
      persistTemplates(newTemplates);
    },
    [templates, persistTemplates]
  );

  // Get a single template by ID
  const getTemplate = useCallback(
    (templateId: string): StepTemplate | undefined => {
      return templates.find((t) => t.id === templateId);
    },
    [templates]
  );

  // Export templates as JSON
  const exportTemplates = useCallback((): string => {
    return JSON.stringify(templates, null, 2);
  }, [templates]);

  // Import templates from JSON
  const importTemplates = useCallback(
    (json: string): { success: number; failed: number } => {
      let success = 0;
      let failed = 0;

      try {
        const parsed = JSON.parse(json);
        if (!Array.isArray(parsed)) {
          throw new Error('Invalid format: expected an array');
        }

        const validTemplates: StepTemplate[] = [];

        for (const item of parsed) {
          // Validate required fields
          if (
            typeof item.name === 'string' &&
            typeof item.toolType === 'string' &&
            typeof item.toolId === 'string'
          ) {
            validTemplates.push({
              id: item.id || `template-${Date.now()}-${Math.random().toString(36).slice(2)}`,
              name: item.name,
              description: item.description,
              category: item.category || 'other',
              toolType: item.toolType,
              toolId: item.toolId,
              configuration: item.configuration || {},
              createdAt: item.createdAt || new Date().toISOString(),
            });
            success++;
          } else {
            failed++;
          }
        }

        // Merge with existing templates (avoid duplicates by ID)
        const existingIds = new Set(templates.map((t) => t.id));
        const newTemplates = [
          ...templates,
          ...validTemplates.filter((t) => !existingIds.has(t.id)),
        ];

        persistTemplates(newTemplates);
      } catch (err) {
        logger.error('Failed to import templates:', err);
        setError('Failed to import templates: invalid JSON');
      }

      return { success, failed };
    },
    [templates, persistTemplates]
  );

  return {
    templates,
    loading,
    error,
    saveTemplate,
    deleteTemplate,
    getTemplate,
    exportTemplates,
    importTemplates,
    refresh: loadTemplates,
  };
}
