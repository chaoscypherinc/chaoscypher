// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * MaintenancePage — shown when a database upgrade is pending or blocked.
 *
 * The top-level App effect redirects here when /api/v1/upgrade/pending
 * reports ready=false. This page lists the pending migrations with a
 * plain-language description and offers Apply + Rollback buttons so
 * self-hosted users never need a terminal to finish an upgrade.
 */

import { useEffect } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  CssBaseline,
  Stack,
  ThemeProvider,
  Typography,
  alpha,
  createTheme,
} from '@mui/material';

import {
  usePendingUpgrades,
  useApplyUpgrades,
  useRollbackUpgrade,
} from '../services/api/useMaintenance';
import { ChaosCypherPalette, ChaosCypherBackground, ChaosCypherNeutrals } from '../theme/palette';
import { getComponentOverrides } from '../theme/componentOverrides';

const TIER_COLOR: Record<string, 'success' | 'warning' | 'error'> = {
  safe_auto: 'success',
  needs_confirmation: 'warning',
  manual: 'error',
};

// Maintenance mode always uses the dark palette — during an upgrade the
// app can't rely on the user's saved dark_mode setting (settings fetch is
// gated behind the upgrade check), and the light splash looks broken
// against the operator-facing "something's happening" framing.
const MAINTENANCE_THEME = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: ChaosCypherPalette.primary },
    secondary: { main: ChaosCypherPalette.secondary },
    error: { main: ChaosCypherPalette.error },
    warning: { main: ChaosCypherPalette.warning },
    info: { main: ChaosCypherPalette.info },
    success: { main: ChaosCypherPalette.success },
    background: ChaosCypherBackground.dark,
    text: {
      primary: ChaosCypherNeutrals.textPrimary,
      secondary: ChaosCypherNeutrals.textSecondary,
      disabled: ChaosCypherNeutrals.textTertiary,
    },
    divider: alpha(ChaosCypherNeutrals.borderDivider, 0.4),
  },
  components: getComponentOverrides(),
});

export function MaintenancePage() {
  const pending = usePendingUpgrades();
  const data = pending.data;
  const isReady = data?.ready ?? false;

  const applyUpgrades = useApplyUpgrades();
  const rollbackUpgrade = useRollbackUpgrade();

  // While showing pending migrations, poll so the page auto-redirects
  // if the upgrade is applied out-of-band (CLI, another tab). Polling stops
  // once the upgrade state goes ready (the effect below bounces away).
  const shouldPoll = pending.isSuccess && !isReady;
  useEffect(() => {
    if (!shouldPoll) return;
    const interval = window.setInterval(() => void pending.refetch(), 3000);
    return () => window.clearInterval(interval);
  }, [shouldPoll, pending]);

  // Bounce back to the app once the upgrade state goes ready.
  useEffect(() => {
    if (isReady) {
      window.location.href = '/';
    }
  }, [isReady]);

  const onApply = () => {
    applyUpgrades.mutate(undefined, {
      onSuccess: () => {
        window.location.href = '/';
      },
    });
  };

  const onRollback = () => {
    rollbackUpgrade.mutate(undefined, {
      onSuccess: () => {
        window.location.href = '/';
      },
    });
  };

  // Transient phases first — a mutation in flight takes priority over the
  // pending-list render so the operator sees the spinner while it runs.
  if (applyUpgrades.isPending || rollbackUpgrade.isPending) {
    const label = applyUpgrades.isPending ? 'Applying upgrade…' : 'Rolling back…';
    return (
      <CenteredContainer>
        <Stack spacing={2} sx={{ alignItems: 'center' }}>
          <CircularProgress />
          <Typography>{label}</Typography>
          <Typography variant="body2" color="text.secondary">
            This may take a few seconds for small databases, longer for large ones.
          </Typography>
        </Stack>
      </CenteredContainer>
    );
  }

  // Surface a mutation failure or the initial pending-check failure. A mutation
  // error wins because it's the most recent operator action.
  const errorMessage =
    applyUpgrades.error != null
      ? String(applyUpgrades.error)
      : rollbackUpgrade.error != null
        ? String(rollbackUpgrade.error)
        : pending.isError
          ? String(pending.error)
          : null;

  if (errorMessage) {
    return (
      <CenteredContainer>
        <Card sx={{ maxWidth: 600, width: '100%' }}>
          <CardContent>
            <Alert severity="error" sx={{ mb: 2 }}>
              Upgrade check failed
            </Alert>
            <Typography variant="body2" sx={{ fontFamily: 'monospace', mb: 2 }}>
              {errorMessage}
            </Typography>
            <Button
              variant="contained"
              onClick={() => {
                applyUpgrades.reset();
                rollbackUpgrade.reset();
                void pending.refetch();
              }}
            >
              Retry
            </Button>
          </CardContent>
        </Card>
      </CenteredContainer>
    );
  }

  if (pending.isLoading || !data) {
    return (
      <CenteredContainer>
        <Stack spacing={2} sx={{ alignItems: 'center' }}>
          <CircularProgress />
          <Typography>Checking upgrade state…</Typography>
        </Stack>
      </CenteredContainer>
    );
  }

  if (data.ready) {
    // Bounce handled by effect; render nothing while it fires.
    return null;
  }

  return (
    <CenteredContainer>
      <Card sx={{ maxWidth: 720, width: '100%' }}>
        <CardContent>
          <Typography variant="h4" gutterBottom>
            Database upgrade required
          </Typography>
          {data.message && (
            <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
              {data.message}
            </Typography>
          )}

          <Typography variant="h6" sx={{ mb: 1 }}>
            Pending migrations
          </Typography>
          <Stack spacing={1} sx={{ mb: 3 }}>
            {data.blocked_on.map((m) => (
              <Card key={m.revision} variant="outlined">
                <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
                  <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                    <Chip
                      label={m.tier}
                      size="small"
                      color={TIER_COLOR[m.tier] ?? 'default'}
                    />
                    <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                      {m.revision}
                    </Typography>
                  </Stack>
                  <Typography variant="body1" sx={{ mt: 0.5 }}>
                    {m.description}
                  </Typography>
                </CardContent>
              </Card>
            ))}
          </Stack>

          {data.last_backup && (
            <Alert severity="info" sx={{ mb: 3 }}>
              A pre-upgrade backup is saved at{' '}
              <code style={{ fontSize: '0.9em' }}>{data.last_backup}</code>. If
              something goes wrong during the upgrade, you can restore it.
            </Alert>
          )}

          <Stack direction="row" spacing={2}>
            <Button
              variant="contained"
              color="primary"
              size="large"
              onClick={onApply}
            >
              Apply upgrade
            </Button>
            {data.last_backup && (
              <Button
                variant="outlined"
                color="warning"
                size="large"
                onClick={onRollback}
              >
                Roll back to pre-upgrade backup
              </Button>
            )}
          </Stack>
        </CardContent>
      </Card>
    </CenteredContainer>
  );
}

function CenteredContainer({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider theme={MAINTENANCE_THEME}>
      <CssBaseline />
      <InnerCentered>{children}</InnerCentered>
    </ThemeProvider>
  );
}

function InnerCentered({ children }: { children: React.ReactNode }) {
  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        p: 2,
      }}
    >
      {children}
    </Box>
  );
}
