// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  alpha,
  Box,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Tooltip,
  Typography,
} from '@mui/material';
import LogoutIcon from '@mui/icons-material/Logout';
import SettingsIcon from '@mui/icons-material/Settings';
import PersonIcon from '@mui/icons-material/Person';
import KeyIcon from '@mui/icons-material/Key';
import { useState, useRef, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router';
import { useAuth } from '../contexts/useAuth';
import { ChaosCypherPalette } from '../theme/palette';

interface SidebarUserProps {
  isDrawerOpen: boolean;
  /** "sidebar" (default) renders full avatar+name row; "header" renders compact avatar only. */
  variant?: 'sidebar' | 'header';
}

/**
 * User avatar with account menu.
 * - sidebar variant: Full row with avatar + name at bottom of sidebar.
 * - header variant: Compact circle for the top-right control cluster.
 */
export default function SidebarUser({ isDrawerOpen, variant = 'sidebar' }: SidebarUserProps) {
  const { isAuthenticated, user, logout } = useAuth();
  const navigate = useNavigate();
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);

  const initial = isAuthenticated && user?.username
    ? user.username.charAt(0).toUpperCase()
    : '?';

  const handleOpen = (event: React.MouseEvent<HTMLElement>) => {
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleAccount = () => {
    handleClose();
    // Account controls now live in the General settings tab; deep-link so the
    // accordion opens and scrolls into view.
    navigate('/settings?tab=general&section=account');
  };

  const handleApiKeys = () => {
    handleClose();
    navigate('/settings?tab=general&section=api-keys');
  };

  const handleSettings = () => {
    handleClose();
    navigate('/settings');
  };

  const handleLogout = () => {
    handleClose();
    logout();
  };

  // Header variant hover handlers — must be called unconditionally (rules of hooks)
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

  if (variant === 'header') {
    return (
      <>
        <Box
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          onClick={isAuthenticated ? handleOpen : undefined}
          sx={{
            width: 32,
            height: 32,
            borderRadius: '50%',
            bgcolor: 'rgba(0, 229, 255, 0.06)',
            border: '1px solid rgba(0, 229, 255, 0.2)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '0.75rem',
            fontWeight: 600,
            color: 'primary.main',
            opacity: 0.7,
            cursor: 'pointer',
            flexShrink: 0,
            transition: 'all 0.2s',
            '&:hover': {
              opacity: 1,
              borderColor: 'rgba(0, 229, 255, 0.4)',
              boxShadow: '0 0 8px rgba(0, 229, 255, 0.15)',
            },
          }}
        >
          {initial}
        </Box>

        {isAuthenticated && (
          <Menu
            anchorEl={anchorEl}
            open={Boolean(anchorEl)}
            onClose={handleClose}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
            transformOrigin={{ vertical: 'top', horizontal: 'right' }}
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
            {/* User info header */}
            <Box sx={{ px: 2, pt: 1, pb: 0.75, borderBottom: '1px solid rgba(255, 255, 255, 0.06)' }}>
              <Typography sx={{ fontSize: '0.7rem', color: alpha('#fff', 0.19), letterSpacing: '1.5px', textTransform: 'uppercase' }}>
                Account
              </Typography>
            </Box>
            <Box sx={{ px: 2, pt: 0.75, pb: 1, borderBottom: '1px solid rgba(255, 255, 255, 0.06)' }}>
              <Typography sx={{ fontSize: '0.8rem', fontWeight: 500, color: alpha('#fff', 0.8) }}>
                {user?.username}
              </Typography>
            </Box>
            <MenuItem
              onClick={handleAccount}
              sx={{ py: 0.75, px: 2, minHeight: 'auto', mt: 0.5, transition: 'all 0.15s', '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.04)' } }}
            >
              <ListItemIcon sx={{ minWidth: 24 }}>
                <PersonIcon sx={{ fontSize: 14, color: alpha('#fff', 0.25) }} />
              </ListItemIcon>
              <ListItemText
                primary="Account"
                slotProps={{ primary: { sx: { fontSize: '0.8rem', fontWeight: 400 } } }}
              />
            </MenuItem>
            <MenuItem
              onClick={handleApiKeys}
              sx={{ py: 0.75, px: 2, minHeight: 'auto', transition: 'all 0.15s', '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.04)' } }}
            >
              <ListItemIcon sx={{ minWidth: 24 }}>
                <KeyIcon sx={{ fontSize: 14, color: alpha('#fff', 0.25) }} />
              </ListItemIcon>
              <ListItemText
                primary="API keys"
                slotProps={{ primary: { sx: { fontSize: '0.8rem', fontWeight: 400 } } }}
              />
            </MenuItem>
            <MenuItem
              onClick={handleSettings}
              sx={{ py: 0.75, px: 2, minHeight: 'auto', transition: 'all 0.15s', '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.04)' } }}
            >
              <ListItemIcon sx={{ minWidth: 24 }}>
                <SettingsIcon sx={{ fontSize: 14, color: alpha('#fff', 0.25) }} />
              </ListItemIcon>
              <ListItemText
                primary="Settings"
                slotProps={{ primary: { sx: { fontSize: '0.8rem', fontWeight: 400 } } }}
              />
            </MenuItem>
            <MenuItem
              onClick={handleLogout}
              sx={{ py: 0.75, px: 2, minHeight: 'auto', transition: 'all 0.15s', '&:hover': { bgcolor: 'rgba(255, 0, 128, 0.05)' } }}
            >
              <ListItemIcon sx={{ minWidth: 24 }}>
                <LogoutIcon sx={{ fontSize: 14, color: ChaosCypherPalette.secondary }} />
              </ListItemIcon>
              <ListItemText
                primary="Logout"
                slotProps={{ primary: { sx: { fontSize: '0.8rem', fontWeight: 400, color: ChaosCypherPalette.secondary } } }}
              />
            </MenuItem>
          </Menu>
        )}
      </>
    );
  }

  // Sidebar variant
  return (
    <>
      <Tooltip title={isDrawerOpen ? 'Account' : (user?.username || 'Account')} placement="right">
        <Box
          onClick={isAuthenticated ? handleOpen : undefined}
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1.5,
            px: isDrawerOpen ? 2 : 0,
            py: 1.5,
            mx: 1,
            borderRadius: 1,
            cursor: isAuthenticated ? 'pointer' : 'default',
            justifyContent: isDrawerOpen ? 'flex-start' : 'center',
            transition: 'background 0.15s',
            '&:hover': {
              bgcolor: 'rgba(255, 255, 255, 0.04)',
            },
          }}
        >
          <Box
            sx={{
              width: 28,
              height: 28,
              borderRadius: '50%',
              bgcolor: 'rgba(0, 229, 255, 0.08)',
              border: '1px solid rgba(0, 229, 255, 0.2)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '0.75rem',
              fontWeight: 600,
              color: 'primary.main',
              opacity: 0.7,
              flexShrink: 0,
            }}
          >
            {initial}
          </Box>
          {isDrawerOpen && isAuthenticated && user && (
            <Typography
              variant="body2"
              noWrap
              sx={{ overflow: 'hidden', minWidth: 0, color: alpha('#fff', 0.31), fontSize: '0.8rem' }}
            >
              {user.username}
            </Typography>
          )}
        </Box>
      </Tooltip>

      {isAuthenticated && (
        <Menu
          anchorEl={anchorEl}
          open={Boolean(anchorEl)}
          onClose={handleClose}
          anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
          transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          slotProps={{
            paper: {
              sx: {
                ml: 1,
                minWidth: 180,
              },
            },
          }}
        >
          <MenuItem onClick={handleAccount}>
            <ListItemIcon>
              <PersonIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>Account</ListItemText>
          </MenuItem>
          <MenuItem onClick={handleApiKeys}>
            <ListItemIcon>
              <KeyIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>API keys</ListItemText>
          </MenuItem>
          <MenuItem onClick={handleSettings}>
            <ListItemIcon>
              <SettingsIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>Settings</ListItemText>
          </MenuItem>
          <MenuItem onClick={handleLogout}>
            <ListItemIcon>
              <LogoutIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>Logout</ListItemText>
          </MenuItem>
        </Menu>
      )}
    </>
  );
}
