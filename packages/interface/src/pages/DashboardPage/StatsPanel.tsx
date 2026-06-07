// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography, Tooltip } from '@mui/material';
import { ChaosCypherPalette } from '../../theme/palette';
import BulletChart from './BulletChart';
import {
  AVG_REL_BANDS,
  DENSITY_BANDS,
  QUALITY_BANDS,
  classify,
} from './utils/bulletBands';
import type { DashboardStats } from './types';

/** Purple — same accent we used for Density during brainstorming. */
const DENSITY_COLOR = '#B388FF';

/** Props for the StatsPanel on the left side of the dashboard. */
interface StatsPanelProps {
  stats: DashboardStats;
}

/** Left-side HUD panel: entity/relationship counts, health rings, and secondary stats. */
export default function StatsPanel({ stats }: StatsPanelProps) {
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        gap: '6px',
        position: 'relative',
        '&::before': {
          content: '""',
          position: 'absolute',
          top: -40,
          left: -80,
          width: 520,
          height: 480,
          background:
            'radial-gradient(ellipse at 25% 35%, #0A0E17dd 0%, #0A0E17bb 30%, #0A0E1770 55%, transparent 75%)',
          pointerEvents: 'none',
          zIndex: -1,
        },
      }}
    >
      {/* Primary: Entities */}
      <Tooltip
        title={
          <>
            <strong>{stats.entityCount.toLocaleString()} knowledge graph entities</strong>
            <br />
            Extracted from {stats.sourceCount} source{stats.sourceCount !== 1 ? 's' : ''} across {stats.templateCount} template type{stats.templateCount !== 1 ? 's' : ''}
          </>
        }
        arrow
        placement="bottom-start"
      >
        <Box sx={{ cursor: 'default', width: 'fit-content' }}>
          <Typography
            sx={{
              fontSize: 11,
              letterSpacing: '5px',
              textTransform: 'uppercase',
              color: ChaosCypherPalette.primary,
              opacity: 0.5,
              mb: '4px',
            }}
          >
            Entities
          </Typography>
          <Typography
            sx={{
              fontSize: 'clamp(3rem, 6vw, 5.5rem)',
              fontWeight: 100,
              lineHeight: 1,
              letterSpacing: '-2px',
              color: ChaosCypherPalette.primary,
              textShadow: `0 0 50px ${ChaosCypherPalette.primary}18`,
            }}
          >
            {stats.entityCount.toLocaleString()}
          </Typography>
        </Box>
      </Tooltip>

      {/* Primary: Relationships */}
      <Tooltip
        title={
          <>
            <strong>{stats.relationCount.toLocaleString()} edges</strong> connecting entities
            <br />
            {stats.avgRelations.toFixed(1)} relationships per entity on average
          </>
        }
        arrow
        placement="bottom-start"
      >
        <Box sx={{ cursor: 'default', width: 'fit-content' }}>
          <Typography
            sx={{
              fontSize: 11,
              letterSpacing: '5px',
              textTransform: 'uppercase',
              color: ChaosCypherPalette.secondary,
              opacity: 0.5,
              mb: '4px',
            }}
          >
            Relationships
          </Typography>
          <Typography
            sx={{
              fontSize: 'clamp(3rem, 6vw, 5.5rem)',
              fontWeight: 100,
              lineHeight: 1,
              letterSpacing: '-2px',
              color: ChaosCypherPalette.secondary,
              textShadow: `0 0 50px ${ChaosCypherPalette.secondary}18`,
            }}
          >
            {stats.relationCount.toLocaleString()}
          </Typography>
        </Box>
      </Tooltip>

      {/* Bullet chart cluster — Quality / Density / Avg Rel */}
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: '10px', my: '24px', maxWidth: 380 }}>
        <BulletChart
          label="Quality"
          value={stats.qualityScore ?? 0}
          config={QUALITY_BANDS}
          color={ChaosCypherPalette.primary}
          tooltip={
            <>
              <strong>Quality: {stats.qualityScore ?? 0} / 100 · {classify(stats.qualityScore ?? 0, QUALITY_BANDS).label}</strong>
              <br />
              Average extraction quality across all sources. Bands: 0–50 poor · 50–75 ok · 75–100 good.
            </>
          }
        />
        <BulletChart
          label="Density"
          value={stats.density}
          displayValue={`${stats.density.toFixed(1)}%`}
          config={DENSITY_BANDS}
          color={DENSITY_COLOR}
          tooltip={
            <>
              <strong>Graph density: {stats.density.toFixed(2)}% · {classify(stats.density, DENSITY_BANDS).label}</strong>
              <br />
              Fraction of possible directed edges that exist (m / n × (n−1)).
              Bands: &lt;0.1% sparse · 0.1–1% typical · 1–5% dense.
              For reference: Wikidata ~10⁻⁹%, DBpedia ~10⁻⁸%, OpenCyc 0.14%.
            </>
          }
        />
        <BulletChart
          label="Avg Rel"
          value={stats.avgRelations}
          displayValue={stats.avgRelations.toFixed(1)}
          config={AVG_REL_BANDS}
          color={ChaosCypherPalette.secondary}
          tooltip={
            <>
              <strong>{stats.avgRelations.toFixed(1)} relationships per entity · {classify(stats.avgRelations, AVG_REL_BANDS).label}</strong>
              <br />
              Bands: &lt;3 sparse · 3–10 moderate · 10+ heavy.
              For reference: Wikidata 6.4, DBpedia 21, OpenCyc 59.
            </>
          }
        />
      </Box>

      {/* Secondary stats */}
      <Box sx={{ display: 'flex', gap: '24px', alignItems: 'baseline', mb: '30px', flexWrap: 'wrap' }}>
        {[
          {
            label: 'Sources',
            value: String(stats.sourceCount),
            tooltip: `${stats.sourceCount} document${stats.sourceCount !== 1 ? 's' : ''} imported — PDFs, web pages, and other files processed into the knowledge graph`,
          },
          {
            label: 'Templates',
            value: String(stats.templateCount),
            tooltip: `${stats.templateCount} entity type${stats.templateCount !== 1 ? 's' : ''} — the schema defining what kinds of entities exist in your graph`,
          },
          {
            label: 'Edges/Source',
            value: stats.edgesPerSource.toFixed(1),
            tooltip: `${stats.edgesPerSource.toFixed(1)} relationships extracted per source on average — extraction yield across your corpus`,
          },
        ].map((stat) => (
          <Tooltip key={stat.label} title={stat.tooltip} arrow placement="bottom-start">
            <Box sx={{ cursor: 'default' }}>
              <Typography
                sx={{
                  fontSize: '8px',
                  letterSpacing: '2px',
                  textTransform: 'uppercase',
                  opacity: 0.25,
                  mb: '2px',
                }}
              >
                {stat.label}
              </Typography>
              <Typography
                sx={{
                  fontSize: 20,
                  fontWeight: 300,
                  opacity: 0.7,
                }}
              >
                {stat.value}
              </Typography>
            </Box>
          </Tooltip>
        ))}
      </Box>
    </Box>
  );
}
