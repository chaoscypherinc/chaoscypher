// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * NetworkAccessAccordion — Settings-page control for the host allow-list.
 *
 * Mirrors the wizard's Account-step network section. Changes hit the
 * existing `PATCH /api/v1/settings` path via the parent's `setSettings`,
 * and the backend middleware re-reads the allow-list each request so the
 * toggle takes effect immediately — no restart needed.
 */

import {
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Autocomplete,
  Box,
  Chip,
  FormControlLabel,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import LanIcon from '@mui/icons-material/Lan';
import type { Settings } from '../../types';
import { accordionSummarySx, accentAccordionSx } from '../../theme/settings';
import { ACCENT_COLORS } from '../../theme/accentStyles';
import { ghostSwitchSx } from '../../theme/ghostStyles';

interface NetworkAccessAccordionProps {
  settings: Settings;
  setSettings: (settings: Settings) => void;
}

const LOOPBACK_DEFAULTS = ['localhost', '127.0.0.1', '::1'];

export default function NetworkAccessAccordion({
  settings,
  setSettings,
}: NetworkAccessAccordionProps) {
  const sec = settings.security ?? {
    allow_external_access: false,
    allowed_hosts: LOOPBACK_DEFAULTS,
  };

  const setExternal = (value: boolean) =>
    setSettings({
      ...settings,
      security: { ...sec, allow_external_access: value },
    });

  const setHosts = (hosts: string[]) =>
    setSettings({
      ...settings,
      security: { ...sec, allowed_hosts: hosts },
    });

  return (
    <Accordion sx={accentAccordionSx('domain')}>
      <AccordionSummary
        expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.domain }} />}
        sx={accordionSummarySx}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
          <LanIcon sx={{ fontSize: 18, color: ACCENT_COLORS.domain }} />
          <Typography variant="subtitle2" sx={{ fontWeight: 'medium' }}>
            Network access
          </Typography>
          <Chip
            size="small"
            label={sec.allow_external_access ? 'External enabled' : 'Loopback only'}
            color={sec.allow_external_access ? 'warning' : 'default'}
            variant="outlined"
            sx={{ height: 20, fontSize: '0.7rem' }}
          />
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        <FormControlLabel
          control={
            <Switch
              checked={sec.allow_external_access}
              onChange={(e) => setExternal(e.target.checked)}
              sx={ghostSwitchSx}
              slotProps={{
                input: { 'aria-label': 'Allow access from any host' },
              }}
            />
          }
          label="Allow access from any host on the network"
        />
        <Typography variant="body2" sx={{ color: 'text.secondary', ml: 6, mt: -0.5, mb: 2 }}>
          DNS-rebinding protection is disabled when on. Changes apply immediately
          — no restart needed.
        </Typography>

        <Box sx={{ opacity: sec.allow_external_access ? 0.5 : 1 }}>
          <Typography variant="body2" sx={{ mb: 1, fontWeight: 500 }}>
            Manual host allow-list
          </Typography>
          {sec.allow_external_access && (
            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mb: 1 }}>
              Allow-list is bypassed while external access is on — kept for
              when you turn it off again.
            </Typography>
          )}
          <Autocomplete<string, true, false, true>
            multiple
            freeSolo
            options={[]}
            value={sec.allowed_hosts}
            onChange={(_, value) => setHosts(value)}
            renderValue={(value, getItemProps) =>
              value.map((host, index) => {
                const { key, ...itemProps } = getItemProps({ index });
                return <Chip key={key} label={host} size="small" {...itemProps} />;
              })
            }
            renderInput={(params) => (
              <TextField
                {...params}
                placeholder="Add host (e.g. 192.168.1.20)"
                variant="outlined"
                size="small"
              />
            )}
          />
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}
