// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useParams, useNavigate } from 'react-router';
import { Alert, Box, Button, Tab, Tabs } from '@mui/material';
import EditIcon from '@mui/icons-material/Edit';
import SaveIcon from '@mui/icons-material/Save';
import CancelIcon from '@mui/icons-material/Cancel';
import BackIcon from '@mui/icons-material/ArrowBack';
import DeleteIcon from '@mui/icons-material/Delete';
import SourceIcon from '@mui/icons-material/Description';
import TemplateIcon from '../../components/TemplateIcon';
import DetailPageHeader from '../../components/detail/DetailPageHeader';
import DetailPageLayout from '../../components/detail/DetailPageLayout';
import ConfirmDialog from '../../components/ConfirmDialog';
import { LoadingState } from '../../components/LoadingState';
import {
  ghostButtonSx,
  ghostCancelBtnSx,
  ghostErrorAlertSx,
  ghostTabsSx,
} from '../../theme/ghostStyles';
import { glassPanelSx } from '../../theme/cardStyles';
import { ChaosCypherPalette } from '../../theme/palette';
import { useNodeDetail } from './hooks/useNodeDetail';
import DetailsTab from './components/tabs/DetailsTab';
import ConnectionsTab from './components/tabs/ConnectionsTab';
import SourcesTab from './components/tabs/SourcesTab';
import RawJsonTab from './components/tabs/RawJsonTab';
import EntityMetadataCard from './components/sidebar/EntityMetadataCard';
import SourceImagesCard from './components/sidebar/SourceImagesCard';
import StatsCard from './components/sidebar/StatsCard';

export default function NodeDetailPage() {
  const { nodeId } = useParams<{ nodeId: string }>();
  const navigate = useNavigate();
  const { state, actions } = useNodeDetail(nodeId);

  if (state.loading) {
    return <LoadingState message="Loading entity..." fullPage />;
  }

  // Only a genuine load failure (no entity to show) ejects to the full-page
  // error view. Action errors (failed save/delete) keep `state.entity`
  // populated and surface via the inline alert below, so the edit form and
  // its unsaved changes survive.
  if (!state.entity) {
    return (
      <Box>
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
          {state.error || 'Entity not found'}
        </Alert>
        <Button
          startIcon={<BackIcon />}
          onClick={() => navigate('/nodes')}
          sx={ghostCancelBtnSx}
        >
          Back to Entities
        </Button>
      </Box>
    );
  }

  const { entity, template, editing, formData, activeTab } = state;

  const headerActions = editing ? (
    <>
      <Button startIcon={<CancelIcon />} onClick={actions.handleCancel} sx={ghostCancelBtnSx}>
        Cancel
      </Button>
      <Button
        variant="outlined"
        startIcon={<SaveIcon />}
        onClick={actions.handleSave}
        sx={ghostButtonSx(ChaosCypherPalette.success)}
      >
        Save
      </Button>
    </>
  ) : (
    <>
      {entity.source_id && (
        <Button
          variant="outlined"
          startIcon={<SourceIcon />}
          onClick={() => navigate(`/sources/${entity.source_id}`)}
          sx={ghostButtonSx(ChaosCypherPalette.primary)}
        >
          View Source Document
        </Button>
      )}
      <Button
        variant="outlined"
        startIcon={<DeleteIcon />}
        onClick={actions.handleDelete}
        sx={ghostButtonSx(ChaosCypherPalette.error)}
      >
        Delete
      </Button>
      <Button
        variant="outlined"
        startIcon={<EditIcon />}
        onClick={actions.handleEdit}
        sx={ghostButtonSx(ChaosCypherPalette.primary)}
      >
        Edit
      </Button>
    </>
  );

  const main = (
    <Box sx={{ ...glassPanelSx, p: 3 }}>
      <Box sx={{ borderBottom: '1px solid rgba(255, 255, 255, 0.06)', mb: 2 }}>
        <Tabs
          value={activeTab}
          onChange={(_, newValue) => actions.setActiveTab(newValue)}
          sx={{ ...ghostTabsSx }}
        >
          <Tab label="Details" />
          <Tab
            label={`Connections${state.connectionsTotal > 0 ? ` (${state.connectionsTotal})` : ''}`}
          />
          <Tab
            label={`Sources${state.citationsTotal > 0 ? ` (${state.citationsTotal})` : ''}`}
          />
          <Tab label="Raw JSON" />
        </Tabs>
      </Box>

      {activeTab === 0 && (
        <DetailsTab
          entity={entity}
          editing={editing}
          formData={formData}
          onFormDataChange={actions.setFormData}
        />
      )}

      {activeTab === 1 && (
        <ConnectionsTab
          connections={state.connections}
          loading={state.connectionsLoading}
          sortBy={state.connectionsSortBy}
          hasMore={state.connectionsHasMore}
          onSortByChange={actions.setConnectionsSortBy}
          onLoadMore={() => actions.loadConnections(false)}
        />
      )}

      {activeTab === 2 && (
        <SourcesTab
          citations={state.citations}
          citationsTotal={state.citationsTotal}
          loading={state.citationsLoading}
        />
      )}

      {activeTab === 3 && (
        <RawJsonTab
          entity={entity}
          editing={editing}
          formData={formData}
          onFormDataChange={actions.setFormData}
        />
      )}
    </Box>
  );

  const sidebar = (
    <>
      <EntityMetadataCard entity={entity} template={template} />

      <SourceImagesCard
        images={state.sourceImages}
        expandedImage={state.expandedImage}
        onExpand={actions.setExpandedImage}
      />

      <StatsCard
        connectionsTotal={state.connectionsTotal}
        edgesTotal={entity.edge_count ?? null}
        citationsTotal={state.citationsTotal}
        propertiesCount={Object.keys(entity.properties || {}).length}
      />
    </>
  );

  return (
    <Box>
      <DetailPageHeader
        title={editing ? 'Edit Entity' : entity.label || ''}
        icon={
          <TemplateIcon
            template={template}
            fallbackTemplateId={entity.template_id}
            size={24}
            containerSize={40}
          />
        }
        onBack={() => navigate('/nodes')}
        actions={headerActions}
      />
      {state.error && (
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
          {state.error}
        </Alert>
      )}
      <DetailPageLayout main={main} sidebar={sidebar} />
      <ConfirmDialog
        open={state.confirmDeleteOpen}
        title="Confirm Delete"
        message="Are you sure you want to delete this entity?"
        onConfirm={actions.handleConfirmDelete}
        onCancel={actions.closeConfirmDelete}
      />
    </Box>
  );
}
