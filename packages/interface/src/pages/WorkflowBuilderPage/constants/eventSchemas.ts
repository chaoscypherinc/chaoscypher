// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Event Schemas for Workflow Triggers
 *
 * Defines the payload structure for each event source that can trigger
 * a workflow. These schemas describe what data is available when an
 * event fires.
 */

import type { FieldSchema } from '../types/dataflow';

/**
 * Event source identifiers matching backend EventSource enum
 */
export type EventSource =
  | 'manual'
  | 'node.create'
  | 'node.update'
  | 'node.delete'
  | 'edge.create'
  | 'edge.update'
  | 'edge.delete'
  | 'file.upload'
  | 'file.indexed'
  | 'import.complete'
  | 'custom';

/**
 * Display information for event sources
 */
interface EventSourceInfo {
  /** Event source identifier */
  id: EventSource;
  /** Human-readable label */
  label: string;
  /** Description of when this event fires */
  description: string;
  /** Icon name for display */
  icon?: string;
  /** Category for grouping */
  category: 'manual' | 'graph' | 'file' | 'custom';
}

/**
 * Event source display information
 */
export const EVENT_SOURCE_INFO: Record<EventSource, EventSourceInfo> = {
  manual: {
    id: 'manual',
    label: 'Manual Trigger',
    description: 'Workflow is triggered manually by user or API call',
    icon: 'PlayArrow',
    category: 'manual',
  },
  'node.create': {
    id: 'node.create',
    label: 'Node Created',
    description: 'Fires when a new node is created in the knowledge graph',
    icon: 'AddCircle',
    category: 'graph',
  },
  'node.update': {
    id: 'node.update',
    label: 'Node Updated',
    description: 'Fires when an existing node is modified',
    icon: 'Edit',
    category: 'graph',
  },
  'node.delete': {
    id: 'node.delete',
    label: 'Node Deleted',
    description: 'Fires when a node is removed from the graph',
    icon: 'Delete',
    category: 'graph',
  },
  'edge.create': {
    id: 'edge.create',
    label: 'Edge Created',
    description: 'Fires when a new relationship is created between nodes',
    icon: 'Link',
    category: 'graph',
  },
  'edge.update': {
    id: 'edge.update',
    label: 'Edge Updated',
    description: 'Fires when a relationship is modified',
    icon: 'EditRoad',
    category: 'graph',
  },
  'edge.delete': {
    id: 'edge.delete',
    label: 'Edge Deleted',
    description: 'Fires when a relationship is removed',
    icon: 'LinkOff',
    category: 'graph',
  },
  'file.upload': {
    id: 'file.upload',
    label: 'File Uploaded',
    description: 'Fires when a new file is uploaded for processing',
    icon: 'CloudUpload',
    category: 'file',
  },
  'file.indexed': {
    id: 'file.indexed',
    label: 'File Indexed',
    description: 'Fires when a file has been chunked and indexed for RAG',
    icon: 'Search',
    category: 'file',
  },
  'import.complete': {
    id: 'import.complete',
    label: 'Import Complete',
    description: 'Fires when document entity extraction is finished',
    icon: 'CheckCircle',
    category: 'file',
  },
  custom: {
    id: 'custom',
    label: 'Custom Event',
    description: 'Custom event source for webhooks or external triggers',
    icon: 'Code',
    category: 'custom',
  },
};

/**
 * Payload schemas for each event source
 * These define what data fields are available when the event fires
 */
export const EVENT_SCHEMAS: Record<EventSource, FieldSchema[]> = {
  // Manual trigger - no automatic payload, uses workflow input_schema
  manual: [],

  // Node created event
  'node.create': [
    {
      name: 'node_id',
      type: 'string',
      required: true,
      description: 'Unique identifier of the created node',
    },
    {
      name: 'node_type',
      type: 'string',
      required: true,
      description: 'Type/class of the node (e.g., Person, Concept, Document)',
    },
    {
      name: 'node_label',
      type: 'string',
      required: false,
      description: 'Human-readable label of the node',
    },
    {
      name: 'properties',
      type: 'object',
      required: false,
      description: 'All properties/attributes of the node',
    },
    {
      name: 'database_name',
      type: 'string',
      required: true,
      description: 'Name of the database where the node was created',
    },
    {
      name: 'created_at',
      type: 'string',
      required: true,
      description: 'ISO timestamp when the node was created',
    },
  ],

  // Node updated event
  'node.update': [
    {
      name: 'node_id',
      type: 'string',
      required: true,
      description: 'Unique identifier of the updated node',
    },
    {
      name: 'node_type',
      type: 'string',
      required: true,
      description: 'Type/class of the node',
    },
    {
      name: 'node_label',
      type: 'string',
      required: false,
      description: 'Current label of the node',
    },
    {
      name: 'changed_fields',
      type: 'array',
      required: true,
      description: 'List of field names that were modified',
      itemType: 'string',
    },
    {
      name: 'old_values',
      type: 'object',
      required: false,
      description: 'Previous values of changed fields',
    },
    {
      name: 'new_values',
      type: 'object',
      required: false,
      description: 'New values of changed fields',
    },
    {
      name: 'properties',
      type: 'object',
      required: false,
      description: 'All current properties of the node',
    },
    {
      name: 'updated_at',
      type: 'string',
      required: true,
      description: 'ISO timestamp when the node was updated',
    },
  ],

  // Node deleted event
  'node.delete': [
    {
      name: 'node_id',
      type: 'string',
      required: true,
      description: 'Unique identifier of the deleted node',
    },
    {
      name: 'node_type',
      type: 'string',
      required: true,
      description: 'Type/class of the deleted node',
    },
    {
      name: 'node_label',
      type: 'string',
      required: false,
      description: 'Label of the deleted node',
    },
    {
      name: 'deleted_at',
      type: 'string',
      required: true,
      description: 'ISO timestamp when the node was deleted',
    },
  ],

  // Edge created event
  'edge.create': [
    {
      name: 'edge_id',
      type: 'string',
      required: true,
      description: 'Unique identifier of the created edge',
    },
    {
      name: 'edge_type',
      type: 'string',
      required: true,
      description: 'Type/label of the relationship',
    },
    {
      name: 'source_id',
      type: 'string',
      required: true,
      description: 'ID of the source node',
    },
    {
      name: 'target_id',
      type: 'string',
      required: true,
      description: 'ID of the target node',
    },
    {
      name: 'properties',
      type: 'object',
      required: false,
      description: 'Properties of the relationship',
    },
    {
      name: 'created_at',
      type: 'string',
      required: true,
      description: 'ISO timestamp when the edge was created',
    },
  ],

  // Edge updated event
  'edge.update': [
    {
      name: 'edge_id',
      type: 'string',
      required: true,
      description: 'Unique identifier of the updated edge',
    },
    {
      name: 'edge_type',
      type: 'string',
      required: true,
      description: 'Type/label of the relationship',
    },
    {
      name: 'source_id',
      type: 'string',
      required: true,
      description: 'ID of the source node',
    },
    {
      name: 'target_id',
      type: 'string',
      required: true,
      description: 'ID of the target node',
    },
    {
      name: 'changed_fields',
      type: 'array',
      required: true,
      description: 'List of field names that were modified',
      itemType: 'string',
    },
    {
      name: 'old_values',
      type: 'object',
      required: false,
      description: 'Previous values of changed fields',
    },
    {
      name: 'new_values',
      type: 'object',
      required: false,
      description: 'New values of changed fields',
    },
    {
      name: 'updated_at',
      type: 'string',
      required: true,
      description: 'ISO timestamp when the edge was updated',
    },
  ],

  // Edge deleted event
  'edge.delete': [
    {
      name: 'edge_id',
      type: 'string',
      required: true,
      description: 'Unique identifier of the deleted edge',
    },
    {
      name: 'edge_type',
      type: 'string',
      required: true,
      description: 'Type/label of the relationship',
    },
    {
      name: 'source_id',
      type: 'string',
      required: true,
      description: 'ID of the source node',
    },
    {
      name: 'target_id',
      type: 'string',
      required: true,
      description: 'ID of the target node',
    },
    {
      name: 'deleted_at',
      type: 'string',
      required: true,
      description: 'ISO timestamp when the edge was deleted',
    },
  ],

  // File uploaded event
  'file.upload': [
    {
      name: 'file_id',
      type: 'string',
      required: true,
      description: 'Unique identifier of the uploaded file',
    },
    {
      name: 'filename',
      type: 'string',
      required: true,
      description: 'Original filename',
    },
    {
      name: 'mime_type',
      type: 'string',
      required: true,
      description: 'MIME type of the file (e.g., application/pdf)',
    },
    {
      name: 'size_bytes',
      type: 'number',
      required: true,
      description: 'File size in bytes',
    },
    {
      name: 'source_id',
      type: 'string',
      required: false,
      description: 'Source record ID if associated with a source',
    },
    {
      name: 'uploaded_at',
      type: 'string',
      required: true,
      description: 'ISO timestamp when the file was uploaded',
    },
  ],

  // File indexed event (RAG indexing complete)
  'file.indexed': [
    {
      name: 'file_id',
      type: 'string',
      required: true,
      description: 'Unique identifier of the indexed file',
    },
    {
      name: 'filename',
      type: 'string',
      required: true,
      description: 'Original filename',
    },
    {
      name: 'chunk_count',
      type: 'number',
      required: true,
      description: 'Number of text chunks created',
    },
    {
      name: 'total_tokens',
      type: 'number',
      required: false,
      description: 'Total token count across all chunks',
    },
    {
      name: 'index_status',
      type: 'string',
      required: true,
      description: 'Indexing status (success, partial, failed)',
    },
    {
      name: 'indexed_at',
      type: 'string',
      required: true,
      description: 'ISO timestamp when indexing completed',
    },
  ],

  // Import complete event (entity extraction finished)
  'import.complete': [
    {
      name: 'file_id',
      type: 'string',
      required: true,
      description: 'Unique identifier of the imported file',
    },
    {
      name: 'filename',
      type: 'string',
      required: true,
      description: 'Original filename',
    },
    {
      name: 'entities_extracted',
      type: 'number',
      required: true,
      description: 'Number of entities extracted from the document',
    },
    {
      name: 'relationships_extracted',
      type: 'number',
      required: true,
      description: 'Number of relationships extracted',
    },
    {
      name: 'entity_types',
      type: 'array',
      required: false,
      description: 'List of entity types found',
      itemType: 'string',
    },
    {
      name: 'import_status',
      type: 'string',
      required: true,
      description: 'Import status (success, partial, failed)',
    },
    {
      name: 'completed_at',
      type: 'string',
      required: true,
      description: 'ISO timestamp when import completed',
    },
  ],

  // Custom event (webhook/external)
  custom: [
    {
      name: 'event_type',
      type: 'string',
      required: true,
      description: 'Custom event type identifier',
    },
    {
      name: 'payload',
      type: 'object',
      required: false,
      description: 'Custom event payload data',
    },
    {
      name: 'source',
      type: 'string',
      required: false,
      description: 'Source of the custom event',
    },
    {
      name: 'timestamp',
      type: 'string',
      required: true,
      description: 'ISO timestamp when the event occurred',
    },
  ],
};

/**
 * Get all event sources grouped by category
 */
export function getEventSourcesByCategory(): Record<string, EventSourceInfo[]> {
  const grouped: Record<string, EventSourceInfo[]> = {
    manual: [],
    graph: [],
    file: [],
    custom: [],
  };

  for (const info of Object.values(EVENT_SOURCE_INFO)) {
    grouped[info.category].push(info);
  }

  return grouped;
}

