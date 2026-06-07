// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Tooltip,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import type { ExtractionTaskStats } from '../../../../types';
import { UnifiedPromptDisplay } from '../OverviewTab/accordions/UnifiedPromptDisplay';

interface PromptsSectionProps {
  stats: ExtractionTaskStats | null;
}

const PROMPTS_TOOLTIP =
  'The exact system prompt and two-pass extraction prompts (entities first, ' +
  'then relationships) sent to the model for every chunk.';

/**
 * AI prompts as a collapsed-by-default accordion pinned at the bottom of the
 * Chunks tab. Closed on load keeps the tab clean; operators expand it to
 * inspect the exact system / two-pass extraction prompts. Content lives in a
 * bounded scroll area so a long system prompt doesn't stretch the page. Hidden
 * entirely when the source has no prompt data.
 */
export function PromptsSection({ stats }: PromptsSectionProps) {
  const [expanded, setExpanded] = useState(false);

  const hasSystem = Boolean(stats?.system_prompt);
  const hasUser = Boolean(
    stats?.user_instructions_template ||
      stats?.user_instructions ||
      stats?.relationship_instructions,
  );
  if (!stats || (!hasSystem && !hasUser)) return null;

  return (
    <Accordion
      expanded={expanded}
      onChange={(_, isExpanded) => setExpanded(isExpanded)}
      disableGutters
      sx={{ mt: 2, bgcolor: 'rgba(255,255,255,0.02)' }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        {/* describeChild keeps "AI prompts" as the button's accessible name and
            attaches the explanation as a description rather than overriding it. */}
        <Tooltip title={PROMPTS_TOOLTIP} arrow describeChild>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, cursor: 'help' }}>
            <SmartToyIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
            <Typography variant="subtitle2">AI prompts</Typography>
          </Box>
        </Tooltip>
      </AccordionSummary>
      <AccordionDetails>
        {/* Only mount the (potentially large) prompt DOM once opened — keeps
            the tab light on load and the collapsed state genuinely empty. */}
        {expanded && (
          <Box
            sx={{
              bgcolor: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 1,
              p: 1.5,
              maxHeight: 320,
              overflow: 'auto',
            }}
          >
            <UnifiedPromptDisplay stats={stats} />
          </Box>
        )}
      </AccordionDetails>
    </Accordion>
  );
}
