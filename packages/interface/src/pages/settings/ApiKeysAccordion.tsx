// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ApiKeysAccordion — list / create / revoke personal API keys, as a
 * collapsible section of the Settings > General tab.
 *
 * Keys are for programmatic use (CLI, scripts). The plaintext is returned by
 * `POST /auth/keys` ONCE — we surface it in a dedicated dialog with a copy
 * button. Subsequent list calls only return metadata.
 *
 * When `autoFocus` is set (deep-linked from the user dropdown via
 * `?section=api-keys`) the accordion opens and scrolls itself into view.
 */

import { useEffect, useRef, useState } from 'react';
import {
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Box,
  Typography,
  Button,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
  Tooltip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Alert,
  CircularProgress,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import AddIcon from '@mui/icons-material/Add';
import CopyIcon from '@mui/icons-material/ContentCopy';
import DeleteIcon from '@mui/icons-material/Delete';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import KeyIcon from '@mui/icons-material/Key';
import { type ApiKeyInfo, type ApiKeyCreatedResponse } from '../../services/api/auth';
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from '../../services/api/useApiKeys';
import { getApiErrorMessage } from '../../utils/errors';
import { accentAccordionSx, accordionSummarySx } from '../../theme/settings';
import { ACCENT_COLORS } from '../../theme/accentStyles';

function formatDate(iso: string | null | undefined): string {
  if (!iso) return 'Never';
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return iso;
  }
}

interface ApiKeysAccordionProps {
  /** Open + scroll into view on mount (deep-link from the user dropdown). */
  autoFocus?: boolean;
}

export default function ApiKeysAccordion({ autoFocus = false }: ApiKeysAccordionProps) {
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

  const { data: keys = [], isLoading: loading, error: loadError } = useApiKeys();
  const createKey = useCreateApiKey();
  const revokeKey = useRevokeApiKey();

  // Action-error state for create/revoke failures. Load failures surface
  // through the query's own error, merged below.
  const [actionError, setActionError] = useState<string | null>(null);
  const [errorDismissed, setErrorDismissed] = useState(false);

  // Create dialog state
  const [createOpen, setCreateOpen] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const creating = createKey.isPending;

  // Post-create dialog state (shows the plaintext key exactly once)
  const [createdKey, setCreatedKey] = useState<ApiKeyCreatedResponse | null>(null);
  const [keyCopied, setKeyCopied] = useState(false);
  const keyCopiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Revoke dialog state
  const [revokeTarget, setRevokeTarget] = useState<ApiKeyInfo | null>(null);
  const revoking = revokeKey.isPending;

  // Surface the load error and any action error through one Alert, matching
  // the pre-migration single-`error` slot. A manual dismiss hides it until the
  // next failure.
  const loadErrorMessage = loadError
    ? getApiErrorMessage(loadError) || 'Failed to load API keys'
    : null;
  const error = errorDismissed ? null : actionError ?? loadErrorMessage;

  const dismissError = () => {
    setActionError(null);
    setErrorDismissed(true);
  };

  useEffect(() => {
    return () => {
      if (keyCopiedTimer.current) clearTimeout(keyCopiedTimer.current);
    };
  }, []);

  const handleCreate = async () => {
    const name = newKeyName.trim();
    if (!name) return;
    setErrorDismissed(false);
    setActionError(null);
    try {
      const result = await createKey.mutateAsync(name);
      setCreatedKey(result);
      setCreateOpen(false);
      setNewKeyName('');
    } catch (err) {
      setActionError(getApiErrorMessage(err) || 'Failed to create API key');
    }
  };

  const handleCopyKey = async () => {
    if (!createdKey) return;
    try {
      await navigator.clipboard.writeText(createdKey.key);
      setKeyCopied(true);
      if (keyCopiedTimer.current) clearTimeout(keyCopiedTimer.current);
      keyCopiedTimer.current = setTimeout(() => setKeyCopied(false), 2000);
    } catch {
      // Clipboard blocked — the user can select the text manually.
    }
  };

  const handleRevoke = async () => {
    if (!revokeTarget) return;
    setErrorDismissed(false);
    setActionError(null);
    try {
      await revokeKey.mutateAsync(revokeTarget.id);
      setRevokeTarget(null);
    } catch (err) {
      setActionError(getApiErrorMessage(err) || 'Failed to revoke API key');
    }
  };

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
          <KeyIcon sx={{ fontSize: 18, color: ACCENT_COLORS.domain }} />
          <Typography variant="subtitle2" sx={{ fontWeight: 'medium' }}>
            API keys
          </Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        <Box
          sx={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 2,
            justifyContent: 'space-between',
            alignItems: { xs: 'flex-start', sm: 'center' },
            mb: 2,
          }}
        >
          <Typography variant="body2" sx={{ color: 'text.secondary', flex: 1, minWidth: 240 }}>
            Create keys for CLI tools and scripts. Plaintext keys are shown once
            at creation time and never stored after that.
          </Typography>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setCreateOpen(true)}
          >
            New key
          </Button>
        </Box>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={dismissError}>
            {error}
          </Alert>
        )}

        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
            <CircularProgress />
          </Box>
        ) : keys.length === 0 ? (
          <Paper variant="outlined" sx={{ p: 4, textAlign: 'center' }}>
            <Typography sx={{ color: 'text.secondary' }}>
              No API keys yet. Create one to authenticate CLI and script access.
            </Typography>
          </Paper>
        ) : (
          <TableContainer component={Paper} variant="outlined">
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Created</TableCell>
                  <TableCell>Last used</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {keys.map((key) => (
                  <TableRow key={key.id} hover>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {key.name}
                      </Typography>
                    </TableCell>
                    <TableCell>{formatDate(key.created_at)}</TableCell>
                    <TableCell>{formatDate(key.last_used_at)}</TableCell>
                    <TableCell align="right">
                      <Tooltip title="Revoke key">
                        <IconButton
                          size="small"
                          color="error"
                          onClick={() => setRevokeTarget(key)}
                          aria-label={`Revoke ${key.name}`}
                        >
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}

        {/* Create key dialog */}
        <Dialog
          open={createOpen}
          onClose={() => (!creating ? setCreateOpen(false) : undefined)}
          maxWidth="xs"
          fullWidth
        >
          <DialogTitle>Create API key</DialogTitle>
          <DialogContent>
            <TextField
              autoFocus
              margin="dense"
              label="Key name"
              placeholder="e.g. CI pipeline, laptop CLI"
              fullWidth
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              helperText="A label to help you remember what uses this key."
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setCreateOpen(false)} disabled={creating}>
              Cancel
            </Button>
            <Button
              variant="contained"
              onClick={handleCreate}
              disabled={creating || !newKeyName.trim()}
            >
              {creating ? 'Creating…' : 'Create'}
            </Button>
          </DialogActions>
        </Dialog>

        {/* Plaintext key reveal — shown exactly once */}
        <Dialog
          open={!!createdKey}
          maxWidth="sm"
          fullWidth
          onClose={(_event, reason) => {
            // Don't dismiss accidentally — the user MUST acknowledge via the
            // "I've saved it" button. This is the only chance to copy the key.
            if (reason === 'backdropClick' || reason === 'escapeKeyDown') return;
            setCreatedKey(null);
            setKeyCopied(false);
          }}
        >
          <DialogTitle>API key created</DialogTitle>
          <DialogContent>
            <Alert severity="warning" sx={{ mb: 2 }}>
              Save this key now. It will not be shown again. If you lose it
              you'll need to revoke and create a new one.
            </Alert>
            {createdKey && (
              <>
                <Typography variant="subtitle2" gutterBottom>
                  {createdKey.name}
                </Typography>
                <Paper
                  variant="outlined"
                  sx={{
                    p: 2,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1,
                    wordBreak: 'break-all',
                  }}
                >
                  <Typography
                    variant="body2"
                    sx={{ fontFamily: 'monospace', flex: 1, fontSize: '0.85rem' }}
                  >
                    {createdKey.key}
                  </Typography>
                  <Tooltip title={keyCopied ? 'Copied!' : 'Copy to clipboard'}>
                    <IconButton aria-label={keyCopied ? 'Copied!' : 'Copy to clipboard'} onClick={handleCopyKey} size="small">
                      {keyCopied ? (
                        <CheckCircleIcon color="success" />
                      ) : (
                        <CopyIcon />
                      )}
                    </IconButton>
                  </Tooltip>
                </Paper>
              </>
            )}
          </DialogContent>
          <DialogActions>
            <Button
              variant="contained"
              onClick={() => {
                setCreatedKey(null);
                setKeyCopied(false);
              }}
            >
              I've saved it
            </Button>
          </DialogActions>
        </Dialog>

        {/* Revoke confirmation */}
        <Dialog
          open={!!revokeTarget}
          onClose={() => (!revoking ? setRevokeTarget(null) : undefined)}
          maxWidth="xs"
          fullWidth
        >
          <DialogTitle>Revoke API key</DialogTitle>
          <DialogContent>
            <Typography>
              Revoke <strong>{revokeTarget?.name}</strong>? Any script using
              this key will stop working immediately.
            </Typography>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setRevokeTarget(null)} disabled={revoking}>
              Cancel
            </Button>
            <Button
              variant="contained"
              color="error"
              onClick={handleRevoke}
              disabled={revoking}
            >
              {revoking ? 'Revoking…' : 'Revoke'}
            </Button>
          </DialogActions>
        </Dialog>
      </AccordionDetails>
    </Accordion>
  );
}
