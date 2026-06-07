// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * QualityTooltips: Tooltip content components for each quality metric dimension.
 *
 * Provides detailed explanations of scoring methodology, scales, weights,
 * and formulas for the quality metric cards. Each tooltip is a standalone
 * component rendered inside MUI Tooltip wrappers.
 */

import React, { memo } from 'react';
import { Box, Typography } from '@mui/material';

/** Tooltip content for Entity Quality section. */
const EntityQualityTooltipComponent: React.FC = () => (
  <Box>
    <Typography variant="body2" gutterBottom sx={{ fontWeight: 600 }}>
      Entity Quality Score (0-100)
    </Typography>
    <Typography variant="caption" gutterBottom sx={{ display: "block" }}>
      Measures the average quality of all extracted entities.
    </Typography>
    <Typography
      variant="caption"
      gutterBottom
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Scale:</strong><br />
      • 70+ Excellent - High confidence extractions<br />
      • 50-69 Good - Reliable extractions<br />
      • 30-49 Fair - May need review<br />
      • {'<'}30 Low - Consider re-extraction
    </Typography>
    <Typography
      variant="caption"
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Weight:</strong> 35% of Final Grade<br />
      <strong>Factors:</strong> extraction confidence, property completeness,
      description length, cross-chunk mentions.
    </Typography>
  </Box>
);

export const EntityQualityTooltip = memo(EntityQualityTooltipComponent);

/** Tooltip content for Relationship Quality section. */
const RelationshipQualityTooltipComponent: React.FC = () => (
  <Box>
    <Typography variant="body2" gutterBottom sx={{ fontWeight: 600 }}>
      Relationship Quality Score (0-100)
    </Typography>
    <Typography variant="caption" gutterBottom sx={{ display: "block" }}>
      Measures the average quality of entity relationships.
    </Typography>
    <Typography
      variant="caption"
      gutterBottom
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Scale:</strong><br />
      • 70+ Excellent - Well-defined relationships<br />
      • 50-69 Good - Clear relationships<br />
      • 30-49 Fair - Weak connections<br />
      • {'<'}30 Low - Unreliable relationships
    </Typography>
    <Typography
      variant="caption"
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Weight:</strong> 50% of Final Grade<br />
      <strong>Factors:</strong> relationship type clarity, endpoint quality,
      context support.
    </Typography>
  </Box>
);

export const RelationshipQualityTooltip = memo(RelationshipQualityTooltipComponent);

/** Tooltip content for Topology Score section. */
const TopologyTooltipComponent: React.FC = () => (
  <Box>
    <Typography variant="body2" gutterBottom sx={{ fontWeight: 600 }}>
      Topology Score (0-100)
    </Typography>
    <Typography variant="caption" gutterBottom sx={{ display: "block" }}>
      Measures graph structure quality via connectivity and density.
    </Typography>
    <Typography
      variant="caption"
      gutterBottom
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Components:</strong><br />
      • Connectivity: % of entities in relationships<br />
      • Density: edges per node vs target (2.5, bell-shaped)
    </Typography>
    <Typography
      variant="caption"
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Weight:</strong> 15% of Final Grade<br />
      <strong>Formula:</strong> (Connectivity + Density) / 2<br />
      <strong>Why it matters:</strong> Higher topology scores indicate a
      well-connected graph. v7 penalizes over-dense graphs so models can't
      pad the score by emitting redundant edges.
    </Typography>
  </Box>
);

export const TopologyTooltip = memo(TopologyTooltipComponent);

/** Tooltip content for Trust Safety section. */
const TrustSafetyTooltipComponent: React.FC = () => (
  <Box>
    <Typography variant="body2" gutterBottom sx={{ fontWeight: 600 }}>
      Trust Safety (v7)
    </Typography>
    <Typography variant="caption" gutterBottom sx={{ display: "block" }}>
      Combined penalty for low-quality items and graph-shape noise.
    </Typography>
    <Typography
      variant="caption"
      gutterBottom
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Pollution Penalty (0-15):</strong> items with score {'<'} 40<br />
      • 0-9%: 0 pts &nbsp; • 10-19%: -5 &nbsp; • 20-29%: -10 &nbsp; • 30%+: -15
    </Typography>
    <Typography
      variant="caption"
      gutterBottom
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Structural Penalty (0-15):</strong><br />
      • Hub skew: max_degree ÷ median_degree. When one entity is connected to
      many more things than the rest (ratio &gt; 3 with ≥10 entities), the LLM
      is usually anchoring noise on a memorable entity.<br />
      • Reciprocal rate: fraction of edges with a same-type reciprocal
      (A→B and B→A with identical type). Catches directional errors and
      duplicates the dedup stage missed.
    </Typography>
    <Typography
      variant="caption"
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Why it matters:</strong> Stops chatty models from inflating the
      grade by padding the graph with redundant or inverted edges.
    </Typography>
  </Box>
);

export const TrustSafetyTooltip = memo(TrustSafetyTooltipComponent);

/** Tooltip content for Final Grade section. */
const FinalGradeTooltipComponent: React.FC = () => (
  <Box>
    <Typography variant="body2" gutterBottom sx={{ fontWeight: 600 }}>
      Final Grade Calculation (v7)
    </Typography>
    <Typography variant="caption" gutterBottom sx={{ display: "block" }}>
      Combined quality score independent of volume.
    </Typography>
    <Typography
      variant="caption"
      gutterBottom
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Formula:</strong><br />
      Weighted = (R × 0.5) + (E × 0.35) + (T × 0.15)<br />
      Final = Weighted − Pollution − Structural
    </Typography>
    <Typography
      variant="caption"
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>R</strong> = Relationship Quality (50%)<br />
      <strong>E</strong> = Entity Quality (35%)<br />
      <strong>T</strong> = Topology Score (15%)<br />
      <strong>Pollution</strong> — low-quality item share (0-15)<br />
      <strong>Structural</strong> — hub-skew + reciprocal-rate (0-15, v7+)<br />
      Clamped to 0-100 range.
    </Typography>
  </Box>
);

export const FinalGradeTooltip = memo(FinalGradeTooltipComponent);

/** Tooltip content for Richness section. */
const RichnessTooltipComponent: React.FC = () => (
  <Box>
    <Typography variant="body2" gutterBottom sx={{ fontWeight: 600 }}>
      Richness Score (unbounded)
    </Typography>
    <Typography variant="caption" gutterBottom sx={{ display: "block" }}>
      Total extraction volume weighted by quality.
    </Typography>
    <Typography
      variant="caption"
      gutterBottom
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Components:</strong><br />
      • Entity contribution: count x quality-weighted<br />
      • Relationship contribution: count x quality-weighted<br />
      • Connectivity bonus: connected entities x 10
    </Typography>
    <Typography
      variant="caption"
      sx={{ display: "block", color: "text.secondary" }}
    >
      <strong>Note:</strong> Unlike Quality Grade, Richness grows with more
      extractions. Use it to compare extraction volume across sources.
    </Typography>
  </Box>
);

export const RichnessTooltip = memo(RichnessTooltipComponent);
