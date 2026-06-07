// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * FinalGradeVisual - Visual calculation flow for final grade.
 *
 * Displays the v7 grade calculation as a visual equation:
 * (R×0.5) + (E×0.35) + (T×0.15) − Pollution − Structural = Grade
 */

import { Box, Typography, LinearProgress, alpha } from '@mui/material';
import { SECTION_COLORS, getGradeBackground } from '../utils';

interface FinalGradeVisualProps {
  /** Relationship quality score (0-100) */
  relationshipQuality: number;
  /** Entity quality score (0-100) */
  entityQuality: number;
  /** Topology score (0-100) */
  topologyScore: number;
  /** Pollution penalty (0-15, subtracted from total) */
  pollutionPenalty: number;
  /** Structural penalty (0-15, v7+; hub-skew + reciprocal-rate) */
  structuralPenalty?: number;
  /** Final grade (0-100) */
  finalGrade: number;
  /** Grade label (Excellent, Good, Fair, Low) */
  gradeLabel: string;
  /** Color for the grade */
  gradeColor: string;
}

interface CalculationBoxProps {
  value: number;
  label: string;
  positive?: boolean;
  color?: string;
}

/**
 * A single box in the calculation display.
 */
function CalculationBox({ value, label, positive = true, color }: CalculationBoxProps) {
  const displayValue = value >= 0
    ? (positive ? value.toFixed(1) : `+${value.toFixed(1)}`)
    : value.toFixed(1);

  return (
    <Box sx={{ textAlign: 'center', minWidth: 60 }}>
      <Box
        sx={{
          border: 1,
          borderColor: 'divider',
          borderRadius: 1,
          px: 1.5,
          py: 0.75,
          bgcolor: color ? alpha(color, 0.1) : 'background.paper',
          fontFamily: 'monospace',
          fontWeight: 600,
          fontSize: '0.875rem',
          color: color || 'text.primary',
        }}
      >
        {displayValue}
      </Box>
      <Typography
        variant="caption"
        sx={{
          color: "text.secondary",
          fontSize: '0.65rem',
          display: 'block',
          mt: 0.5
        }}>
        {label}
      </Typography>
    </Box>
  );
}

/**
 * Operator symbol between calculation boxes.
 */
function Operator({ symbol }: { symbol: string }) {
  return (
    <Typography
      variant="h6"
      sx={{
        color: 'text.secondary',
        mx: 0.5,
        fontWeight: 300,
        alignSelf: 'flex-start',
        mt: 0.75,
      }}
    >
      {symbol}
    </Typography>
  );
}

/**
 * Visual display of the grade calculation formula (v7).
 *
 * Shows: (R×0.5) + (E×0.35) + (T×0.15) − Pollution − Structural = Grade
 * with boxes for each component and operators between them.
 */
export function FinalGradeVisual({
  relationshipQuality,
  entityQuality,
  topologyScore,
  pollutionPenalty,
  structuralPenalty = 0,
  finalGrade,
  gradeLabel,
  gradeColor,
}: FinalGradeVisualProps) {
  const percentage = Math.min(100, Math.max(0, finalGrade));
  const gradient = getGradeBackground(gradeLabel);

  // Calculate weighted contributions (v7)
  const rContrib = relationshipQuality * 0.5;
  const eContrib = entityQuality * 0.35;
  const tContrib = topologyScore * 0.15;
  const weightedSum = rContrib + eContrib + tContrib;
  const totalPenalty = pollutionPenalty + structuralPenalty;

  return (
    <Box>
      {/* Calculation formula */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'center',
          gap: 0.5,
          mb: 2,
          flexWrap: 'wrap',
        }}
      >
        <CalculationBox
          value={rContrib}
          label="R×0.5"
          color={SECTION_COLORS.relationship}
        />
        <Operator symbol="+" />
        <CalculationBox
          value={eContrib}
          label="E×0.35"
          color={SECTION_COLORS.entity}
        />
        <Operator symbol="+" />
        <CalculationBox
          value={tContrib}
          label="T×0.15"
          color={SECTION_COLORS.connectivity}
        />
        {pollutionPenalty > 0 && (
          <>
            <Operator symbol="−" />
            <CalculationBox
              value={pollutionPenalty}
              label="Pollution"
              color={SECTION_COLORS.penalty}
            />
          </>
        )}
        {structuralPenalty > 0 && (
          <>
            <Operator symbol="−" />
            <CalculationBox
              value={structuralPenalty}
              label="Structural"
              color={SECTION_COLORS.penalty}
            />
          </>
        )}
        <Operator symbol="=" />
        <CalculationBox
          value={finalGrade}
          label="Grade"
          color={gradeColor}
        />
      </Box>
      {/* Formula explanation */}
      <Typography
        variant="caption"
        sx={{
          color: "text.secondary",
          display: 'block',
          textAlign: 'center',
          mb: 1.5
        }}>
        ({rContrib.toFixed(1)}) + ({eContrib.toFixed(1)}) + ({tContrib.toFixed(1)})
        {pollutionPenalty > 0 ? ` − ${pollutionPenalty.toFixed(0)}` : ''}
        {structuralPenalty > 0 ? ` − ${structuralPenalty.toFixed(0)}` : ''}
        {' '}= {weightedSum.toFixed(1)}
        {totalPenalty > 0 ? ` → ${finalGrade.toFixed(0)}` : ''}
      </Typography>
      {/* Progress bar */}
      <Box sx={{ position: 'relative' }}>
        <LinearProgress
          variant="determinate"
          value={percentage}
          sx={{
            height: 12,
            borderRadius: 1,
            bgcolor: alpha(gradeColor, 0.15),
            '& .MuiLinearProgress-bar': {
              borderRadius: 1,
              ...(gradient
                ? { background: gradient }
                : { bgcolor: gradeColor }),
              transition: 'transform 0.4s ease',
            },
          }}
        />
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            mt: 0.5,
          }}
        >
          <Typography variant="caption" sx={{
            color: "text.secondary"
          }}>
            0
          </Typography>
          <Typography
            variant="caption"
            sx={{
              fontWeight: 600,
              color: gradeColor
            }}>
            {finalGrade.toFixed(0)}/100 {gradeLabel}
          </Typography>
          <Typography variant="caption" sx={{
            color: "text.secondary"
          }}>
            100
          </Typography>
        </Box>
      </Box>
    </Box>
  );
}
