// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useToolSchemas: Hook for fetching and caching tool schemas
 *
 * Provides parsed input/output schemas for system tools, with caching
 * to avoid repeated API calls.
 */

import { useState, useCallback } from 'react';
import type { FieldSchema, DataPort } from '../types';
import { parseToolInputSchema, parseToolOutputSchema, fieldsToDataPorts } from '../utils/schemaParser';
import { toolsApi } from '../../../services/api/tools';
import { logger } from '../../../utils/logger';

interface ToolSchemaCache {
  [toolId: string]: {
    inputSchema: FieldSchema[];
    outputSchema: FieldSchema[];
    rawInputSchema: Record<string, unknown>;
    rawOutputSchema: Record<string, unknown>;
  };
}

interface UseToolSchemasResult {
  /** Get parsed input schema for a tool */
  getInputSchema: (toolId: string) => FieldSchema[];
  /** Get parsed output schema for a tool */
  getOutputSchema: (toolId: string) => FieldSchema[];
  /** Get input ports for a tool node */
  getInputPorts: (nodeId: string, toolId: string) => DataPort[];
  /** Get output ports for a tool node */
  getOutputPorts: (nodeId: string, toolId: string) => DataPort[];
  /** Get raw JSON schema for a tool */
  getRawSchema: (toolId: string) => { input: Record<string, unknown>; output: Record<string, unknown> } | null;
  /** Loading state */
  isLoading: boolean;
  /** Error state */
  error: string | null;
  /** Refresh schemas for a specific tool */
  refreshTool: (toolId: string) => Promise<void>;
  /** Pre-load schemas for multiple tools */
  preloadTools: (toolIds: string[]) => Promise<void>;
}

/**
 * Hook for managing tool schema loading and caching
 */
export function useToolSchemas(): UseToolSchemasResult {
  const [cache, setCache] = useState<ToolSchemaCache>({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingTools, setLoadingTools] = useState<Set<string>>(new Set());

  /**
   * Fetch and cache schema for a single tool
   */
  const fetchToolSchema = useCallback(async (toolId: string): Promise<void> => {
    // Check if already loading
    if (loadingTools.has(toolId)) {
      return;
    }

    // Check if already cached
    if (cache[toolId]) {
      return;
    }

    setLoadingTools((prev) => new Set(prev).add(toolId));
    setIsLoading(true);
    setError(null);

    try {
      // Fetch the tool details from API
      const tools = await toolsApi.listSystem();
      const tool = tools.find((t) => t.id === toolId);

      if (!tool) {
        throw new Error(`Tool not found: ${toolId}`);
      }

      // Parse schemas
      const inputSchema = parseToolInputSchema(tool.input_schema);
      const outputSchema = parseToolOutputSchema(tool.output_schema);

      // Cache the result
      setCache((prev) => ({
        ...prev,
        [toolId]: {
          inputSchema,
          outputSchema,
          rawInputSchema: tool.input_schema || {},
          rawOutputSchema: tool.output_schema || {},
        },
      }));
    } catch (err) {
      logger.error(`Failed to fetch schema for tool ${toolId}:`, err);
      setError(err instanceof Error ? err.message : 'Failed to fetch tool schema');
    } finally {
      setLoadingTools((prev) => {
        const next = new Set(prev);
        next.delete(toolId);
        return next;
      });
      setIsLoading(false);
    }
  }, [cache, loadingTools]);

  /**
   * Get input schema for a tool (triggers fetch if needed)
   */
  const getInputSchema = useCallback(
    (toolId: string): FieldSchema[] => {
      if (!cache[toolId]) {
        // Trigger fetch in background
        fetchToolSchema(toolId);
        return [];
      }
      return cache[toolId].inputSchema;
    },
    [cache, fetchToolSchema]
  );

  /**
   * Get output schema for a tool (triggers fetch if needed)
   */
  const getOutputSchema = useCallback(
    (toolId: string): FieldSchema[] => {
      if (!cache[toolId]) {
        // Trigger fetch in background
        fetchToolSchema(toolId);
        return [];
      }
      return cache[toolId].outputSchema;
    },
    [cache, fetchToolSchema]
  );

  /**
   * Get input ports for a tool node
   */
  const getInputPorts = useCallback(
    (nodeId: string, toolId: string): DataPort[] => {
      const schema = getInputSchema(toolId);
      return fieldsToDataPorts(nodeId, schema, 'input');
    },
    [getInputSchema]
  );

  /**
   * Get output ports for a tool node
   */
  const getOutputPorts = useCallback(
    (nodeId: string, toolId: string): DataPort[] => {
      const schema = getOutputSchema(toolId);
      return fieldsToDataPorts(nodeId, schema, 'output');
    },
    [getOutputSchema]
  );

  /**
   * Get raw JSON schemas
   */
  const getRawSchema = useCallback(
    (toolId: string): { input: Record<string, unknown>; output: Record<string, unknown> } | null => {
      if (!cache[toolId]) {
        fetchToolSchema(toolId);
        return null;
      }
      return {
        input: cache[toolId].rawInputSchema,
        output: cache[toolId].rawOutputSchema,
      };
    },
    [cache, fetchToolSchema]
  );

  /**
   * Force refresh schema for a tool
   */
  const refreshTool = useCallback(
    async (toolId: string): Promise<void> => {
      // Remove from cache first
      setCache((prev) => {
        const next = { ...prev };
        delete next[toolId];
        return next;
      });
      // Re-fetch
      await fetchToolSchema(toolId);
    },
    [fetchToolSchema]
  );

  /**
   * Pre-load schemas for multiple tools
   */
  const preloadTools = useCallback(
    async (toolIds: string[]): Promise<void> => {
      const uncached = toolIds.filter((id) => !cache[id] && !loadingTools.has(id));
      if (uncached.length === 0) return;

      setIsLoading(true);
      try {
        // Fetch all tools at once
        const tools = await toolsApi.listSystem();

        // Parse and cache each requested tool
        const newCache: ToolSchemaCache = {};
        for (const toolId of uncached) {
          const tool = tools.find((t) => t.id === toolId);
          if (tool) {
            newCache[toolId] = {
              inputSchema: parseToolInputSchema(tool.input_schema),
              outputSchema: parseToolOutputSchema(tool.output_schema),
              rawInputSchema: tool.input_schema || {},
              rawOutputSchema: tool.output_schema || {},
            };
          }
        }

        setCache((prev) => ({ ...prev, ...newCache }));
      } catch (err) {
        logger.error('Failed to preload tool schemas:', err);
        setError(err instanceof Error ? err.message : 'Failed to preload tool schemas');
      } finally {
        setIsLoading(false);
      }
    },
    [cache, loadingTools]
  );

  return {
    getInputSchema,
    getOutputSchema,
    getInputPorts,
    getOutputPorts,
    getRawSchema,
    isLoading,
    error,
    refreshTool,
    preloadTools,
  };
}
