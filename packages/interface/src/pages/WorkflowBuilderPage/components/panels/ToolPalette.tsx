// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ToolPalette: Left sidebar component for dragging tools onto the canvas
 *
 * Displays available system tools organized by category with drag-and-drop
 * functionality. Supports search and filtering.
 */

import React, { useMemo, useState, useCallback, DragEvent } from 'react';
import {
  Box,
  Typography,
  TextField,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  IconButton,
  Tooltip,
  CircularProgress,
  Alert,
  InputAdornment,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import SearchIcon from '@mui/icons-material/Search';
import CloseIcon from '@mui/icons-material/Close';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import CodeIcon from '@mui/icons-material/Code';
import StorageIcon from '@mui/icons-material/Storage';
import HttpIcon from '@mui/icons-material/Http';
import BuildIcon from '@mui/icons-material/Build';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import BoltIcon from '@mui/icons-material/Bolt';
import { useSystemTools } from '../../../../services/api/useTools';
import type { SystemTool } from '../../types';
import { getEventSourcesByCategory } from '../../constants/eventSchemas';
import { CategoryColors } from '../../../../theme/colors';
import { CardColors } from '../../../../theme/cardStyles';
import { ghostInputSx } from '../../../../theme/ghostStyles';
import { ToolCategoryAccordion } from './ToolCategoryAccordion';

// Category definitions
const CATEGORY_CONFIG: Record<string, { name: string; icon: React.ElementType; color: string }> = {
  ai: { name: 'AI Tools', icon: SmartToyIcon, color: CategoryColors.ai },
  graph: { name: 'Graph Operations', icon: AccountTreeIcon, color: CategoryColors.graph },
  logic: { name: 'Logic & Control', icon: CodeIcon, color: CategoryColors.logic },
  data: { name: 'Data Processing', icon: StorageIcon, color: CategoryColors.data },
  http: { name: 'HTTP & APIs', icon: HttpIcon, color: CategoryColors.http },
  external: { name: 'External Services', icon: HttpIcon, color: CategoryColors.external },
  templates: { name: 'Templates', icon: BuildIcon, color: CategoryColors.templates },
  template: { name: 'Templates', icon: BuildIcon, color: CategoryColors.template },
};

/**
 * Format category name for display (Title Case)
 */
function formatCategoryName(category: string): string {
  return category
    .split(/[-_\s]+/)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

interface ToolPaletteProps {
  onClose: () => void;
}

export const ToolPalette: React.FC<ToolPaletteProps> = ({ onClose }) => {
  const [searchQuery, setSearchQuery] = useState('');
  // Only triggers expanded by default, tool categories collapsed
  const [expandedCategories, setExpandedCategories] = useState<string[]>([]);
  const [triggersExpanded, setTriggersExpanded] = useState(true);

  const { data, isLoading: loading, isError } = useSystemTools();

  // On load failure fall back to sample tools (development affordance) and
  // surface a warning, mirroring the pre-TanStack behaviour.
  const error = isError ? 'Failed to load tools' : null;
  const tools = useMemo<SystemTool[]>(
    () => (isError ? getSampleTools() : (data ?? [])),
    [isError, data],
  );

  // Group tools by category
  const toolsByCategory = tools.reduce<Record<string, SystemTool[]>>((acc, tool) => {
    const category = tool.category || 'other';
    if (!acc[category]) {
      acc[category] = [];
    }
    acc[category].push(tool);
    return acc;
  }, {});

  // Filter tools by search query
  const filteredToolsByCategory = Object.entries(toolsByCategory).reduce<Record<string, SystemTool[]>>(
    (acc, [category, categoryTools]) => {
      const filtered = categoryTools.filter(
        (tool) =>
          tool.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          tool.description.toLowerCase().includes(searchQuery.toLowerCase())
      );
      if (filtered.length > 0) {
        acc[category] = filtered;
      }
      return acc;
    },
    {}
  );

  // Handle accordion expansion
  const handleAccordionChange = useCallback(
    (category: string) => (_event: React.SyntheticEvent, isExpanded: boolean) => {
      setExpandedCategories((prev) =>
        isExpanded ? [...prev, category] : prev.filter((c) => c !== category)
      );
    },
    []
  );

  // Handle drag start
  const handleDragStart = useCallback(
    (event: DragEvent<HTMLLIElement>, tool: SystemTool) => {
      event.dataTransfer.setData('application/workflow-tool', JSON.stringify(tool));
      event.dataTransfer.effectAllowed = 'move';
    },
    []
  );

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', p: 3 }}>
        <CircularProgress size={24} sx={{ color: 'primary.main' }} />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        bgcolor: 'rgba(10, 14, 23, 0.6)',
        borderRight: '1px solid rgba(255, 255, 255, 0.06)',
      }}
    >
      {/* Header */}
      <Box sx={{ p: 2, borderBottom: '1px solid rgba(255, 255, 255, 0.06)' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="subtitle1" sx={{
            fontWeight: 600
          }}>
            Tool Palette
          </Typography>
          <Tooltip title="Close">
            <IconButton aria-label="Close" size="small" onClick={onClose}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>

        {/* Search */}
        <TextField
          size="small"
          fullWidth
          placeholder="Search tools..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          sx={ghostInputSx}
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" sx={{ color: 'rgba(255, 255, 255, 0.3)' }} />
                </InputAdornment>
              ),
            }
          }}
        />
      </Box>
      {/* Error alert */}
      {error && (
        <Alert
          severity="warning"
          sx={{
            m: 1,
            bgcolor: 'rgba(255, 171, 0, 0.08)',
            border: '1px solid rgba(255, 171, 0, 0.2)',
            color: 'warning.main',
            '& .MuiAlert-icon': { color: 'warning.main' },
          }}
        >
          {error}
        </Alert>
      )}
      {/* Scrollable content */}
      <Box sx={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        {/* Unified Triggers Section */}
        <Accordion
          expanded={triggersExpanded}
          onChange={(_, isExpanded) => setTriggersExpanded(isExpanded)}
          disableGutters
          elevation={0}
          sx={{
            bgcolor: 'transparent',
            '&:before': { display: 'none' },
            borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
          }}
        >
          <AccordionSummary
            expandIcon={<ExpandMoreIcon />}
            sx={{ minHeight: 48, '& .MuiAccordionSummary-content': { my: 0 } }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <BoltIcon sx={{ fontSize: 18, color: 'warning.main' }} />
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 600,
                  color: 'warning.main'
                }}>
                Triggers
              </Typography>
            </Box>
          </AccordionSummary>
          <AccordionDetails sx={{ p: 0 }}>
            <List dense disablePadding>
              {/* Manual Trigger */}
              <Tooltip
                title="Define custom input fields for manual execution or API calls"
                placement="right"
              >
                <ListItem
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData('application/workflow-input', JSON.stringify({
                      type: 'workflow-input',
                      name: 'Workflow Input',
                    }));
                    e.dataTransfer.effectAllowed = 'move';
                  }}
                  sx={{
                    cursor: 'grab',
                    borderLeft: `3px solid ${CardColors.success}`,
                    '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.04)' },
                    '&:active': { cursor: 'grabbing' },
                  }}
                >
                  <ListItemIcon sx={{ minWidth: 32 }}>
                    <PlayArrowIcon fontSize="small" sx={{ color: 'success.main' }} />
                  </ListItemIcon>
                  <ListItemText
                    primary={<Typography variant="body2" sx={{
                      fontWeight: 500
                    }}>Manual / API</Typography>}
                    secondary={<Typography variant="caption" sx={{
                      color: "text.secondary"
                    }}>Define input fields</Typography>}
                  />
                </ListItem>
              </Tooltip>

              {/* Event Triggers */}
              {(() => {
                const eventsByCategory = getEventSourcesByCategory();
                return (
                  <>
                    {/* Graph Events Sub-section */}
                    <ListItem sx={{ bgcolor: 'rgba(0, 0, 0, 0.15)', py: 0.5 }}>
                      <ListItemIcon sx={{ minWidth: 24 }}>
                        <AccountTreeIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                      </ListItemIcon>
                      <ListItemText
                        primary={
                          <Typography
                            variant="caption"
                            sx={{
                              fontWeight: 600,
                              color: "text.secondary"
                            }}>
                            Graph Events
                          </Typography>
                        }
                      />
                    </ListItem>
                    {eventsByCategory.graph.map((info) => (
                      <Tooltip
                        key={info.id}
                        title={info.description}
                        placement="right"
                      >
                        <ListItem
                          draggable
                          onDragStart={(e) => {
                            e.dataTransfer.setData('application/workflow-trigger', JSON.stringify({
                              type: 'event-trigger',
                              eventSource: info.id,
                              name: info.label,
                            }));
                            e.dataTransfer.effectAllowed = 'move';
                          }}
                          sx={{
                            cursor: 'grab',
                            borderLeft: `3px solid ${CardColors.warning}`,
                            '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.04)' },
                            '&:active': { cursor: 'grabbing' },
                          }}
                        >
                          <ListItemIcon sx={{ minWidth: 32 }}>
                            <BoltIcon fontSize="small" sx={{ color: 'warning.main' }} />
                          </ListItemIcon>
                          <ListItemText
                            primary={<Typography variant="body2" sx={{
                              fontWeight: 500
                            }}>{info.label}</Typography>}
                          />
                        </ListItem>
                      </Tooltip>
                    ))}
                    {/* File Events Sub-section */}
                    <ListItem sx={{ bgcolor: 'rgba(0, 0, 0, 0.15)', py: 0.5 }}>
                      <ListItemIcon sx={{ minWidth: 24 }}>
                        <StorageIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                      </ListItemIcon>
                      <ListItemText
                        primary={
                          <Typography
                            variant="caption"
                            sx={{
                              fontWeight: 600,
                              color: "text.secondary"
                            }}>
                            File Events
                          </Typography>
                        }
                      />
                    </ListItem>
                    {eventsByCategory.file.map((info) => (
                      <Tooltip
                        key={info.id}
                        title={info.description}
                        placement="right"
                      >
                        <ListItem
                          draggable
                          onDragStart={(e) => {
                            e.dataTransfer.setData('application/workflow-trigger', JSON.stringify({
                              type: 'event-trigger',
                              eventSource: info.id,
                              name: info.label,
                            }));
                            e.dataTransfer.effectAllowed = 'move';
                          }}
                          sx={{
                            cursor: 'grab',
                            borderLeft: `3px solid ${CardColors.primary}`,
                            '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.04)' },
                            '&:active': { cursor: 'grabbing' },
                          }}
                        >
                          <ListItemIcon sx={{ minWidth: 32 }}>
                            <BoltIcon fontSize="small" sx={{ color: 'primary.main' }} />
                          </ListItemIcon>
                          <ListItemText
                            primary={<Typography variant="body2" sx={{
                              fontWeight: 500
                            }}>{info.label}</Typography>}
                          />
                        </ListItem>
                      </Tooltip>
                    ))}
                  </>
                );
              })()}
            </List>
          </AccordionDetails>
        </Accordion>

        {/* Tools list */}
        {Object.entries(filteredToolsByCategory).map(([category, categoryTools]) => {
          const config = CATEGORY_CONFIG[category] || {
            name: formatCategoryName(category),
            icon: BuildIcon,
            color: CategoryColors.templates,
          };

          return (
            <ToolCategoryAccordion
              key={category}
              category={category}
              categoryName={config.name}
              CategoryIcon={config.icon}
              categoryColor={config.color}
              tools={categoryTools}
              expanded={expandedCategories.includes(category)}
              onAccordionChange={handleAccordionChange(category)}
              onDragStart={handleDragStart}
            />
          );
        })}

        {/* Empty state */}
        {Object.keys(filteredToolsByCategory).length === 0 && (
          <Box sx={{ p: 3, textAlign: 'center' }}>
            <Typography variant="body2" sx={{
              color: "text.secondary"
            }}>
              {searchQuery ? 'No tools match your search.' : 'No tools available.'}
            </Typography>
          </Box>
        )}
      </Box>
      {/* Help text */}
      <Box sx={{ p: 1.5, borderTop: '1px solid rgba(255, 255, 255, 0.06)', bgcolor: 'rgba(0, 0, 0, 0.15)' }}>
        <Typography variant="caption" component="div" sx={{
          color: "text.secondary"
        }}>
          Drag items onto the canvas to add them to the workflow.
        </Typography>
      </Box>
    </Box>
  );
};

// Sample tools for development/fallback
function getSampleTools(): SystemTool[] {
  return [
    {
      id: 'ai.prompt',
      category: 'ai',
      name: 'AI Prompt',
      description: 'Execute an AI prompt with optional context and return the response.',
      input_schema: {},
      output_schema: {},
      version: '1.0.0',
      is_active: true,
    },
    {
      id: 'ai.extract_json',
      category: 'ai',
      name: 'Extract JSON',
      description: 'Extract structured JSON data from unstructured text using AI.',
      input_schema: {},
      output_schema: {},
      version: '1.0.0',
      is_active: true,
    },
    {
      id: 'ai.generate_embedding',
      category: 'ai',
      name: 'Generate Embedding',
      description: 'Generate vector embeddings for text content.',
      input_schema: {},
      output_schema: {},
      version: '1.0.0',
      is_active: true,
    },
    {
      id: 'ai.vector_search',
      category: 'ai',
      name: 'Vector Search',
      description: 'Search for similar content using vector similarity.',
      input_schema: {},
      output_schema: {},
      version: '1.0.0',
      is_active: true,
    },
    {
      id: 'graph.query',
      category: 'graph',
      name: 'Graph Query',
      description: 'Execute a SPARQL query against the knowledge graph.',
      input_schema: {},
      output_schema: {},
      version: '1.0.0',
      is_active: true,
    },
    {
      id: 'graph.create_node',
      category: 'graph',
      name: 'Create Node',
      description: 'Create a new node in the knowledge graph.',
      input_schema: {},
      output_schema: {},
      version: '1.0.0',
      is_active: true,
    },
    {
      id: 'logic.conditional',
      category: 'logic',
      name: 'Conditional',
      description: 'Branch workflow execution based on a condition.',
      input_schema: {},
      output_schema: {},
      version: '1.0.0',
      is_active: true,
    },
    {
      id: 'data.transform',
      category: 'data',
      name: 'Transform Data',
      description: 'Transform and reshape data between steps.',
      input_schema: {},
      output_schema: {},
      version: '1.0.0',
      is_active: true,
    },
    {
      id: 'external.http_request',
      category: 'external',
      name: 'HTTP Request',
      description: 'Make an HTTP request to an external API.',
      input_schema: {},
      output_schema: {},
      version: '1.0.0',
      is_active: true,
    },
  ];
}
