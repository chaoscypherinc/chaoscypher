// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * LexiconAuthStatus — Login/logout controls for the Lexicon package registry.
 */
import { Box, Button, Chip, CircularProgress, Tooltip } from '@mui/material';
import Person from '@mui/icons-material/Person';
import Login from '@mui/icons-material/Login';
import Logout from '@mui/icons-material/Logout';
import { ghostButtonSx, ghostCancelBtnSx } from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';
import type { LexiconAuthStatus as AuthStatusType } from '../../types/lexicon';

interface LexiconAuthStatusProps {
  authStatus: AuthStatusType | null;
  loading: boolean;
  onLogin: () => void;
  onLogout: () => void;
}

export function LexiconAuthStatus({
  authStatus,
  loading,
  onLogin,
  onLogout,
}: LexiconAuthStatusProps) {
  if (loading) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <CircularProgress size={20} sx={{ color: 'primary.main' }} />
      </Box>
    );
  }

  if (authStatus?.authenticated) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Tooltip title="Logged in to Lexicon">
          <Chip
            icon={<Person />}
            label={authStatus.username || 'Authenticated'}
            color="success"
            size="small"
            variant="outlined"
          />
        </Tooltip>
        <Tooltip title="Logout">
          <Button
            size="small"
            variant="outlined"
            onClick={onLogout}
            startIcon={<Logout />}
            sx={{ height: 32, ...ghostCancelBtnSx, borderColor: 'rgba(255, 255, 255, 0.12)', border: '1px solid' }}
          >
            Logout
          </Button>
        </Tooltip>
      </Box>
    );
  }

  return (
    <Tooltip title="Login to Lexicon to import packages">
      <Button
        size="small"
        variant="outlined"
        onClick={onLogin}
        startIcon={<Login />}
        sx={{ height: 40, ...ghostButtonSx(ChaosCypherPalette.primary) }}
      >
        Login to Lexicon
      </Button>
    </Tooltip>
  );
}
