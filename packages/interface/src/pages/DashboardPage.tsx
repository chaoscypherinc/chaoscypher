// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useContext, useMemo } from 'react';
import { useNavigate } from 'react-router';
import { Box, ButtonBase, Typography, CircularProgress } from '@mui/material';
import { ChaosCypherPalette, ChaosCypherBackground } from '../theme/palette';
import { UploadDialogContext } from '../contexts/UploadDialogContext';
import DashboardGraph from './DashboardPage/DashboardGraph';
import SystemHealthCluster from './DashboardPage/SystemHealthCluster';
import StatsPanel from './DashboardPage/StatsPanel';
import { useActivityLog } from './DashboardPage/useActivityLog';
import { useCounts, useQualitySummary } from '../services/api/useDashboard';
import type { DashboardStats } from './DashboardPage/types';
import {
  computeAvgRelations,
  computeDensity,
  computeEdgesPerSource,
} from './DashboardPage/utils/stats';

const pillSx = {
  position: 'absolute' as const,
  bottom: 32,
  zIndex: 2,
  px: 2,
  py: 1,
  borderRadius: '20px',
  border: '1px solid rgba(0, 229, 255, 0.15)',
  bgcolor: 'rgba(5, 5, 10, 0.5)',
  backdropFilter: 'blur(8px)',
  WebkitBackdropFilter: 'blur(8px)',
  cursor: 'pointer',
  opacity: 0.85,
  transition: 'all 0.3s',
  '&:hover': {
    opacity: 1,
    borderColor: 'rgba(0, 229, 255, 0.35)',
    bgcolor: 'rgba(0, 229, 255, 0.06)',
    boxShadow: '0 0 16px rgba(0, 229, 255, 0.08)',
  },
};

const pillTextSx = {
  fontSize: '11px',
  letterSpacing: '1.5px',
  textTransform: 'uppercase',
  color: 'primary.main',
  fontWeight: 500,
};

export default function DashboardPage() {
  const navigate = useNavigate();
  const uploadDialog = useContext(UploadDialogContext);
  const countsQuery = useCounts();
  const qualityQuery = useQualitySummary();

  const { entries: activityEntries, isIdle, activeCount, progress, totalCostUsd } = useActivityLog(10);

  const stats: DashboardStats = useMemo(() => {
    const counts = countsQuery.data;
    const quality = qualityQuery.data;
    const entityCount = counts?.knowledge_nodes ?? 0;
    const relationCount = counts?.links ?? 0;
    const sourceCount = counts?.sources ?? 0;
    return {
      entityCount,
      relationCount,
      sourceCount,
      templateCount: counts?.templates ?? 0,
      lensCount: counts?.lenses ?? 0,
      workflowCount: counts?.workflows ?? 0,
      qualityScore: quality ? Math.round(quality.avg_quality_grade) : null,
      avgRelations: computeAvgRelations(entityCount, relationCount),
      density: computeDensity(entityCount, relationCount),
      edgesPerSource: computeEdgesPerSource(sourceCount, relationCount),
    };
  }, [countsQuery.data, qualityQuery.data]);

  const loading = countsQuery.isPending || qualityQuery.isPending;

  if (loading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: 'calc(100vh - 64px)',
          bgcolor: ChaosCypherBackground.dark.default
        }}>
        <CircularProgress sx={{ color: ChaosCypherPalette.primary }} />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        width: '100%',
        height: 'calc(100vh - 64px)',
        position: 'relative',
        overflow: 'hidden',
        p: { xs: '16px', sm: '24px 32px', md: '32px 48px' },
        display: 'flex',
        flexDirection: 'column',
        bgcolor: ChaosCypherBackground.dark.default,
      }}
    >
      {/* === GRAPH BACKGROUND === */}
      <DashboardGraph />

      {/* === Add source button === */}
      <ButtonBase
        onClick={() => uploadDialog?.openUploadDialog()}
        aria-label="Add source"
        sx={{ ...pillSx, left: 48 }}
      >
        <Typography sx={pillTextSx}>+ Add Source</Typography>
      </ButtonBase>

      {/* === Explore graph button === */}
      <ButtonBase
        onClick={() => navigate('/graph')}
        aria-label="Explore graph"
        sx={{ ...pillSx, right: 48 }}
      >
        <Typography sx={pillTextSx}>Explore Graph &rarr;</Typography>
      </ButtonBase>

      {/* === CONTENT OVERLAY === */}
      <Box
        sx={{
          position: 'relative',
          zIndex: 1,
          display: 'flex',
          flexDirection: 'column',
          flex: 1,
          pointerEvents: 'none',
          '& > *': { pointerEvents: 'auto' },
        }}
      >
        {/* === TOP-RIGHT: System Health Cluster === */}
        <SystemHealthCluster
          isIdle={isIdle}
          activeCount={activeCount}
          totalCostUsd={totalCostUsd}
          progress={progress}
          activityEntries={activityEntries}
          graphNodeCount={stats.entityCount}
          totalNodes={stats.entityCount}
        />

        {/* === LEFT HUD === */}
        <StatsPanel stats={stats} />

      </Box>
    </Box>
  );
}
