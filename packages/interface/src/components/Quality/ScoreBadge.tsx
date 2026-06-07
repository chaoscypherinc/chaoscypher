// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography, Tooltip } from '@mui/material';
import { alpha } from '@mui/material/styles';
import StarIcon from '@mui/icons-material/Star';
import { getGradeColor, getLabelFromGrade, formatRichness } from './utils';

interface ScoreBadgeProps {
  score: number;                    // Richness score (total_score)
  qualityGrade?: number;            // Quality grade 0-100
  qualityLabel?: string;            // Quality label: Excellent/Good/Fair/Low
  size?: 'small' | 'medium' | 'large';
  showLabel?: boolean;
  showIcon?: boolean;
  avgEntityQuality?: number;
  avgRelationshipQuality?: number;
  connectivityRatio?: number;
}

export function ScoreBadge({
  score,
  qualityGrade,
  qualityLabel,
  size = 'medium',
  showLabel = false,
  showIcon = false,
  avgEntityQuality,
  avgRelationshipQuality,
  connectivityRatio,
}: ScoreBadgeProps) {
  const sizeMap = {
    small: { height: 24, fontSize: '0.75rem', labelSize: '0.6rem', iconSize: 14 },
    medium: { height: 32, fontSize: '0.875rem', labelSize: '0.65rem', iconSize: 16 },
    large: { height: 40, fontSize: '1rem', labelSize: '0.75rem', iconSize: 18 },
  };

  const { height, fontSize, labelSize, iconSize } = sizeMap[size];

  // Determine display values
  const hasQualityGrade = qualityGrade !== undefined;
  const displayLabel = qualityLabel || (hasQualityGrade ? getLabelFromGrade(qualityGrade!) : undefined);
  const color = getGradeColor(displayLabel, qualityGrade);

  const tooltipContent = (
    <Box sx={{ p: 0.5 }}>
      {hasQualityGrade && (
        <>
          <Typography
            variant="caption"
            sx={{
              display: "block",
              fontWeight: 600
            }}>
            Quality Grade: {qualityGrade.toFixed(0)} ({displayLabel})
          </Typography>
          <Typography
            variant="caption"
            sx={{
              display: "block",
              color: "text.secondary",
              mt: 0.5
            }}>
            Richness: {formatRichness(score)}
          </Typography>
        </>
      )}
      {!hasQualityGrade && (
        <Typography
          variant="caption"
          sx={{
            display: "block",
            fontWeight: 600
          }}>
          Richness Score: {formatRichness(score)}
        </Typography>
      )}
      {avgEntityQuality !== undefined && (
        <Typography
          variant="caption"
          sx={{
            display: "block",
            mt: 0.5
          }}>
          Avg Entity: {avgEntityQuality.toFixed(1)}
        </Typography>
      )}
      {avgRelationshipQuality !== undefined && (
        <Typography variant="caption" sx={{
          display: "block"
        }}>
          Avg Relationship: {avgRelationshipQuality.toFixed(1)}
        </Typography>
      )}
      {connectivityRatio !== undefined && (
        <Typography variant="caption" sx={{
          display: "block"
        }}>
          Connectivity: {(connectivityRatio * 100).toFixed(0)}%
        </Typography>
      )}
    </Box>
  );

  return (
    <Tooltip title={tooltipContent} arrow placement="top">
      <Box
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          height,
          px: 1,
          borderRadius: 1,
          bgcolor: 'transparent',
          border: `1px solid ${alpha(color, 0.45)}`,
          cursor: 'default',
          gap: 0.5,
        }}
      >
        {/* Quality grade display */}
        {hasQualityGrade ? (
          <>
            {showIcon && (
              <StarIcon sx={{ fontSize: iconSize, color }} />
            )}
            <Typography
              sx={{
                fontSize,
                fontWeight: 600,
                color,
                lineHeight: 1,
              }}
            >
              {qualityGrade.toFixed(0)}
            </Typography>
            {showLabel && displayLabel && (
              <Typography
                sx={{
                  fontSize: labelSize,
                  color: 'text.secondary',
                }}
              >
                {displayLabel}
              </Typography>
            )}
          </>
        ) : (
          // Legacy: show richness score only
          (<>
            {showIcon && (
              <StarIcon sx={{ fontSize: iconSize, color }} />
            )}
            <Typography
            sx={{
              fontSize,
              fontWeight: 600,
              color,
              lineHeight: 1,
            }}
          >
            {formatRichness(score)}
          </Typography>
          </>)
        )}
      </Box>
    </Tooltip>
  );
}
