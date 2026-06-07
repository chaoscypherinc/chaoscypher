// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * GraphCanvasContent: Main canvas component for the knowledge graph.
 *
 * Renders inside SigmaContainer and uses sigma hooks for all
 * graph interactions. Overlay UI (search, menus, panels, modals)
 * is positioned absolutely over the canvas.
 *
 * Logic is delegated to focused hooks:
 * - useSpotlightHover — debounced hover highlighting
 * - useZoomIcons — zoom-adaptive icon visibility
 * - useGraphReducers — sigma node/edge visual reducers
 */

import React, { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { useSigma } from '@react-sigma/core';
import type Graph from 'graphology';
import {
  Box,
  Button,
  CircularProgress,
  Alert,
  IconButton,
  Tooltip,
  Typography,
} from '@mui/material';
import FilterListIcon from '@mui/icons-material/FilterListOutlined';

import { PropertiesPanel } from './components/PropertiesPanel';
import { ItemCreationModal } from './components/ItemCreationModal';
import { LinkCreationModal } from './components/LinkCreationModal';
import { SearchBar } from './components/SearchBar';
import { TemplateSelectionModal } from './components/TemplateSelectionModal';
import { LayoutDisplayMenu, FiltersMenu, KeyboardShortcutsMenu } from './components/CanvasControlMenus';
import { HorizontalLayoutSelector } from './components/HorizontalLayoutSelector';
import { GraphSpeedDial } from './components/GraphSpeedDial';
import { LoadingState } from '../../components/LoadingState';
import {
  NodeContextMenu,
  EdgeContextMenu,
  CanvasContextMenu,
} from './components/ContextMenus';
import {
  useNodeContextMenu,
  useEdgeContextMenu,
  useCanvasContextMenu,
} from './components/contextMenuHooks';
import {
  useGraphDataLoader,
  useLayoutManager,
  useNodeEdgeManager,
  useKeyboardShortcuts,
  useGraphSelection,
  useSearchHighlight,
  useSigmaEvents,
  useNodeDrag,
  useSigmaTheme,
  useSpotlightHover,
  useZoomIcons,
  useGraphReducers,
} from './hooks';
import { useMenuState } from '../../hooks';
import type { NodeAttributes, EdgeAttributes, LayoutType, GraphNodeData, GraphEdgeData } from './types';
import { isSourceGroupNode, SOURCE_GROUP_PREFIX } from './types';
import { useSourceGroups } from './hooks/useSourceGroups';
import { ChaosCypherBackground } from '../../theme/palette';
import ConfirmDialog from '../../components/ConfirmDialog';

interface GraphCanvasContentProps {
  graph: Graph<NodeAttributes, EdgeAttributes>;
}

export const GraphCanvasContent: React.FC<GraphCanvasContentProps> = ({ graph }) => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const sigma = useSigma<NodeAttributes, EdgeAttributes>();

  // Source filter from URL params (supports ?source_ids=id1,id2)
  const [sourceFilters, setSourceFilters] = useState<string[]>(() => {
    const param = searchParams.get('source_ids');
    return param ? param.split(',').filter(Boolean) : [];
  });

  // UI state
  const [layoutType, setLayoutType] = useState<LayoutType>('mindmap');
  const [nodeCreationModal, setNodeCreationModal] = useState<{
    open: boolean;
    position?: { x: number; y: number };
  }>({ open: false });
  const [showLabels, setShowLabels] = useState(false);
  const [templateSelectionModal, setTemplateSelectionModal] = useState<{
    open: boolean;
    type: 'node' | 'edge';
    position?: { x: number; y: number };
  }>({ open: false, type: 'node' });
  const [templateFilters, setTemplateFilters] = useState<string[]>([]);

  // Menu anchors — useMenuState for filters (click-event driven),
  // plain state for layout/shortcuts (receive HTMLElement directly
  // from HorizontalLayoutSelector callbacks).
  const filtersMenu = useMenuState();
  const [layoutMenuAnchor, setLayoutMenuAnchor] = useState<HTMLElement | null>(null);
  const [shortcutsMenuAnchor, setShortcutsMenuAnchor] = useState<HTMLElement | null>(null);

  // Apply theme to sigma
  useSigmaTheme();

  // Toggle label visibility based on display settings
  useEffect(() => {
    sigma.setSetting('renderLabels', showLabels);
  }, [sigma, showLabels]);

  // Enable node dragging
  useNodeDrag();

  // Layout Manager
  const { applyLayout } = useLayoutManager({
    graph,
    setLayoutType,
    setError: (err) => setError(err),
  });

  // Graph Data Loader
  const { loading, reloading, error, setError, loadGraphData } = useGraphDataLoader({
    graph,
    applyLayout,
    layoutType,
    sourceIds: sourceFilters.length > 0 ? sourceFilters : undefined,
  });

  // Selection
  const {
    selectedNodeId,
    selectedNodeData,
    selectedEdgeId,
    selectedEdgeData,
    isPropertiesPanelOpen,
    edgeCreationModal,
    setSelectedNodeData,
    setIsPropertiesPanelOpen,
    setEdgeCreationModal,
    handleNodeClick,
    handleEdgeClick,
    handleStageClick,
    handleCopyNodeId,
    handleViewSourceDocument,
    clearSelection,
  } = useGraphSelection();

  // Node/Edge CRUD
  const {
    handleNodeCreate,
    handleNodeUpdate,
    handleNodeDelete,
    handleNodeDuplicate,
    handleEdgeCreate,
    handleEdgeDelete,
  } = useNodeEdgeManager({
    graph,
    setError,
    setIsPropertiesPanelOpen,
  });

  // Search & Highlight
  const { handleSearch, highlightedNodeIds, hiddenNodeIds, hasActiveSearch } = useSearchHighlight({
    graph,
    templateFilters,
  });

  // Keyboard Shortcuts
  const { pendingDelete, confirmDelete, cancelDelete } = useKeyboardShortcuts({
    selectedNodeId,
    selectedNodeData,
    selectedEdgeId,
    clearSelection,
    handleNodeDelete,
    handleEdgeDelete,
    handleNodeDuplicate,
  });

  // Source Groups
  const {
    groups: sourceGroups,
    collapsedMemberIds,
    collapsedSourceIds,
    loadSourceGroups,
    toggleGroup,
    expandAll: expandAllGroups,
    collapseAll: collapseAllGroups,
    getNodeSourceGroup,
  } = useSourceGroups();

  // Load source groups after graph data finishes loading
  const sourceGroupsLoadedRef = useRef(false);
  useEffect(() => {
    if (!loading && graph.order > 0 && !sourceGroupsLoadedRef.current) {
      sourceGroupsLoadedRef.current = true;
      loadSourceGroups(graph);
    }
  }, [loading, graph, loadSourceGroups]);

  // Context menus
  const nodeMenu = useNodeContextMenu();
  const edgeMenu = useEdgeContextMenu();
  const canvasMenu = useCanvasContextMenu();

  // Sigma event handlers
  const onNodeRightClick = useCallback(
    (nodeId: string, data: GraphNodeData, event: MouseEvent) => {
      nodeMenu.show({ event, props: { nodeId, data } });
    },
    [nodeMenu],
  );

  const onEdgeRightClick = useCallback(
    (edgeId: string, data: GraphEdgeData, event: MouseEvent) => {
      edgeMenu.show({ event, props: { edgeId, data } });
    },
    [edgeMenu],
  );

  const onStageRightClick = useCallback(
    (event: MouseEvent) => {
      const pos = sigma.viewportToGraph({ x: event.clientX, y: event.clientY });
      canvasMenu.show({ event, props: { x: pos.x, y: pos.y } });
    },
    [canvasMenu, sigma],
  );

  const onNodeDoubleClick = useCallback(
    (nodeId: string, data: GraphNodeData) => {
      if (isSourceGroupNode(nodeId)) {
        const sourceId = nodeId.slice(SOURCE_GROUP_PREFIX.length);
        toggleGroup(graph, sourceId);
      } else {
        setSelectedNodeData(data);
        setIsPropertiesPanelOpen(true);
      }
    },
    [graph, toggleGroup, setSelectedNodeData, setIsPropertiesPanelOpen],
  );

  // Register sigma events
  useSigmaEvents({
    onNodeClick: handleNodeClick,
    onEdgeClick: handleEdgeClick,
    onStageClick: handleStageClick,
    onNodeRightClick,
    onEdgeRightClick,
    onStageRightClick,
    onNodeDoubleClick,
  });

  // Spotlight hover (debounced neighbor highlighting)
  const { hoveredNode, hoveredNeighborsRef } = useSpotlightHover({ graph });

  // Zoom-adaptive icon visibility
  const iconVisibleBySizeRef = useZoomIcons();

  // Merge template-filtered hidden nodes with collapsed source group members
  const allHiddenNodeIds = useMemo(() => {
    if (collapsedMemberIds.size === 0) return hiddenNodeIds;
    const merged = new Set(hiddenNodeIds);
    for (const id of collapsedMemberIds) merged.add(id);
    return merged;
  }, [hiddenNodeIds, collapsedMemberIds]);

  // Node & edge reducers for visual effects
  useGraphReducers({
    graph,
    selectedNodeId,
    selectedEdgeId,
    highlightedNodeIds,
    hiddenNodeIds: allHiddenNodeIds,
    hasActiveSearch,
    hoveredNode,
    hoveredNeighborsRef,
    iconVisibleBySizeRef,
    collapsedSourceIds,
  });

  // Handle template selection from template selection modal
  const handleTemplateSelect = useCallback(
    (templateId: string) => {
      const position = templateSelectionModal.position;
      setTemplateSelectionModal({ open: false, type: 'node' });
      setNodeCreationModal({ open: true, position });
      handleNodeCreate(templateId, position);
    },
    [templateSelectionModal.position, handleNodeCreate],
  );

  // Handle source filter changes: sync to URL and reload graph
  const handleSourceFiltersChange = useCallback((newSourceIds: string[]) => {
    setSourceFilters(newSourceIds);
    setSearchParams(prev => {
      const next = new URLSearchParams(prev);
      if (newSourceIds.length > 0) {
        next.set('source_ids', newSourceIds.join(','));
      } else {
        next.delete('source_ids');
      }
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  // Load graph data on mount and when source filters change
  const isInitialMount = useRef(true);
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      loadGraphData();
      return;
    }
    loadGraphData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceFilters]);

  // Fit view after data loads
  useEffect(() => {
    if (!loading && graph.order > 0) {
      const timer = setTimeout(() => {
        sigma.getCamera().animatedReset({ duration: 400 });
      }, 150);
      return () => clearTimeout(timer);
    }
  }, [loading, graph.order, sigma]);

  if (loading) {
    return (
      <Box sx={{ position: 'absolute', inset: 0, zIndex: 100, bgcolor: ChaosCypherBackground.dark.default }}>
        <LoadingState message="Loading knowledge graph..." minHeight="100%" />
      </Box>
    );
  }

  // Empty-state when the graph is empty and no filter is active. Covers two
  // real launch-day paths: a fresh install with no sources, and the
  // canvas_max_* > safe-cap branch where the backend rejects an unscoped
  // query (graph/service.py raises ValidationError on `source_ids`).
  const showEmptyState = !loading && !reloading && graph.order === 0 && sourceFilters.length === 0;

  return (
    <>
      {/* Empty-state overlay — friendly CTA instead of a bare red Alert. */}
      {showEmptyState && (
        <Box
          sx={{
            position: 'absolute',
            inset: 0,
            zIndex: 100,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            bgcolor: ChaosCypherBackground.dark.default,
            p: 4,
          }}
        >
          <Box sx={{ maxWidth: 480, textAlign: 'center' }}>
            <Typography variant="h6" gutterBottom>
              No graph to display
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
              {error
                ? error
                : 'Add a source from the Sources page, or pick one or more existing sources to scope the canvas.'}
            </Typography>
            <Button
              variant="contained"
              startIcon={<FilterListIcon />}
              onClick={filtersMenu.open}
            >
              Select sources
            </Button>
          </Box>
        </Box>
      )}

      {/* Error Alert — only shown when we have something on canvas to keep visible. */}
      {error && !showEmptyState && (
        <Alert
          severity="error"
          onClose={() => setError(null)}
          sx={{
            position: 'absolute',
            top: 16,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 1000,
          }}
        >
          {error}
        </Alert>
      )}

      {/* Reloading indicator (non-blocking, for filter changes) */}
      {reloading && (
        <CircularProgress
          size={24}
          sx={{
            position: 'absolute',
            top: 16,
            right: 16,
            zIndex: 1000,
          }}
        />
      )}

      {/* Top Left - Search with Filter */}
      <Box
        sx={{
          position: 'absolute',
          top: 16,
          left: 16,
          zIndex: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 0.5,
        }}
      >
        <Box
          sx={{
            display: 'flex',
            gap: 1,
            alignItems: 'center',
            flexDirection: 'row',
            background: 'rgba(15, 20, 30, 0.15)',
            backdropFilter: 'blur(12px)',
            WebkitBackdropFilter: 'blur(12px)',
            border: '1px solid rgba(255, 255, 255, 0.05)',
            borderRadius: 1.5,
            p: 1,
            '& .MuiOutlinedInput-root': {
              bgcolor: 'transparent',
              '& .MuiOutlinedInput-notchedOutline': { border: 'none' },
            },
            '& .MuiOutlinedInput-root.Mui-focused': {
              '& .MuiOutlinedInput-notchedOutline': {
                border: '1px solid',
                borderColor: 'primary.main',
              },
            },
          }}
        >
          <SearchBar onSearch={handleSearch} />
          <Tooltip title="Filters" placement="bottom">
            <IconButton
              aria-label="Filters"
              size="small"
              onClick={filtersMenu.open}
              color={filtersMenu.isOpen || templateFilters.length > 0 || sourceFilters.length > 0 ? 'primary' : 'default'}
            >
              <FilterListIcon />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>

      {/* Layout Selector SpeedDial - Top Right */}
      <HorizontalLayoutSelector
        currentLayout={layoutType}
        onLayoutChange={applyLayout}
        onOpenSettings={(anchor) => setLayoutMenuAnchor(anchor)}
        onOpenKeyboardShortcuts={(anchor) => setShortcutsMenuAnchor(anchor)}
      />

      {/* Popup Menus */}
      <LayoutDisplayMenu
        anchorEl={layoutMenuAnchor}
        open={Boolean(layoutMenuAnchor)}
        onClose={() => setLayoutMenuAnchor(null)}
        showLabels={showLabels}
        onShowLabelsChange={setShowLabels}
      />
      <FiltersMenu
        anchorEl={filtersMenu.anchorEl}
        open={filtersMenu.isOpen}
        onClose={filtersMenu.close}
        selectedTemplateFilters={templateFilters}
        onTemplateFiltersChange={setTemplateFilters}
        selectedSourceFilters={sourceFilters}
        onSourceFiltersChange={handleSourceFiltersChange}
      />
      <KeyboardShortcutsMenu
        anchorEl={shortcutsMenuAnchor}
        open={Boolean(shortcutsMenuAnchor)}
        onClose={() => setShortcutsMenuAnchor(null)}
      />

      {/* Properties Panel (Right) */}
      <PropertiesPanel
        open={isPropertiesPanelOpen}
        onClose={() => setIsPropertiesPanelOpen(false)}
        selectedNodeId={selectedNodeId}
        selectedNodeData={selectedNodeData}
        selectedEdgeId={selectedEdgeId}
        selectedEdgeData={selectedEdgeData}
        onNodeUpdate={handleNodeUpdate}
        onNodeDelete={handleNodeDelete}
        onEdgeDelete={handleEdgeDelete}
        getNodeSourceGroup={getNodeSourceGroup}
        onToggleSourceGroup={(sourceId) => toggleGroup(graph, sourceId)}
        onNavigate={(path) => navigate(path)}
        onSelectNode={(nodeId) => {
          const data = graph.hasNode(nodeId) ? graph.getNodeAttributes(nodeId) : null;
          if (data) handleNodeClick(nodeId, data);
        }}
        graph={graph}
      />

      {/* Item Creation Modal */}
      <ItemCreationModal
        open={nodeCreationModal.open}
        onClose={() => setNodeCreationModal({ open: false })}
        onCreate={handleNodeCreate}
        position={nodeCreationModal.position}
      />

      {/* Link Creation Modal */}
      <LinkCreationModal
        open={edgeCreationModal.open}
        onClose={() => setEdgeCreationModal({ open: false })}
        onCreate={handleEdgeCreate}
        sourceId={edgeCreationModal.sourceId}
        targetId={edgeCreationModal.targetId}
      />

      {/* Context Menus */}
      <NodeContextMenu
        menuState={nodeMenu}
        onEdit={(_nodeId, data) => {
          setSelectedNodeData(data);
          setIsPropertiesPanelOpen(true);
        }}
        onDelete={(nodeId) => handleNodeDelete(nodeId)}
        onDuplicate={(nodeId, data) => handleNodeDuplicate(nodeId, data)}
        onCopyId={(nodeId) => handleCopyNodeId(nodeId)}
        onViewSourceDocument={(_nodeId, data) => handleViewSourceDocument(data)}
        onToggleSourceGroup={(sourceId) => toggleGroup(graph, sourceId)}
        onNavigateToSource={(sourceId) => navigate(`/sources/${sourceId}`)}
        isSourceGroupExpanded={(sourceId) => sourceGroups.get(sourceId)?.expanded ?? false}
      />
      <EdgeContextMenu
        menuState={edgeMenu}
        onEdit={(_edgeId, _data) => {
          setSelectedNodeData(null);
          setIsPropertiesPanelOpen(true);
        }}
        onDelete={(edgeId) => handleEdgeDelete(edgeId)}
        onCopyId={(edgeId) => navigator.clipboard.writeText(edgeId)}
      />
      <CanvasContextMenu
        menuState={canvasMenu}
        onCreate={(position) =>
          setTemplateSelectionModal({ open: true, type: 'node', position })
        }
        onFitView={() => sigma.getCamera().animatedReset({ duration: 400 })}
        onResetLayout={() => applyLayout(layoutType)}
        hasSourceGroups={sourceGroups.size > 0}
        onExpandAllGroups={() => expandAllGroups(graph)}
        onCollapseAllGroups={() => collapseAllGroups(graph)}
      />

      {/* Floating Action Button with Quick Actions */}
      <GraphSpeedDial
        onCreateItem={() => setTemplateSelectionModal({ open: true, type: 'node' })}
        onCreateLink={() => setEdgeCreationModal({ open: true })}
        onViewEntities={() => navigate('/nodes')}
        onViewRelationships={() => navigate('/edges')}
      />

      {/* Template Selection Modal */}
      <TemplateSelectionModal
        open={templateSelectionModal.open}
        onClose={() => setTemplateSelectionModal({ open: false, type: 'node' })}
        onSelect={handleTemplateSelect}
        templateType={templateSelectionModal.type}
      />

      {/* Keyboard Delete Confirmation */}
      <ConfirmDialog
        open={!!pendingDelete}
        title="Confirm Delete"
        message={pendingDelete?.message || ''}
        onConfirm={confirmDelete}
        onCancel={cancelDelete}
      />
    </>
  );
};
