// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ToolsPage: Browse system tools and manage user tool configurations.
 */
import { useMemo, useState } from 'react';
import {
  Box,
  Typography,
  Tabs,
  Tab,
  Button,
  Alert,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import {
  ghostButtonSx,
  ghostErrorAlertSx,
  ghostInfoAlertSx,
  ghostTabsSx,
} from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';
import { useConfirmDialog } from '../hooks/useConfirmDialog';
import ConfirmDialog from '../components/ConfirmDialog';
import SearchFilterBar from '../components/SearchFilterBar';
import { LoadingState } from '../components/LoadingState';
import SystemToolCard from './ToolsPage/SystemToolCard';
import UserToolCard from './ToolsPage/UserToolCard';
import { ToolFormDialog, SchemaDialog } from './ToolsPage/ToolDialogs';
import {
  useSystemTools,
  useUserTools,
  useSystemTool,
  useCreateUserTool,
  useUpdateUserTool,
  useDeleteUserTool,
  useDuplicateUserTool,
} from '../services/api/useTools';
import type { UserTool } from '../services/api/tools';

const CYAN = ChaosCypherPalette.primary;

const ToolsPage: React.FC = () => {
  const [tabValue, setTabValue] = useState(0);
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [error, setError] = useState<string | null>(null);

  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [schemaDialogOpen, setSchemaDialogOpen] = useState(false);
  const [schemaToolId, setSchemaToolId] = useState<string | null>(null);
  const [editingTool, setEditingTool] = useState<UserTool | null>(null);
  const deleteDialog = useConfirmDialog<string>();

  const [toolName, setToolName] = useState('');
  const [toolDescription, setToolDescription] = useState('');
  const [selectedSystemTool, setSelectedSystemTool] = useState('');
  const [toolConfiguration, setToolConfiguration] = useState('{}');
  const [toolTags, setToolTags] = useState<string[]>([]);

  const systemToolsQuery = useSystemTools();
  const userToolsQuery = useUserTools();
  const schemaToolQuery = useSystemTool(schemaToolId);
  const createTool = useCreateUserTool();
  const updateTool = useUpdateUserTool();
  const deleteTool = useDeleteUserTool();
  const duplicateTool = useDuplicateUserTool();

  const systemTools = useMemo(() => systemToolsQuery.data ?? [], [systemToolsQuery.data]);
  const userTools = useMemo(() => userToolsQuery.data ?? [], [userToolsQuery.data]);
  const loading = systemToolsQuery.isPending || userToolsQuery.isPending;

  const surfaceErr = (err: unknown) => {
    setError(err instanceof Error ? err.message : String(err));
  };

  const resetForm = () => {
    setToolName('');
    setToolDescription('');
    setSelectedSystemTool('');
    setToolConfiguration('{}');
    setToolTags([]);
    setEditingTool(null);
  };

  const handleCreateUserTool = () => {
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(toolConfiguration);
    } catch (err) {
      surfaceErr(err);
      return;
    }
    createTool.mutate(
      {
        name: toolName,
        description: toolDescription,
        system_tool_id: selectedSystemTool,
        configuration: config,
        tags: toolTags,
      },
      {
        onSuccess: () => {
          setCreateDialogOpen(false);
          resetForm();
        },
        onError: surfaceErr,
      },
    );
  };

  const handleUpdateUserTool = () => {
    if (!editingTool) return;
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(toolConfiguration);
    } catch (err) {
      surfaceErr(err);
      return;
    }
    updateTool.mutate(
      {
        id: editingTool.id,
        patch: {
          name: toolName,
          description: toolDescription,
          configuration: config,
          tags: toolTags,
        },
      },
      {
        onSuccess: () => {
          setEditDialogOpen(false);
          resetForm();
        },
        onError: surfaceErr,
      },
    );
  };

  const handleDeleteUserTool = (toolId: string) => {
    deleteDialog.open(toolId);
  };

  const handleConfirmDeleteUserTool = () => {
    void deleteDialog.confirm(async () => {
      await new Promise<void>((resolve) => {
        deleteTool.mutate(deleteDialog.data!, {
          onError: (err) => {
            surfaceErr(err);
            resolve();
          },
          onSuccess: () => resolve(),
        });
      });
    });
  };

  const handleDuplicateUserTool = (toolId: string) => {
    duplicateTool.mutate(toolId, { onError: surfaceErr });
  };

  const openCreateDialog = () => {
    resetForm();
    setCreateDialogOpen(true);
  };

  const openEditDialog = (tool: UserTool) => {
    setEditingTool(tool);
    setToolName(tool.name);
    setToolDescription(tool.description || '');
    setSelectedSystemTool(tool.system_tool_id);
    setToolConfiguration(JSON.stringify(tool.configuration, null, 2));
    setToolTags(tool.tags || []);
    setEditDialogOpen(true);
  };

  const openSchemaDialog = (toolId: string) => {
    setSchemaToolId(toolId);
    setSchemaDialogOpen(true);
  };

  const closeFormDialog = () => {
    setCreateDialogOpen(false);
    setEditDialogOpen(false);
  };

  const filteredSystemTools = systemTools.filter(tool => {
    const matchesCategory = selectedCategory === 'all' || tool.category === selectedCategory;
    const matchesSearch = tool.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      tool.description.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesCategory && matchesSearch;
  });

  const filteredUserTools = userTools.filter(tool =>
    tool.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    (tool.description && tool.description.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  const categories = useMemo(
    () => ['all', ...new Set(systemTools.map(t => t.category))],
    [systemTools],
  );

  const queryError =
    systemToolsQuery.error ?? userToolsQuery.error ?? null;
  const surfacedError =
    error ?? (queryError instanceof Error ? queryError.message : null);

  return (
    <Box sx={{ maxWidth: 'xl', mx: 'auto', mt: { xs: 2, md: 4 }, mb: { xs: 2, md: 4 }, px: { xs: 1, md: 3 } }}>
      <Box
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 2,
          justifyContent: 'space-between',
          alignItems: { xs: 'flex-start', sm: 'center' },
          mb: 3,
        }}
      >
        <Box>
          <Typography variant="h4">Tools</Typography>
          <Typography
            variant="body2"
            sx={{
              color: "text.secondary",
              mt: 0.5
            }}>
            Browse system tools and create custom tool configurations for your workflows.
          </Typography>
        </Box>
        {tabValue === 1 && (
          <Button
            variant="outlined"
            startIcon={<AddIcon />}
            onClick={openCreateDialog}
            sx={ghostButtonSx(CYAN)}
          >
            New Tool
          </Button>
        )}
      </Box>
      {surfacedError && (
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }} onClose={() => setError(null)}>
          {surfacedError}
        </Alert>
      )}
      {/* Search and Filter */}
      <SearchFilterBar
        searchLabel="Search tools"
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        filters={tabValue === 0 ? [{
          label: 'Category',
          value: selectedCategory,
          options: categories.map(cat => ({
            value: cat,
            label: cat.charAt(0).toUpperCase() + cat.slice(1),
          })),
          onChange: setSelectedCategory,
        }] : []}
      />
      {/* Tabs */}
      <Tabs
        value={tabValue}
        onChange={(_, newValue) => setTabValue(newValue)}
        sx={{ mb: 3, ...ghostTabsSx }}
      >
        <Tab label={`System Tools (${systemTools.length})`} />
        <Tab label={`My Tools (${userTools.length})`} />
      </Tabs>
      {/* Loading */}
      {loading && <LoadingState message="Loading tools..." minHeight="200px" />}
      {/* System Tools Tab */}
      {tabValue === 0 && !loading && (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
          {filteredSystemTools.map(tool => (
            <SystemToolCard key={tool.id} tool={tool} onViewSchema={openSchemaDialog} />
          ))}
        </Box>
      )}
      {/* User Tools Tab */}
      {tabValue === 1 && !loading && (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
          {filteredUserTools.map(tool => (
            <UserToolCard
              key={tool.id}
              tool={tool}
              onEdit={openEditDialog}
              onDuplicate={handleDuplicateUserTool}
              onDelete={handleDeleteUserTool}
            />
          ))}
          {filteredUserTools.length === 0 && (
            <Box sx={{ width: '100%' }}>
              <Alert severity="info" sx={ghostInfoAlertSx}>
                No user tools yet. Click &ldquo;New Tool&rdquo; to create a pre-configured system tool.
              </Alert>
            </Box>
          )}
        </Box>
      )}
      {/* Create/Edit Tool Dialog */}
      <ToolFormDialog
        open={createDialogOpen || editDialogOpen}
        isCreate={createDialogOpen}
        toolName={toolName}
        toolDescription={toolDescription}
        selectedSystemTool={selectedSystemTool}
        toolConfiguration={toolConfiguration}
        toolTags={toolTags}
        systemTools={systemTools}
        onToolNameChange={setToolName}
        onToolDescriptionChange={setToolDescription}
        onSelectedSystemToolChange={setSelectedSystemTool}
        onToolConfigurationChange={setToolConfiguration}
        onToolTagsChange={setToolTags}
        onSubmit={createDialogOpen ? handleCreateUserTool : handleUpdateUserTool}
        onClose={closeFormDialog}
      />
      {/* Schema Dialog */}
      <SchemaDialog
        open={schemaDialogOpen}
        tool={schemaToolQuery.data ?? null}
        onClose={() => {
          setSchemaDialogOpen(false);
          setSchemaToolId(null);
        }}
      />
      <ConfirmDialog
        open={deleteDialog.isOpen}
        title="Confirm Delete"
        message="Are you sure you want to delete this tool?"
        onConfirm={handleConfirmDeleteUserTool}
        onCancel={deleteDialog.close}
      />
    </Box>
  );
};

export default ToolsPage;
