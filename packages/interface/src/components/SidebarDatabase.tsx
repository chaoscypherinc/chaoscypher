// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Divider,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  TextField,
  Typography,
  alpha,
} from '@mui/material';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { Database, Check, Plus } from 'lucide-react';
import { useState, useRef, useCallback, useEffect } from 'react';
import type { DatabaseInfo } from '../types/database';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';

interface SidebarDatabaseProps {
  databases: DatabaseInfo[];
  currentDatabase: string;
  onSwitch: (name: string) => void;
  onCreate: (name: string) => Promise<void>;
}

/**
 * Database selector shown in the AppBar.
 * Hover opens a glass dropdown to switch databases or create a new one.
 */
export default function SidebarDatabase({
  databases,
  currentDatabase,
  onSwitch,
  onCreate,
}: SidebarDatabaseProps) {
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (leaveTimer.current) clearTimeout(leaveTimer.current);
    };
  }, []);

  const handleMouseEnter = useCallback((event: React.MouseEvent<HTMLElement>) => {
    if (leaveTimer.current) clearTimeout(leaveTimer.current);
    setAnchorEl(event.currentTarget);
  }, []);

  const handleMouseLeave = useCallback(() => {
    leaveTimer.current = setTimeout(() => setAnchorEl(null), 300);
  }, []);

  const handlePaperEnter = useCallback(() => {
    if (leaveTimer.current) clearTimeout(leaveTimer.current);
  }, []);

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleSwitch = (name: string) => {
    handleClose();
    onSwitch(name);
  };

  const handleNewDatabase = () => {
    handleClose();
    setNewName('');
    setCreateDialogOpen(true);
  };

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      setCreating(true);
      await onCreate(name);
    } finally {
      setCreating(false);
    }
  };

  return (
    <>
      <Box
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        onClick={handleMouseEnter}
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          height: 36,
          px: 1.5,
          cursor: 'pointer',
          borderRadius: 5,
          bgcolor: 'rgba(255, 255, 255, 0.02)',
          border: '1px solid rgba(255, 255, 255, 0.08)',
          transition: 'all 0.2s ease-in-out',
          whiteSpace: 'nowrap',
          '&:hover': {
            bgcolor: 'rgba(255, 255, 255, 0.04)',
            borderColor: 'rgba(255, 255, 255, 0.15)',
          },
        }}
      >
        <Database size={14} strokeWidth={1.5} style={{ color: 'rgba(255, 255, 255, 0.3)' }} />
        <Typography
          sx={{
            fontFamily: "'SF Mono', 'Cascadia Code', monospace",
            fontWeight: 400,
            fontSize: '0.8rem',
            color: 'rgba(255, 255, 255, 0.3)',
            display: { xs: 'none', md: 'block' },
          }}
          noWrap
        >
          {currentDatabase}
        </Typography>
        <ArrowDropDownIcon sx={{ fontSize: 16, color: 'rgba(255, 255, 255, 0.2)', ml: -0.5 }} />
      </Box>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={handleClose}
        autoFocus={false}
        disableAutoFocusItem
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        sx={{
          pointerEvents: 'none',
          '& .MuiPaper-root': { pointerEvents: 'auto' },
        }}
        slotProps={{
          paper: {
            onMouseEnter: handlePaperEnter,
            onMouseLeave: handleMouseLeave,
            sx: {
              mt: 0.5,
              minWidth: 200,
              backgroundColor: 'rgba(5, 5, 10, 0.65) !important',
              backgroundImage: 'none',
              backdropFilter: 'blur(16px)',
              WebkitBackdropFilter: 'blur(16px)',
              border: '1px solid rgba(0, 229, 255, 0.1)',
              boxShadow: '0 12px 40px rgba(0,0,0,0.4)',
            },
          },
        }}
      >
        {/* Database header */}
        <Box sx={{ px: 2, py: 1, borderBottom: '1px solid rgba(255, 255, 255, 0.06)' }}>
          <Typography sx={{ fontSize: '0.7rem', color: alpha('#fff', 0.19), letterSpacing: '1.5px', textTransform: 'uppercase' }}>
            Databases
          </Typography>
        </Box>

        {databases.map((db) => (
          <MenuItem
            key={db.name}
            onClick={() => handleSwitch(db.name)}
            sx={{
              py: 0.75,
              px: 2,
              minHeight: 'auto',
              transition: 'all 0.15s',
              '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.04)' },
            }}
          >
            <ListItemIcon sx={{ minWidth: 24 }}>
              {db.name === currentDatabase ? (
                <Check size={14} strokeWidth={1.5} style={{ color: ChaosCypherPalette.primary }} />
              ) : (
                <Database size={14} strokeWidth={1.5} style={{ color: alpha('#fff', 0.15) }} />
              )}
            </ListItemIcon>
            <ListItemText
              primary={db.name}
              slotProps={{
                primary: {
                  sx: {
                    fontSize: '0.8rem',
                    fontWeight: db.name === currentDatabase ? 600 : 400,
                    color: db.name === currentDatabase ? ChaosCypherPalette.primary : undefined,
                  },
                },
              }}
            />
          </MenuItem>
        ))}

        <Divider sx={{ my: 0.5, borderColor: 'rgba(255, 255, 255, 0.06)' }} />

        <MenuItem
          onClick={handleNewDatabase}
          sx={{
            py: 0.75,
            px: 2,
            minHeight: 'auto',
            transition: 'all 0.15s',
            '&:hover': { bgcolor: 'rgba(255, 0, 128, 0.05)' },
          }}
        >
          <ListItemIcon sx={{ minWidth: 24 }}>
            <Plus size={14} strokeWidth={1.5} style={{ color: ChaosCypherPalette.secondary }} />
          </ListItemIcon>
          <ListItemText
            primary="New Database"
            slotProps={{ primary: { sx: { fontSize: '0.8rem', fontWeight: 600, color: ChaosCypherPalette.secondary } } }}
          />
        </MenuItem>
      </Menu>
      <Dialog
        open={createDialogOpen}
        onClose={() => setCreateDialogOpen(false)}
        maxWidth="xs"
        fullWidth
        slotProps={{
          paper: { sx: ghostDialogPaperSx }
        }}
      >
        <DialogTitle>Create Database</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            label="Database name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleCreate(); }}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateDialogOpen(false)} sx={ghostCancelBtnSx}>Cancel</Button>
          <Button
            onClick={handleCreate}
            variant="outlined"
            sx={ghostButtonSx(ChaosCypherPalette.primary)}
            disabled={!newName.trim() || creating}
          >
            {creating ? 'Creating...' : 'Create & Switch'}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
