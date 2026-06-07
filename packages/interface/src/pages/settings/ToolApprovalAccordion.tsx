// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ToolApprovalAccordion — chat tool-call approval policy, as a collapsible
 * section of the Settings > Models tab.
 *
 * Collapsed by default with the current mode shown on a chip in the summary,
 * mirroring the at-a-glance pattern of the Network access / TLS accordions.
 *
 * The `chat` group is optional on the Settings type because older cortex
 * deployments may not surface it via GET /settings yet. We fall back to
 * `"never-ask"` for display and PATCH a fresh `chat` object on save so the
 * backend can accept partial updates.
 */

import { useState } from 'react';
import {
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Box,
  Chip,
  Typography,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import VerifiedUserIcon from '@mui/icons-material/VerifiedUser';
import type { Settings } from '../../types';
import { accentAccordionSx, accordionSummarySx } from '../../theme/settings';
import { ACCENT_COLORS } from '../../theme/accentStyles';

type ToolApproval = 'always-ask' | 'ask-on-write' | 'never-ask';

/** Short chip label + color per mode (full labels live in the Select below). */
const CHIP_META: Record<ToolApproval, { label: string; color: 'warning' | 'default' }> = {
  // Full autopilot has no guardrails — flag it like external network access.
  'never-ask': { label: 'Never ask', color: 'warning' },
  'ask-on-write': { label: 'Ask on write', color: 'default' },
  'always-ask': { label: 'Always ask', color: 'default' },
};

interface ToolApprovalAccordionProps {
  settings: Settings;
  setSettings: (settings: Settings) => void;
}

export default function ToolApprovalAccordion({ settings, setSettings }: ToolApprovalAccordionProps) {
  const [expanded, setExpanded] = useState(false);
  const current = (settings.chat?.tool_approval ?? 'never-ask') as ToolApproval;
  const chip = CHIP_META[current] ?? CHIP_META['never-ask'];

  const handleChange = (value: ToolApproval) => {
    setSettings({
      ...settings,
      chat: {
        ...(settings.chat ?? {}),
        tool_approval: value,
      },
    });
  };

  return (
    <Accordion
      expanded={expanded}
      onChange={(_, isExpanded) => setExpanded(isExpanded)}
      sx={accentAccordionSx('domain')}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.domain }} />}
        sx={accordionSummarySx}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
          <VerifiedUserIcon sx={{ fontSize: 18, color: ACCENT_COLORS.domain }} />
          <Typography variant="subtitle2" sx={{ fontWeight: 'medium' }}>
            Tool call approval
          </Typography>
          <Chip
            size="small"
            label={chip.label}
            color={chip.color}
            variant="outlined"
            sx={{ height: 20, fontSize: '0.7rem' }}
          />
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        <Typography variant="body2" sx={{ color: 'text.secondary', mb: 2 }}>
          Controls when the chat will ask you to approve a tool call.
          &quot;Mutating tools&quot; are ones that create, update, or delete graph data.
        </Typography>
        <FormControl size="small" sx={{ maxWidth: 420 }} fullWidth>
          <InputLabel id="tool-approval-label">Approval mode</InputLabel>
          <Select
            labelId="tool-approval-label"
            label="Approval mode"
            value={current}
            onChange={(e) => handleChange(e.target.value as ToolApproval)}
          >
            <MenuItem value="never-ask">Never ask (full autopilot)</MenuItem>
            <MenuItem value="ask-on-write">Ask before mutating tools</MenuItem>
            <MenuItem value="always-ask">Always ask</MenuItem>
          </Select>
        </FormControl>
      </AccordionDetails>
    </Accordion>
  );
}
