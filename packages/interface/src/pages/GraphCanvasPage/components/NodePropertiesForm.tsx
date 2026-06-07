// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * NodePropertiesForm: Editable form for graph node properties.
 *
 * Renders the title editor with metadata tooltip, template-driven
 * property fields, tags, connected nodes list, provenance section,
 * and save/delete actions.
 */

import React, { useState } from 'react';
import {
  Box,
  Typography,
  TextField,
  Button,
  Divider,
  Alert,
  Chip,
  Stack,
  IconButton,
  Tooltip,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMoreOutlined';
import EditIcon from '@mui/icons-material/EditOutlined';
import CheckIcon from '@mui/icons-material/CheckOutlined';
import DeleteIcon from '@mui/icons-material/DeleteOutlined';
import SaveIcon from '@mui/icons-material/SaveOutlined';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import ContentCopyIcon from '@mui/icons-material/ContentCopyOutlined';
import type Graph from 'graphology';
import type { GraphNodeData, NodeAttributes, EdgeAttributes } from '../types';
import type { SourceGroupState } from '../hooks/useSourceGroups';
import type { Template } from '../../../types';
import { ghostButtonSx } from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';
import { Overlays } from '../../../theme/overlays';
import { SYSTEM_PROPERTY_KEYS } from '../../../utils/propertyKeys';
import PropertyFieldRenderer from './PropertyFieldRenderer';
import ProvenanceSection from './ProvenanceSection';
import ConnectedNodesList from './ConnectedNodesList';

interface NodePropertiesFormProps {
  /** The selected node ID. */
  selectedNodeId: string | null;
  /** The selected node data. */
  selectedNodeData: GraphNodeData;
  /** Current title value. */
  nodeTitle: string;
  /** Title change handler. */
  onTitleChange: (title: string) => void;
  /** Current properties map. */
  nodeProperties: Record<string, unknown>;
  /** Property change handler. */
  onPropertyChange: (propName: string, value: unknown) => void;
  /** Current tags list. */
  nodeTags: string[];
  /** New tag input value. */
  newTag: string;
  /** New tag input change handler. */
  onNewTagChange: (tag: string) => void;
  /** Add the current new tag. */
  onAddTag: () => void;
  /** Delete a tag by value. */
  onDeleteTag: (tag: string) => void;
  /** Whether there are unsaved changes. */
  hasChanges: boolean;
  /** Mark that a change was made (for title edits). */
  onMarkChanged: () => void;
  /** Template for this node type, or null. */
  template: Template | null;
  /** Whether the template is loading. */
  loadingTemplate: boolean;
  /** Save handler. */
  onSave: () => void;
  /** Delete handler. */
  onDelete: () => void;
  /** Get the source group state for provenance display. */
  getNodeSourceGroup?: (nodeId: string) => SourceGroupState | undefined;
  /** Select a node by ID (for provenance and connection clicks). */
  onSelectNode?: (nodeId: string) => void;
  /** The graphology graph instance for reading connections. */
  graph?: Graph<NodeAttributes, EdgeAttributes>;
}

const INITIAL_FACTS_COUNT = 4;

/** Content properties display with inline editing per row. */
const PropertiesFactsList: React.FC<{
  properties: Record<string, unknown>;
  templatePropertyNames: string[];
  onPropertyChange?: (key: string, value: unknown) => void;
  onMarkChanged?: () => void;
}> = ({ properties, templatePropertyNames, onPropertyChange, onMarkChanged }) => {
  const [expanded, setExpanded] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');

  const facts = Object.entries(properties).filter(([key, value]) => {
    if (templatePropertyNames.includes(key)) return false;
    if (SYSTEM_PROPERTY_KEYS.has(key)) return false;
    if (value === null || value === undefined || value === '') return false;
    return true;
  });

  if (facts.length === 0) return null;

  const visibleFacts = expanded ? facts : facts.slice(0, INITIAL_FACTS_COUNT);
  const hasMore = facts.length > INITIAL_FACTS_COUNT;

  const formatValue = (value: unknown): string => {
    if (Array.isArray(value)) return value.join(', ');
    if (typeof value === 'object' && value !== null) return JSON.stringify(value, null, 2);
    return String(value);
  };

  const formatKey = (key: string): string =>
    key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

  const startEditing = (key: string, value: unknown) => {
    setEditingKey(key);
    setEditValue(formatValue(value));
  };

  const commitEdit = (key: string) => {
    if (!onPropertyChange) return;
    try {
      const trimmed = editValue.trim();
      if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
        onPropertyChange(key, JSON.parse(trimmed));
      } else {
        onPropertyChange(key, trimmed);
      }
    } catch {
      onPropertyChange(key, editValue);
    }
    onMarkChanged?.();
    setEditingKey(null);
  };

  return (
    <>
      <Divider sx={{ my: 1.5 }} />
      <Typography variant="subtitle2" gutterBottom>
        Details
      </Typography>
      {visibleFacts.map(([key, value]) => (
        <Box key={key} sx={{ mb: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Typography variant="caption" sx={{ color: 'text.secondary', lineHeight: 1.2 }}>
              {formatKey(key)}
            </Typography>
            {onPropertyChange && (
              <IconButton
                aria-label={editingKey === key ? "Save property" : "Edit property"}
                size="small"
                onClick={() => editingKey === key ? commitEdit(key) : startEditing(key, value)}
                sx={{ p: 0.25, color: 'text.secondary' }}
              >
                {editingKey === key
                  ? <CheckIcon sx={{ fontSize: 14, color: 'success.main' }} />
                  : <EditIcon sx={{ fontSize: 14 }} />
                }
              </IconButton>
            )}
          </Box>
          {editingKey === key ? (
            <TextField
              fullWidth
              size="small"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  commitEdit(key);
                }
                if (e.key === 'Escape') setEditingKey(null);
              }}
              multiline={editValue.includes('\n') || editValue.length > 80}
              rows={editValue.includes('\n') ? 3 : 1}
              autoFocus
              sx={{ mt: 0.5 }}
            />
          ) : (
            <Typography variant="body2" sx={{ lineHeight: 1.3, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {formatValue(value)}
            </Typography>
          )}
        </Box>
      ))}
      {hasMore && (
        <Button
          size="small"
          onClick={() => setExpanded(!expanded)}
          endIcon={<ExpandMoreIcon sx={{
            transform: expanded ? 'rotate(180deg)' : 'none',
            transition: 'transform 0.2s',
          }} />}
          sx={{
            width: '100%',
            color: 'text.secondary',
            fontSize: '0.75rem',
            '&:hover': { bgcolor: Overlays.subtle.dark },
          }}
        >
          {expanded ? 'Show less' : `Show all ${facts.length} properties`}
        </Button>
      )}
    </>
  );
};

/**
 * Renders the full editing form for a selected graph node.
 */
const NodePropertiesForm: React.FC<NodePropertiesFormProps> = ({
  selectedNodeId,
  selectedNodeData,
  nodeTitle,
  onTitleChange,
  nodeProperties,
  onPropertyChange,
  nodeTags,
  newTag,
  onNewTagChange,
  onAddTag,
  onDeleteTag,
  hasChanges,
  onMarkChanged,
  template,
  loadingTemplate,
  onSave,
  onDelete,
  getNodeSourceGroup,
  onSelectNode,
  graph,
}) => {
  const sourceGroup = selectedNodeId && getNodeSourceGroup
    ? getNodeSourceGroup(selectedNodeId)
    : undefined;

  const handleCopyId = () => {
    if (selectedNodeId) navigator.clipboard.writeText(selectedNodeId);
  };

  // Metadata tooltip content
  const metadataContent = (
    <Box sx={{ p: 0.5, maxWidth: 320 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
        <Typography variant="caption" sx={{ fontFamily: 'monospace', fontSize: '0.7rem', wordBreak: 'break-all' }}>
          {selectedNodeId}
        </Typography>
        <IconButton aria-label="Copy" size="small" onClick={handleCopyId} sx={{ p: 0.25 }}>
          <ContentCopyIcon sx={{ fontSize: 12 }} />
        </IconButton>
      </Box>
      <Typography variant="caption" sx={{ display: 'block' }}>
        Template: {template?.name || selectedNodeData.templateId?.split('/').pop() || 'None'}
      </Typography>
      {selectedNodeData.createdAt && (
        <Typography variant="caption" sx={{ display: 'block' }}>
          Created: {new Date(selectedNodeData.createdAt).toLocaleString()}
        </Typography>
      )}
      {selectedNodeData.updatedAt && (
        <Typography variant="caption" sx={{ display: 'block' }}>
          Updated: {new Date(selectedNodeData.updatedAt).toLocaleString()}
        </Typography>
      )}
    </Box>
  );

  return (
    <>
      {/* Header: Title + Info */}
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1, mb: 2 }}>
        <TextField
          label="Title"
          fullWidth
          value={nodeTitle}
          onChange={(e) => {
            onTitleChange(e.target.value);
            onMarkChanged();
          }}
          size="small"
        />
        <Tooltip title={metadataContent} arrow placement="left" enterDelay={200}>
          <IconButton aria-label="Show node info" size="small" sx={{ mt: 0.5, color: 'text.secondary' }}>
            <InfoOutlinedIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>

      {/* Properties — shows template-driven fields for editing,
          then read-only display of all content properties (the "facts") */}
      {loadingTemplate ? (
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Loading...
        </Typography>
      ) : template && template.properties.length > 0 ? (
        <>
          <Typography variant="subtitle2" gutterBottom sx={{ mt: 1 }}>
            Edit Properties
          </Typography>
          {template.properties.map(propDef => (
            <PropertyFieldRenderer
              key={propDef.name}
              propDef={propDef}
              value={nodeProperties[propDef.name]}
              onChange={onPropertyChange}
            />
          ))}
        </>
      ) : null}

      {/* Content Properties (facts) — read-only display with show more */}
      <PropertiesFactsList
        properties={nodeProperties}
        templatePropertyNames={template?.properties.map(p => p.name) || []}
        onPropertyChange={onPropertyChange}
        onMarkChanged={onMarkChanged}
      />

      {/* Tags */}
      <Divider sx={{ my: 1.5 }} />
      <Typography variant="subtitle2" gutterBottom>
        Tags
      </Typography>
      {nodeTags.length > 0 && (
        <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', mb: 1 }}>
          {nodeTags.map((tag, index) => (
            <Chip
              key={index}
              label={tag}
              onDelete={() => onDeleteTag(tag)}
              size="small"
              sx={{ mb: 0.5 }}
            />
          ))}
        </Stack>
      )}
      <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
        <TextField
          size="small"
          label="Add tag"
          value={newTag}
          onChange={(e) => onNewTagChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              onAddTag();
            }
          }}
          sx={{ flex: 1 }}
        />
        <Button variant="outlined" size="small" onClick={onAddTag}>
          Add
        </Button>
      </Box>

      {/* Connected Nodes */}
      {graph && selectedNodeId && (
        <>
          <Divider sx={{ my: 1.5 }} />
          <Typography variant="subtitle2" gutterBottom>
            Connections
          </Typography>
          <ConnectedNodesList
            graph={graph}
            nodeId={selectedNodeId}
            onSelectNode={(id) => onSelectNode?.(id)}
          />
        </>
      )}

      {/* Provenance */}
      {sourceGroup && (
        <ProvenanceSection
          sourceGroup={sourceGroup}
          onSelectNode={onSelectNode}
        />
      )}

      {/* Actions */}
      <Divider sx={{ my: 1.5 }} />
      <Box sx={{ display: 'flex', gap: 1, justifyContent: 'space-between' }}>
        <Button
          variant="outlined"
          size="small"
          sx={ghostButtonSx(ChaosCypherPalette.primary)}
          startIcon={<SaveIcon />}
          onClick={onSave}
          disabled={!hasChanges}
        >
          Save
        </Button>
        <Button
          variant="outlined"
          size="small"
          sx={ghostButtonSx(ChaosCypherPalette.error)}
          startIcon={<DeleteIcon />}
          onClick={onDelete}
        >
          Delete
        </Button>
      </Box>
      {hasChanges && (
        <Alert severity="info" sx={{ mt: 1.5 }}>
          You have unsaved changes
        </Alert>
      )}
    </>
  );
};

export default NodePropertiesForm;
