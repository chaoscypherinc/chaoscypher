// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared utilities for Quality scoring components.
 *
 * These utilities provide consistent color coding and formatting
 * for quality scores across the application.
 */

import { formatCompactNumber } from '../../utils/formatters';
import { QualityColors } from '../../theme/colors';

/**
 * Color palette for quality grades.
 */
const GRADE_COLORS = {
  Outstanding: QualityColors.grades.outstanding,
  Excellent: QualityColors.grades.excellent,
  Good: QualityColors.grades.good,
  Fair: QualityColors.grades.fair,
  Low: QualityColors.grades.low,
} as const;

/**
 * Outstanding grade visual constants.
 *
 * Gradient runs from bright gold to deep amber for a pronounced shimmer.
 * Border uses a dark brown-gold to contrast the warm gradient.
 */
const OUTSTANDING_GRADIENT = QualityColors.outstandingGradient;
/**
 * Get a CSS background for a grade — gradient for Outstanding, solid for others.
 *
 * @param label - Grade label
 * @param grade - Numeric grade (0-100)
 * @returns Object with `background` or `bgcolor` key for MUI sx spread
 */
export function getGradeBackground(label?: string, grade?: number): string | undefined {
  const resolvedLabel = label || (grade !== undefined ? getLabelFromGrade(grade) : undefined);
  if (resolvedLabel === 'Outstanding') return OUTSTANDING_GRADIENT;
  return undefined;
}

/**
 * Color palette for quality score sections.
 *
 * Each section has a distinct color to help users visually identify
 * different components of the quality calculation.
 */
export const SECTION_COLORS = QualityColors.sections;

/**
 * Format richness scores with K suffix for large numbers.
 *
 * @param score - The richness score to format
 * @returns Formatted string (e.g., "1.5K" or "250")
 */
export function formatRichness(score: number): string {
  if (score >= 10000) return formatCompactNumber(score, 0);
  if (score >= 1000) return formatCompactNumber(score);
  return score.toFixed(0);
}

/**
 * Get the color for a grade label or numeric grade.
 *
 * @param label - Optional grade label (Excellent, Good, Fair, Low)
 * @param grade - Optional numeric grade (0-100)
 * @returns Hex color string
 */
export function getGradeColor(label?: string, grade?: number): string {
  if (label && label in GRADE_COLORS) {
    return GRADE_COLORS[label as keyof typeof GRADE_COLORS];
  }
  if (grade !== undefined) {
    if (grade >= 85) return GRADE_COLORS.Outstanding;
    if (grade >= 70) return GRADE_COLORS.Excellent;
    if (grade >= 50) return GRADE_COLORS.Good;
    if (grade >= 30) return GRADE_COLORS.Fair;
    return GRADE_COLORS.Low;
  }
  return QualityColors.defaultGray;
}

/**
 * Get the grade label from a numeric grade.
 *
 * @param grade - Numeric grade (0-100)
 * @returns Grade label string
 */
export function getLabelFromGrade(grade: number): string {
  if (grade >= 85) return 'Outstanding';
  if (grade >= 70) return 'Excellent';
  if (grade >= 50) return 'Good';
  if (grade >= 30) return 'Fair';
  return 'Low';
}

