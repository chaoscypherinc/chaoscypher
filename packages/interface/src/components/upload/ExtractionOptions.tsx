// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module ExtractionOptions
 * Advanced extraction settings accordion, organized into two labeled groups:
 *   - EXTRACTION — filtering mode, content filtering, extract entities (master),
 *     quick analysis. Filtering mode / content filtering / quick analysis
 *     collapse away when entity extraction is off.
 *   - PROCESSING — vision, text normalization, skip-duplicate-files (preparing
 *     the document and handling the upload).
 *
 * The domain selector is promoted to the parent dialog (see DomainSelect) and
 * is no longer rendered here.
 */

import type { ReactNode } from 'react';
import {
  Box,
  Typography,
  FormControlLabel,
  Checkbox,
  Tooltip,
  Accordion,
  AccordionSummary,
  AccordionDetails,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { ACCENT_COLORS } from '../../theme/accentStyles';
import { accentAccordionSx, accordionSummarySx } from '../../theme/settings';
import { ChaosCypherPalette } from '../../theme/palette';
import { FilteringModeSelect } from './FilteringModeSelect';

// ── Local building blocks ──────────────────────────────────────────────────

function GroupLabel({ children }: { children: ReactNode }) {
  return (
    <Typography
      variant="caption"
      sx={{ display: 'block', letterSpacing: '0.08em', color: 'text.secondary', fontWeight: 600, mb: 0.5 }}
    >
      {children}
    </Typography>
  );
}

interface CheckRowProps {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  tooltip: string;
  color: string;
}

function CheckRow({ checked, onChange, label, tooltip, color }: CheckRowProps) {
  return (
    <Tooltip title={tooltip} placement="right" arrow>
      <FormControlLabel
        control={
          <Checkbox
            checked={checked}
            onChange={(e) => onChange(e.target.checked)}
            size="small"
            sx={{ color: 'rgba(255,255,255,0.3)', '&.Mui-checked': { color } }}
          />
        }
        label={<Typography variant="body2">{label}</Typography>}
      />
    </Tooltip>
  );
}

// ── Component ─────────────────────────────────────────────────────────────

interface ExtractionOptionsProps {
  extractEntities: boolean;
  onExtractEntitiesChange: (value: boolean) => void;
  enableVision: boolean;
  onEnableVisionChange: (value: boolean) => void;
  showNormalizationOption: boolean;
  enableNormalization: boolean;
  onNormalizationChange: (value: boolean) => void;
  analysisDepth: 'quick' | 'full';
  onAnalysisDepthChange: (value: 'quick' | 'full') => void;
  contentFiltering: boolean;
  onContentFilteringChange: (value: boolean) => void;
  filteringMode: string;
  onFilteringModeChange: (value: string) => void;
  skipDuplicates: boolean;
  onSkipDuplicatesChange: (value: boolean) => void;
}

export function ExtractionOptions({
  extractEntities,
  onExtractEntitiesChange,
  enableVision,
  onEnableVisionChange,
  showNormalizationOption,
  enableNormalization,
  onNormalizationChange,
  analysisDepth,
  onAnalysisDepthChange,
  contentFiltering,
  onContentFilteringChange,
  filteringMode,
  onFilteringModeChange,
  skipDuplicates,
  onSkipDuplicatesChange,
}: ExtractionOptionsProps) {
  return (
    <Accordion variant="outlined" sx={accentAccordionSx('info')} disableGutters>
      <AccordionSummary expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.info }} />} sx={accordionSummarySx}>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>Advanced</Typography>
      </AccordionSummary>
      <AccordionDetails>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>

          {/* EXTRACTION — building the knowledge graph (the headline choice) */}
          <Box>
            <GroupLabel>EXTRACTION</GroupLabel>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
              {extractEntities && (
                <Box sx={{ mb: 0.5 }}>
                  <FilteringModeSelect filteringMode={filteringMode} onFilteringModeChange={onFilteringModeChange} />
                </Box>
              )}
              {extractEntities && (
                <CheckRow
                  checked={contentFiltering}
                  onChange={onContentFilteringChange}
                  label="Content filtering"
                  color={ChaosCypherPalette.warning}
                  tooltip="Filters non-essential content (TOC, changelogs, legal text) from entity extraction. Filtered content is still searchable."
                />
              )}
              <CheckRow
                checked={extractEntities}
                onChange={onExtractEntitiesChange}
                label="Extract entities"
                color={ChaosCypherPalette.primary}
                tooltip="Uses AI to identify people, places, concepts, and relationships from the document and add them to the knowledge graph."
              />
              {extractEntities && (
                <CheckRow
                  checked={analysisDepth === 'quick'}
                  onChange={(value) => onAnalysisDepthChange(value ? 'quick' : 'full')}
                  label="Quick analysis"
                  color={ChaosCypherPalette.info}
                  tooltip="Fast processing for testing. Limited entity extraction and no deduplication."
                />
              )}
            </Box>
          </Box>

          {/* PROCESSING — preparing the document + upload handling */}
          <Box>
            <GroupLabel>PROCESSING</GroupLabel>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
              <CheckRow
                checked={enableVision}
                onChange={onEnableVisionChange}
                label="Vision processing"
                color={ChaosCypherPalette.secondary}
                tooltip="Uses a vision model to describe images in PDFs and image files, making visual content searchable and extractable."
              />
              {showNormalizationOption && (
                <CheckRow
                  checked={enableNormalization}
                  onChange={onNormalizationChange}
                  label="Text normalization"
                  color={ChaosCypherPalette.warning}
                  tooltip="Cleans OCR artifacts, fixes encoding issues, normalizes whitespace. May exclude some characters."
                />
              )}
              <CheckRow
                checked={skipDuplicates}
                onChange={onSkipDuplicatesChange}
                label="Skip duplicate files"
                color={ChaosCypherPalette.info}
                tooltip="If a file with identical content already exists, skip uploading and return the existing source instead of creating a duplicate."
              />
            </Box>
          </Box>

        </Box>
      </AccordionDetails>
    </Accordion>
  );
}
