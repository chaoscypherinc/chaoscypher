// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * AccountAccordion — change password + change username, as a collapsible
 * section of the Settings > General tab.
 *
 * Two independent forms inside one accordion:
 *   - Password form POSTs `/auth/password`. The backend clears the session
 *     cookie, so we log out locally and bounce to /login after success.
 *   - Username form POSTs `/auth/username`. The backend issues a fresh
 *     session cookie, so we stay logged in.
 *
 * When `autoFocus` is set (deep-linked from the user dropdown via
 * `?section=account`) the accordion opens and scrolls itself into view.
 */

import { useEffect, useRef, useState, type FormEvent } from 'react';
import {
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Box,
  Typography,
  TextField,
  Button,
  Alert,
  CircularProgress,
  Divider,
  InputAdornment,
  IconButton,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import LockIcon from '@mui/icons-material/Lock';
import PersonIcon from '@mui/icons-material/Person';
import { useNavigate } from 'react-router';
import { authApi } from '../../services/api/auth';
import { useAuth } from '../../contexts/useAuth';
import { getApiErrorMessage } from '../../utils/errors';
import { accentAccordionSx, accordionSummarySx } from '../../theme/settings';
import { ACCENT_COLORS } from '../../theme/accentStyles';

const MIN_USERNAME_LEN = 3;
const MIN_PASSWORD_LEN = 8;

interface AccountAccordionProps {
  /** Open + scroll into view on mount (deep-link from the user dropdown). */
  autoFocus?: boolean;
}

export default function AccountAccordion({ autoFocus = false }: AccountAccordionProps) {
  const { user, logout } = useAuth();
  const ref = useRef<HTMLDivElement>(null);
  // Seed open state from the deep-link flag; the accordion mounts fresh on each
  // dropdown navigation, so no in-place expand is needed — only a scroll.
  const [expanded, setExpanded] = useState(autoFocus);

  useEffect(() => {
    if (autoFocus) {
      // Guarded: jsdom (tests) doesn't implement scrollIntoView.
      ref.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    }
  }, [autoFocus]);

  return (
    <Accordion
      ref={ref}
      expanded={expanded}
      onChange={(_, isExpanded) => setExpanded(isExpanded)}
      sx={accentAccordionSx('domain')}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.domain }} />}
        sx={accordionSummarySx}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
          <PersonIcon sx={{ fontSize: 18, color: ACCENT_COLORS.domain }} />
          <Typography variant="subtitle2" sx={{ fontWeight: 'medium' }}>
            Account
          </Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        <Typography variant="body2" sx={{ color: 'text.secondary', mb: 2 }}>
          Signed in as <strong>{user?.username ?? 'unknown'}</strong>
        </Typography>
        <PasswordForm onLogoutAfterChange={logout} />
        <Box sx={{ height: 24 }} />
        <UsernameForm />
      </AccordionDetails>
    </Accordion>
  );
}

// ========================================
// Password form
// ========================================

function PasswordForm({
  onLogoutAfterChange,
}: {
  onLogoutAfterChange: () => Promise<void>;
}) {
  const navigate = useNavigate();
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const newValid = newPassword.length >= MIN_PASSWORD_LEN;
  const matches = newPassword.length > 0 && newPassword === confirmPassword;
  const canSubmit = oldPassword.length > 0 && newValid && matches;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit || submitting) return;

    setError(null);
    setSuccess(null);
    setSubmitting(true);

    try {
      await authApi.changePassword(oldPassword, newPassword);
      setSuccess('Password changed. Signing you out…');
      setOldPassword('');
      setNewPassword('');
      setConfirmPassword('');
      // Backend has cleared the cookie already; mirror that locally and send
      // the user back to /login so they can sign in with their new password.
      await onLogoutAfterChange();
      navigate('/login', { replace: true });
    } catch (err) {
      setError(getApiErrorMessage(err) || 'Failed to change password.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
        <LockIcon fontSize="small" />
        <Typography variant="h6">Change password</Typography>
      </Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        You'll need to sign in again after changing your password.
      </Alert>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {success && (
        <Alert severity="success" sx={{ mb: 2 }}>
          {success}
        </Alert>
      )}

      <Divider sx={{ mb: 2 }} />

      <Box
        component="form"
        onSubmit={handleSubmit}
        sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}
      >
        <TextField
          label="Current password"
          type={showPw ? 'text' : 'password'}
          value={oldPassword}
          onChange={(e) => setOldPassword(e.target.value)}
          required
          autoComplete="current-password"
          fullWidth
          slotProps={{
            input: {
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton
                    onClick={() => setShowPw((v) => !v)}
                    edge="end"
                    size="small"
                    aria-label={showPw ? 'Hide password' : 'Show password'}
                    tabIndex={-1}
                  >
                    {showPw ? <VisibilityOffIcon /> : <VisibilityIcon />}
                  </IconButton>
                </InputAdornment>
              ),
            },
          }}
        />
        <TextField
          label="New password"
          type={showPw ? 'text' : 'password'}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          required
          autoComplete="new-password"
          fullWidth
          helperText={`At least ${MIN_PASSWORD_LEN} characters`}
          error={newPassword.length > 0 && !newValid}
        />
        <TextField
          label="Confirm new password"
          type={showPw ? 'text' : 'password'}
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          required
          autoComplete="new-password"
          fullWidth
          error={confirmPassword.length > 0 && !matches}
          helperText={
            confirmPassword.length > 0 && !matches ? "Passwords don't match" : ' '
          }
        />
        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button
            type="submit"
            variant="contained"
            disabled={!canSubmit || submitting}
          >
            {submitting ? <CircularProgress size={22} /> : 'Change password'}
          </Button>
        </Box>
      </Box>
    </Box>
  );
}

// ========================================
// Username form
// ========================================

function UsernameForm() {
  const { recheckSetup } = useAuth();
  const [newUsername, setNewUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const trimmed = newUsername.trim();
  const canSubmit = trimmed.length >= MIN_USERNAME_LEN && password.length > 0;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit || submitting) return;

    setError(null);
    setSuccess(null);
    setSubmitting(true);

    try {
      const result = await authApi.changeUsername(password, trimmed);
      setSuccess(`Username changed to "${result.username}".`);
      setNewUsername('');
      setPassword('');
      // Refresh cached user info — backend issued a fresh cookie.
      await recheckSetup();
    } catch (err) {
      setError(getApiErrorMessage(err) || 'Failed to change username.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
        <PersonIcon fontSize="small" />
        <Typography variant="h6">Change username</Typography>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {success && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>
          {success}
        </Alert>
      )}

      <Divider sx={{ mb: 2 }} />

      <Box
        component="form"
        onSubmit={handleSubmit}
        sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}
      >
        <TextField
          label="New username"
          value={newUsername}
          onChange={(e) => setNewUsername(e.target.value)}
          required
          autoComplete="username"
          fullWidth
          helperText={`At least ${MIN_USERNAME_LEN} characters`}
          error={trimmed.length > 0 && trimmed.length < MIN_USERNAME_LEN}
        />
        <TextField
          label="Current password"
          type={showPw ? 'text' : 'password'}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoComplete="current-password"
          fullWidth
          slotProps={{
            input: {
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton
                    onClick={() => setShowPw((v) => !v)}
                    edge="end"
                    size="small"
                    aria-label={showPw ? 'Hide password' : 'Show password'}
                    tabIndex={-1}
                  >
                    {showPw ? <VisibilityOffIcon /> : <VisibilityIcon />}
                  </IconButton>
                </InputAdornment>
              ),
            },
          }}
        />
        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button
            type="submit"
            variant="contained"
            disabled={!canSubmit || submitting}
          >
            {submitting ? <CircularProgress size={22} /> : 'Change username'}
          </Button>
        </Box>
      </Box>
    </Box>
  );
}
