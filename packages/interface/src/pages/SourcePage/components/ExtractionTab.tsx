// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ExtractionTab orchestrator.
 * Thin wrapper that renders three sub-tabs (Entities, Relationships, Templates)
 * and delegates data loading to useExtractionData.
 */

import { Box, Tabs, Tab } from '@mui/material';
import CategoryIcon from '@mui/icons-material/Category';
import ShareIcon from '@mui/icons-material/Share';
import SchemaIcon from '@mui/icons-material/Schema';
import { LoadingState } from '../../../components/LoadingState';
import { ghostTabsSx } from '../../../theme/ghostStyles';
import { EntitiesView, RelationshipsView, TemplatesView, useExtractionData } from './extraction';

interface ExtractionTabProps {
  sourceId: string;
  entitiesCount: number;
  relationshipsCount: number;
  templatesCount: number;
}

export function ExtractionTab({
  sourceId,
  entitiesCount,
  relationshipsCount,
  templatesCount,
}: ExtractionTabProps) {
  const {
    subTab,
    setSubTab,
    entities,
    relationships,
    templates,
    entitiesPage,
    setEntitiesPage,
    relationshipsPage,
    setRelationshipsPage,
    templatesPage,
    setTemplatesPage,
    sortBy,
    setSortBy,
    sortOrder,
    setSortOrder,
    loading,
    templateNameMap,
    pageSize,
  } = useExtractionData(sourceId);

  return (
    <Box>
      {/* Sub-tabs for Entities vs Relationships vs Templates */}
      <Box sx={{ mb: 2, borderBottom: '1px solid rgba(255, 255, 255, 0.06)' }}>
        <Tabs value={subTab} onChange={(_, newValue) => setSubTab(newValue)} sx={ghostTabsSx}>
          <Tab
            icon={<CategoryIcon sx={{ fontSize: 18 }} />}
            iconPosition="start"
            label={`Entities (${entitiesCount})`}
          />
          <Tab
            icon={<ShareIcon sx={{ fontSize: 18 }} />}
            iconPosition="start"
            label={`Relationships (${relationshipsCount})`}
          />
          <Tab
            icon={<SchemaIcon sx={{ fontSize: 18 }} />}
            iconPosition="start"
            label={`Templates (${templatesCount})`}
          />
        </Tabs>
      </Box>

      {loading && <LoadingState message="Loading extraction data..." />}

      {/* Entities View */}
      {!loading && subTab === 0 && (
        <EntitiesView
          entities={entities}
          entitiesCount={entitiesCount}
          entitiesPage={entitiesPage}
          setEntitiesPage={setEntitiesPage}
          sortBy={sortBy}
          setSortBy={setSortBy}
          sortOrder={sortOrder}
          setSortOrder={setSortOrder}
          pageSize={pageSize}
          templateNameMap={templateNameMap}
        />
      )}

      {/* Relationships View */}
      {!loading && subTab === 1 && (
        <RelationshipsView
          relationships={relationships}
          relationshipsCount={relationshipsCount}
          relationshipsPage={relationshipsPage}
          setRelationshipsPage={setRelationshipsPage}
          pageSize={pageSize}
          templateNameMap={templateNameMap}
        />
      )}

      {/* Templates View */}
      {!loading && subTab === 2 && (
        <TemplatesView
          templates={templates}
          templatesCount={templatesCount}
          templatesPage={templatesPage}
          setTemplatesPage={setTemplatesPage}
          pageSize={pageSize}
        />
      )}
    </Box>
  );
}
