// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * AccountStep — first-run account creation + network-access decision.
 *
 * Submits the credential immediately so steps 1-3 of the wizard can use
 * auth-gated registry endpoints. The network-access selection is bubbled
 * up to the wizard via onComplete; the wizard seeds it into the working
 * Settings draft so it's persisted alongside the rest on Finish.
 */

import { useEffect, useRef, useState, type FormEvent } from 'react';
import {
  Box,
  TextField,
  Button,
  Alert,
  CircularProgress,
  InputAdornment,
  IconButton,
  Typography,
  FormControlLabel,
  Switch,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Chip,
  Autocomplete,
} from '@mui/material';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { authApi } from '../../services/api/auth';
import { settingsApi } from '../../services/api/settings';
import { useAccessHint } from '../../services/api/useSetup';
import { useAuth } from '../../contexts/useAuth';
import { ghostButtonSx, ghostSwitchSx } from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';
import { getApiErrorMessage } from '../../utils/errors';
import { logger } from '../../utils/logger';

const MIN_USERNAME_LEN = 3;
const MIN_PASSWORD_LEN = 8;
const LOOPBACK_DEFAULTS = ['localhost', '127.0.0.1', '::1'];

export interface AccountStepNetworkSelection {
  allow_external_access: boolean;
  allowed_hosts: string[];
}

interface AccountStepProps {
  /** Called once the credential is created and the cookie is live. */
  onComplete: (network: AccountStepNetworkSelection) => void;
}

export default function AccountStep({ onComplete }: AccountStepProps) {
  const { completeSetup } = useAuth();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [allowExternal, setAllowExternal] = useState<boolean | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [allowedHosts, setAllowedHosts] = useState<string[]>(LOOPBACK_DEFAULTS);

  // Server hint for the network-access default. The query is server state;
  // the switch + host list below are editable form state seeded from it once,
  // so toggling the switch after load doesn't get clobbered by a refetch.
  const { data: accessHint, isError: accessHintError, error: accessHintErr } = useAccessHint();
  const seeded = useRef(false);
  useEffect(() => {
    if (seeded.current) return;
    if (accessHint) {
      seeded.current = true;
      setAllowExternal(!accessHint.is_loopback);
      if (!accessHint.is_loopback && accessHint.request_host) {
        setAllowedHosts((prev) =>
          prev.includes(accessHint.request_host) ? prev : [...prev, accessHint.request_host],
        );
      }
    } else if (accessHintError) {
      seeded.current = true;
      logger.warn('access hint fetch failed; defaulting allowExternal to false', accessHintErr);
      setAllowExternal(false);
    }
  }, [accessHint, accessHintError, accessHintErr]);

  const trimmedUsername = username.trim();
  const usernameValid = trimmedUsername.length >= MIN_USERNAME_LEN;
  const passwordValid = password.length >= MIN_PASSWORD_LEN;
  const passwordsMatch = password.length > 0 && password === confirmPassword;
  const formValid =
    usernameValid && passwordValid && passwordsMatch && allowExternal !== null;

  const showMismatch = confirmPassword.length > 0 && !passwordsMatch;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!formValid || submitting || allowExternal === null) return;

    setError(null);
    setSubmitting(true);
    try {
      await authApi.setup(trimmedUsername, password);
      await completeSetup();
      // Persist the network-access choice immediately, before bubbling up
      // to the wizard. Earlier versions routed this through the wizard's
      // `working` draft and PATCH-on-Finish, but that path was racy across
      // the auth → settings-context handoff and could silently drop the
      // slice. Patching here is unambiguous: the slice is live before the
      // user even sees step 1.
      try {
        await settingsApi.update({
          security: {
            allow_external_access: allowExternal,
            allowed_hosts: allowedHosts,
          },
        });
      } catch (err) {
        logger.warn('Failed to persist network-access selection from wizard.', err);
      }
      onComplete({
        allow_external_access: allowExternal,
        allowed_hosts: allowedHosts,
      });
    } catch (err) {
      setError(getApiErrorMessage(err) || 'Setup failed. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box
      component="form"
      onSubmit={handleSubmit}
      sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}
    >
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <TextField
        label="Username"
        variant="outlined"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        fullWidth
        required
        autoFocus
        autoComplete="username"
        helperText={`At least ${MIN_USERNAME_LEN} characters`}
        error={trimmedUsername.length > 0 && !usernameValid}
      />
      <TextField
        label="Password"
        variant="outlined"
        type={showPassword ? 'text' : 'password'}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        fullWidth
        required
        autoComplete="new-password"
        helperText={`At least ${MIN_PASSWORD_LEN} characters`}
        error={password.length > 0 && !passwordValid}
        slotProps={{
          input: {
            endAdornment: (
              <InputAdornment position="end">
                <IconButton
                  onClick={() => setShowPassword((v) => !v)}
                  edge="end"
                  size="small"
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  tabIndex={-1}
                >
                  {showPassword ? <VisibilityOffIcon /> : <VisibilityIcon />}
                </IconButton>
              </InputAdornment>
            ),
          },
        }}
      />
      <TextField
        label="Confirm Password"
        variant="outlined"
        type={showPassword ? 'text' : 'password'}
        value={confirmPassword}
        onChange={(e) => setConfirmPassword(e.target.value)}
        fullWidth
        required
        autoComplete="new-password"
        error={showMismatch}
        helperText={showMismatch ? "Passwords don't match" : ' '}
      />

      <Box sx={{ mt: 1 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Network access
        </Typography>
        <FormControlLabel
          control={
            <Switch
              checked={allowExternal ?? false}
              onChange={(e) => setAllowExternal(e.target.checked)}
              sx={ghostSwitchSx}
              disabled={allowExternal === null}
              slotProps={{ input: { 'aria-label': 'Allow access from other devices' } }}
            />
          }
          label="Allow access from other devices"
        />
        <Typography variant="body2" sx={{ color: 'text.secondary', ml: 6, mt: -0.5 }}>
          When off, only the machine running Chaos Cypher can reach the app.
        </Typography>
        <Accordion
          expanded={advancedOpen}
          onChange={(_, isOpen) => setAdvancedOpen(isOpen)}
          sx={{ mt: 1, boxShadow: 'none', '&:before': { display: 'none' } }}
        >
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="body2">
              Advanced: allow specific hosts only
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Typography variant="body2" sx={{ color: 'text.secondary', mb: 1 }}>
              Add LAN hostnames or IPs. Has no effect while "Allow access from
              other devices" is on (which bypasses the list entirely).
            </Typography>
            <Autocomplete<string, true, false, true>
              multiple
              freeSolo
              options={[] as string[]}
              value={allowedHosts}
              onChange={(_, value) => setAllowedHosts(value as string[])}
              renderValue={(value, getItemProps) =>
                value.map((host, index) => {
                  const { key, ...tagProps } = getItemProps({ index });
                  return <Chip key={key} label={host} size="small" {...tagProps} />;
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
          </AccordionDetails>
        </Accordion>
      </Box>

      <Button
        type="submit"
        variant="outlined"
        size="large"
        fullWidth
        disabled={!formValid || submitting}
        sx={ghostButtonSx(ChaosCypherPalette.primary)}
      >
        {submitting ? <CircularProgress size={24} color="inherit" /> : 'Create Account'}
      </Button>
    </Box>
  );
}
