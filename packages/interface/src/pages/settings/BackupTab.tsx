// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Alert,
  Switch,
  FormControlLabel,
  Select,
  MenuItem,
  TextField,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
  Tooltip,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Checkbox,
} from '@mui/material';
import BackupIcon from '@mui/icons-material/Backup';
import DownloadIcon from '@mui/icons-material/Download';
import RestoreIcon from '@mui/icons-material/SettingsBackupRestore';
import DeleteIcon from '@mui/icons-material/Delete';
import WarningIcon from '@mui/icons-material/Warning';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import {
  useBackups,
  useCreateBackup,
  useRestoreBackup,
  useDeleteBackup,
  useDownloadBackup,
} from './hooks/useBackups';
import type { Settings } from '../../types';
import {
  ghostButtonSx,
  ghostCancelBtnSx,
  ghostSuccessAlertSx,
  ghostErrorAlertSx,
  ghostInfoAlertSx,
  ghostSwitchSx,
  ghostDialogPaperSx,
  ghostInputSx,
} from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';

interface BackupTabProps {
  settings: Settings;
  setSettings: (settings: Settings) => void;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${Math.round((bytes / Math.pow(k, i)) * 100) / 100} ${sizes[i]}`;
}

function formatDate(dateStr: string): string {
  // Backend returns "YYYYMMDD_HHMMSS"
  const match = dateStr.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/);
  if (!match) return dateStr;
  const [, y, mo, d, h, mi, s] = match;
  const date = new Date(`${y}-${mo}-${d}T${h}:${mi}:${s}`);
  return date.toLocaleString();
}

export default function BackupTab({ settings, setSettings }: BackupTabProps) {
  const { data: backups = [], isLoading: loading, isError: listError } = useBackups();
  const createBackup = useCreateBackup();
  const restoreBackup = useRestoreBackup();
  const deleteBackup = useDeleteBackup();
  const downloadBackup = useDownloadBackup();

  const [success, setSuccess] = useState<string | null>(null);
  // Action-level error (create/download/restore/delete). The initial-list
  // load error is surfaced separately via the query's `isError`.
  const [actionError, setActionError] = useState<string | null>(null);
  const error = listError ? 'Failed to load backups' : actionError;

  const creating = createBackup.isPending;
  const restoring = restoreBackup.isPending;
  const deleting = deleteBackup.isPending;

  // Restore dialog state
  const [restoreDialog, setRestoreDialog] = useState<{
    open: boolean;
    filename: string;
  }>({ open: false, filename: '' });
  const [restoreChecked, setRestoreChecked] = useState(false);
  const [restoreText, setRestoreText] = useState('');

  // Delete dialog state
  const [deleteDialog, setDeleteDialog] = useState<{
    open: boolean;
    filename: string;
  }>({ open: false, filename: '' });

  const handleCreate = async () => {
    setSuccess(null);
    setActionError(null);
    try {
      const result = await createBackup.mutateAsync();
      setSuccess(`Backup created: ${result.filename} (${formatBytes(result.size)})`);
    } catch {
      setActionError('Failed to create backup');
    }
  };

  const handleDownload = async (filename: string) => {
    setActionError(null);
    try {
      await downloadBackup.mutateAsync(filename);
    } catch {
      setActionError(`Failed to download ${filename}`);
    }
  };

  const handleRestoreConfirm = async () => {
    setActionError(null);
    const filename = restoreDialog.filename;
    try {
      const result = await restoreBackup.mutateAsync(filename);
      setSuccess(`Database restored from ${result.restored_from}. Reload the page to see changes.`);
      setRestoreDialog({ open: false, filename: '' });
      setRestoreChecked(false);
      setRestoreText('');
    } catch {
      setActionError(`Failed to restore from ${filename}`);
    }
  };

  const handleDeleteConfirm = async () => {
    setActionError(null);
    const filename = deleteDialog.filename;
    try {
      await deleteBackup.mutateAsync(filename);
      setSuccess(`Deleted ${filename}`);
      setDeleteDialog({ open: false, filename: '' });
    } catch {
      setActionError(`Failed to delete ${filename}`);
    }
  };

  const totalSize = backups.reduce((sum, b) => sum + b.size, 0);

  return (
    <Box sx={{ p: 3 }}>
      {success && (
        <Alert severity="success" sx={{ mb: 2, ...ghostSuccessAlertSx }} onClose={() => setSuccess(null)}>
          {success}
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }} onClose={() => setActionError(null)}>
          {error}
        </Alert>
      )}
      {/* Section 1: Scheduled Backups */}
      <Box sx={{
        border: '1px solid rgba(0, 229, 255, 0.15)',
        borderRadius: 1,
        p: 2,
        mb: 2.5,
        background: 'rgba(0, 229, 255, 0.02)',
      }}>
        <Typography
          variant="subtitle2"
          sx={{
            fontWeight: "medium",
            mb: 1.5,
            display: 'flex',
            alignItems: 'center',
            gap: 1
          }}>
          <BackupIcon sx={{ fontSize: 18, color: 'primary.main' }} />
          Scheduled Backups
        </Typography>
        <Box sx={{ display: 'flex', gap: 3, alignItems: 'center', flexWrap: 'wrap' }}>
          <FormControlLabel
            control={
              <Switch
                checked={settings.backup?.enabled ?? true}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    backup: { ...settings.backup, enabled: e.target.checked },
                  })
                }
                sx={ghostSwitchSx}
              />
            }
            label="Enabled"
          />
          <Box sx={{ minWidth: 140 }}>
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                display: 'block',
                mb: 0.5
              }}>
              Interval
            </Typography>
            <Select
              size="small"
              value={settings.backup?.interval ?? 'daily'}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  backup: { ...settings.backup, interval: e.target.value },
                })
              }
              fullWidth
              sx={{ bgcolor: 'rgba(0,0,0,0.2)' }}
            >
              <MenuItem value="hourly">Hourly</MenuItem>
              <MenuItem value="daily">Daily</MenuItem>
              <MenuItem value="weekly">Weekly</MenuItem>
            </Select>
          </Box>
          <Box sx={{ minWidth: 100 }}>
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                display: 'block',
                mb: 0.5
              }}>
              Keep Last
            </Typography>
            <TextField
              size="small"
              type="number"
              value={settings.backup?.retention_count ?? 7}
              onChange={(e) => {
                const val = Math.max(1, Math.min(100, parseInt(e.target.value) || 1));
                setSettings({
                  ...settings,
                  backup: { ...settings.backup, retention_count: val },
                });
              }}
              fullWidth
              sx={{ bgcolor: 'rgba(0,0,0,0.2)' }}
              slotProps={{
                htmlInput: { min: 1, max: 100 }
              }}
            />
          </Box>
        </Box>
      </Box>
      {/* Section 2: Manual Backup */}
      <Box sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 2,
        p: 2,
        mb: 2.5,
        border: '1px solid rgba(0, 229, 255, 0.1)',
        borderRadius: 1,
        background: 'rgba(0, 229, 255, 0.01)',
      }}>
        <Typography
          variant="subtitle2"
          sx={{
            fontWeight: "medium",
            display: 'flex',
            alignItems: 'center',
            gap: 1
          }}>
          <BackupIcon sx={{ fontSize: 18, color: 'primary.main' }} />
          Manual Backup
        </Typography>
        <Button
          variant="outlined"
          size="small"
          onClick={handleCreate}
          disabled={creating}
          startIcon={creating ? <CircularProgress size={14} /> : undefined}
          sx={{ ml: 'auto', ...ghostButtonSx(ChaosCypherPalette.primary) }}
        >
          {creating ? 'Creating...' : 'Create Backup Now'}
        </Button>
      </Box>
      {/* Section 3: Backup List */}
      <Box sx={{
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 1,
        overflow: 'hidden',
        mb: 2,
      }}>
        <Box sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          px: 2,
          py: 1.5,
          borderBottom: '1px solid rgba(255,255,255,0.08)',
          background: 'rgba(255,255,255,0.02)',
        }}>
          <Typography variant="subtitle2" sx={{
            fontWeight: "medium"
          }}>
            Available Backups
          </Typography>
          {backups.length > 0 && (
            <Typography variant="caption" sx={{
              color: "text.secondary"
            }}>
              ({backups.length} backup{backups.length !== 1 ? 's' : ''}, {formatBytes(totalSize)} total)
            </Typography>
          )}
        </Box>

        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
            <CircularProgress size={24} />
          </Box>
        ) : backups.length === 0 ? (
          <Box sx={{ p: 3, textAlign: 'center' }}>
            <Typography variant="body2" sx={{
              color: "text.secondary"
            }}>
              No backups yet. Create one manually or wait for the scheduled backup.
            </Typography>
          </Box>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell sx={{ color: 'text.secondary', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    Filename
                  </TableCell>
                  <TableCell sx={{ color: 'text.secondary', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    Size
                  </TableCell>
                  <TableCell sx={{ color: 'text.secondary', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    Created
                  </TableCell>
                  <TableCell align="right" sx={{ color: 'text.secondary', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    Actions
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {backups.map((backup) => (
                  <TableRow key={backup.filename} sx={{ '&:hover': { bgcolor: 'rgba(255,255,255,0.02)' } }}>
                    <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>
                      {backup.filename}
                    </TableCell>
                    <TableCell sx={{ color: 'text.secondary' }}>
                      {formatBytes(backup.size)}
                    </TableCell>
                    <TableCell sx={{ color: 'text.secondary' }}>
                      {formatDate(backup.created_at)}
                    </TableCell>
                    <TableCell align="right">
                      <Tooltip title="Download">
                        <IconButton aria-label="Download" size="small" onClick={() => handleDownload(backup.filename)} sx={{ color: 'primary.main' }}>
                          <DownloadIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Restore">
                        <IconButton
                          aria-label="Restore"
                          size="small"
                          onClick={() => setRestoreDialog({ open: true, filename: backup.filename })}
                          sx={{ color: 'warning.main' }}
                        >
                          <RestoreIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete">
                        <IconButton
                          aria-label="Delete backup"
                          size="small"
                          onClick={() => setDeleteDialog({ open: true, filename: backup.filename })}
                          sx={{ color: 'error.main' }}
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
      </Box>
      {/* Safety Warning */}
      <Alert severity="info" icon={<InfoOutlinedIcon />} sx={ghostInfoAlertSx}>
        Restoring a backup replaces the current database. A safety backup is automatically created before restore.
      </Alert>
      {/* Restore Confirmation Dialog */}
      <Dialog
        open={restoreDialog.open}
        onClose={() => {
          setRestoreDialog({ open: false, filename: '' });
          setRestoreChecked(false);
          setRestoreText('');
        }}
        maxWidth="sm"
        fullWidth
        slotProps={{
          paper: { sx: ghostDialogPaperSx }
        }}
      >
        <DialogTitle>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1
            }}>
            <WarningIcon color="warning" />
            Restore Database
          </Box>
        </DialogTitle>
        <DialogContent>
          <Typography variant="body1" gutterBottom>
            This will replace the current database with <strong>{restoreDialog.filename}</strong>.
            All current data will be overwritten.
          </Typography>
          <Alert severity="info" sx={{ my: 2, ...ghostInfoAlertSx }}>
            A safety backup of the current database will be created automatically before restoring.
          </Alert>
          <Box sx={{
            mt: 2
          }}>
            <FormControlLabel
              control={
                <Checkbox
                  checked={restoreChecked}
                  onChange={(e) => setRestoreChecked(e.target.checked)}
                />
              }
              label="I understand this will replace the current database"
            />
          </Box>
          <TextField
            fullWidth
            margin="normal"
            label='Type RESTORE to proceed'
            value={restoreText}
            onChange={(e) => setRestoreText(e.target.value)}
            placeholder="RESTORE"
            helperText='Type the word RESTORE in capital letters'
            sx={ghostInputSx}
          />
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => {
              setRestoreDialog({ open: false, filename: '' });
              setRestoreChecked(false);
              setRestoreText('');
            }}
            sx={ghostCancelBtnSx}
          >
            Cancel
          </Button>
          <Button
            onClick={handleRestoreConfirm}
            variant="outlined"
            disabled={!restoreChecked || restoreText !== 'RESTORE' || restoring}
            startIcon={restoring ? <CircularProgress size={14} /> : undefined}
            sx={ghostButtonSx(ChaosCypherPalette.warning)}
          >
            {restoring ? 'Restoring...' : 'Restore'}
          </Button>
        </DialogActions>
      </Dialog>
      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteDialog.open}
        onClose={() => setDeleteDialog({ open: false, filename: '' })}
        maxWidth="xs"
        fullWidth
        slotProps={{
          paper: { sx: ghostDialogPaperSx }
        }}
      >
        <DialogTitle>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1
            }}>
            <WarningIcon color="error" />
            Delete Backup
          </Box>
        </DialogTitle>
        <DialogContent>
          <Typography>
            Delete <strong>{deleteDialog.filename}</strong>? This cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialog({ open: false, filename: '' })} sx={ghostCancelBtnSx}>
            Cancel
          </Button>
          <Button
            onClick={handleDeleteConfirm}
            variant="outlined"
            disabled={deleting}
            startIcon={deleting ? <CircularProgress size={14} /> : undefined}
            sx={ghostButtonSx(ChaosCypherPalette.error)}
          >
            {deleting ? 'Deleting...' : 'Delete'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
