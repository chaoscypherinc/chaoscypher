// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * General Settings Tab
 *
 * Top-level application settings including dark mode, auto-enable,
 * import/export, and TLS configuration.
 */

import { RefObject, useState } from 'react';
import {
  Box,
  Typography,
  FormControlLabel,
  Switch,
  Divider,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  TextField,
  Button,
  Chip,
  CircularProgress,
} from '@mui/material';
import LockIcon from '@mui/icons-material/Lock';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import type { Settings } from '../../types';
import { accordionSummarySx, accordionBtnSx, accentAccordionSx } from '../../theme/settings';
import { ACCENT_COLORS } from '../../theme/accentStyles';
import { getApiErrorMessage } from '../../utils/errors';
import { ghostSwitchSx } from '../../theme/ghostStyles';
import { useTlsStatus, useToggleTls } from './hooks/useTlsStatus';
import AccountAccordion from './AccountAccordion';
import ApiKeysAccordion from './ApiKeysAccordion';
import ImportExportSection from './ImportExportSection';
import NetworkAccessAccordion from './NetworkAccessAccordion';

interface GeneralSettingsTabProps {
  settings: Settings;
  setSettings: (settings: Settings) => void;
  importing: boolean;
  exporting: boolean;
  importSuccess: boolean;
  importError: string | null;
  setImportError: (error: string | null) => void;
  fileInputRef: RefObject<HTMLInputElement | null>;
  handleExport: () => Promise<void>;
  handleImport: (event: React.ChangeEvent<HTMLInputElement>) => Promise<void>;
  exportOptions: {
    includeTemplates: boolean;
    includeKnowledge: boolean;
    includeLenses: boolean;
    includeWorkflows: boolean;
    includeSources: boolean;
    includeEmbeddings: boolean;
  };
  setExportOptions: (options: {
    includeTemplates: boolean;
    includeKnowledge: boolean;
    includeLenses: boolean;
    includeWorkflows: boolean;
    includeSources: boolean;
    includeEmbeddings: boolean;
  }) => void;
  /**
   * Deep-link target from the user dropdown (`?section=`). Opens and scrolls
   * to the matching account/API-keys accordion on mount.
   */
  focusSection?: 'account' | 'api-keys' | null;
}

export default function GeneralSettingsTab({
  settings,
  setSettings,
  importing,
  exporting,
  importSuccess,
  importError,
  setImportError,
  fileInputRef,
  handleExport,
  handleImport,
  exportOptions,
  setExportOptions,
  focusSection = null,
}: GeneralSettingsTabProps) {
  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h6" gutterBottom>
        General Settings
      </Typography>
      {/* App Settings */}
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mb: 4 }}>
        <FormControlLabel
          control={
            <Switch
              checked={settings.dark_mode}
              onChange={(e) =>
                setSettings({ ...settings, dark_mode: e.target.checked })
              }
              sx={ghostSwitchSx}
            />
          }
          label={
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <DarkModeIcon sx={{ fontSize: 18 }} color="primary" />
              Dark Mode (requires page reload after save)
            </Box>
          }
        />
        <FormControlLabel
          control={
            <Switch
              checked={settings.auto_enable}
              onChange={(e) =>
                setSettings({ ...settings, auto_enable: e.target.checked })
              }
              sx={ghostSwitchSx}
            />
          }
          label={
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <AutoFixHighIcon sx={{ fontSize: 18 }} color="primary" />
              Auto-enable imported sources
            </Box>
          }
        />
        <Typography
          variant="body2"
          sx={{
            color: "text.secondary",
            ml: 4,
            mt: -1
          }}>
          When enabled, imported sources are automatically visible in the knowledge graph and searchable.
        </Typography>
      </Box>

      <Divider sx={{ my: 3 }} />
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2
        }}>
        {/* Account — change password / username (deep-linked from the user dropdown) */}
        <AccountAccordion autoFocus={focusSection === 'account'} />

        {/* API keys — CLI / script credentials (deep-linked from the user dropdown) */}
        <ApiKeysAccordion autoFocus={focusSection === 'api-keys'} />

        {/* Import & Export */}
        <ImportExportSection
          settings={settings}
          setSettings={setSettings}
          importing={importing}
          exporting={exporting}
          importSuccess={importSuccess}
          importError={importError}
          setImportError={setImportError}
          fileInputRef={fileInputRef}
          handleExport={handleExport}
          handleImport={handleImport}
          exportOptions={exportOptions}
          setExportOptions={setExportOptions}
        />

        {/* Network access — host allow-list + external-access toggle */}
        <NetworkAccessAccordion settings={settings} setSettings={setSettings} />

        {/* TLS Settings (auth-enabled only) */}
        <TLSAccordion />
      </Box>
    </Box>
  );
}

/** Inline TLS accordion for the General Settings tab. */
function TLSAccordion() {
  // `undefined` until the status query resolves (or if it errors — TLS config
  // is optional, so a failed fetch silently shows the "Unknown" state).
  const { data: tlsEnabledData } = useTlsStatus();
  const tlsEnabled = tlsEnabledData ?? null;
  const toggleTls = useToggleTls();
  const toggling = toggleTls.isPending;
  const [hostname, setHostname] = useState('');
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const handleToggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setMessage(null);
    try {
      if (tlsEnabled) {
        await toggleTls.mutateAsync({ enable: false });
        setMessage({ type: 'success', text: 'TLS disabled. Restart the container for changes to take effect.' });
      } else {
        await toggleTls.mutateAsync({ enable: true, hostname: hostname.trim() || undefined });
        setMessage({ type: 'success', text: 'Self-signed TLS enabled. Restart the container for changes to take effect.' });
      }
    } catch (err) {
      setMessage({ type: 'error', text: getApiErrorMessage(err) || 'Failed to toggle TLS' });
    }
  };

  return (
    <Accordion sx={accentAccordionSx('domain')}>
      <AccordionSummary
        expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.domain }} />}
        sx={accordionSummarySx}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
          <LockIcon sx={{ fontSize: 18, color: ACCENT_COLORS.domain }} />
          <Typography variant="subtitle2" sx={{
            fontWeight: "medium"
          }}>
            TLS / HTTPS
          </Typography>
          {tlsEnabled !== null && (
            <Chip
              size="small"
              label={tlsEnabled ? 'Enabled' : 'Disabled'}
              color={tlsEnabled ? 'success' : 'default'}
              variant="outlined"
              sx={{ height: 20, fontSize: '0.7rem' }}
            />
          )}
          {tlsEnabled !== null && (
            <Button
              size="small"
              variant="outlined"
              color="warning"
              onClick={handleToggle}
              disabled={toggling}
              sx={{ ...accordionBtnSx, ml: 'auto' }}
            >
              {toggling ? <CircularProgress size={14} /> : tlsEnabled ? 'Disable' : 'Enable'}
            </Button>
          )}
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        {message && (
          <Alert severity={message.type} sx={{ mb: 2 }} onClose={() => setMessage(null)}>
            {message.text}
          </Alert>
        )}

        {!tlsEnabled && (
          <TextField
            label="Hostname (optional)"
            variant="outlined"
            size="small"
            fullWidth
            value={hostname}
            onChange={(e) => setHostname(e.target.value)}
            placeholder="e.g., chaoscypher.local"
            helperText="Used for the self-signed certificate CN. Leave empty for server default."
            sx={{ mb: 2 }}
          />
        )}

        <Alert severity="info" sx={{ mb: 0 }}>
          <Typography variant="body2" sx={{ mb: 1 }}>
            <strong>Self-signed certificates</strong> will trigger browser security warnings on first visit.
          </Typography>
          <Typography variant="body2">
            <strong>Custom certificates:</strong> Replace the files in the data volume at{' '}
            <code>data/certs/server.crt</code> and <code>data/certs/server.key</code>{' '}
            with your own (e.g., from Let&apos;s Encrypt), then restart the container.
          </Typography>
        </Alert>
      </AccordionDetails>
    </Accordion>
  );
}
