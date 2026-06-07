// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Sidebar Navigation Component
 *
 * Renders the full sidebar drawer content: logo/branding, navigation menu
 * with section grouping, route-based active highlighting, and the
 * collapse/expand toggle. Used by Layout inside both the temporary (mobile)
 * and permanent (desktop) MUI Drawers.
 */

import React from 'react';
import {
  Box,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Tooltip,
  Typography,
  alpha,
} from '@mui/material';
import { ChaosCypherPalette } from '../theme/palette';
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import {
  LayoutDashboard,
  BookOpen,
  MessageSquare,
  Network,
  Target,
  Link as LinkIcon,
  FileText,
  FileStack,
  Zap,
  Settings,
  Layers,
} from 'lucide-react';
import { useNavigate, useLocation } from 'react-router';
import { ContentTypeColors } from '../theme/colors';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SubmenuItem {
  text: string;
  icon: React.JSX.Element;
  path: string;
  color: string;
}

interface MenuItem {
  text: string;
  icon: React.JSX.Element;
  path: string;
  submenu?: SubmenuItem[];
}

interface SidebarProps {
  /** Whether the sidebar is in its expanded (open) state */
  isDrawerOpen: boolean;
  /** Toggle the drawer between collapsed and expanded */
  onToggleCollapse: () => void;
  /** Close the mobile drawer after navigation */
  onMobileClose: () => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ICON_SIZE = 16;
const ICON_STROKE = 1.5;
const APP_NAME = 'Chaos Cypher';

const MENU_ITEMS: MenuItem[] = [
  { text: 'Dashboard', icon: <LayoutDashboard size={ICON_SIZE} strokeWidth={ICON_STROKE} />, path: '/' },
  { text: 'Lexicon', icon: <BookOpen size={ICON_SIZE} strokeWidth={ICON_STROKE} />, path: '/lexicon' },
  { text: 'Chat', icon: <MessageSquare size={ICON_SIZE} strokeWidth={ICON_STROKE} />, path: '/chat' },
  {
    text: 'Knowledge',
    icon: <Network size={ICON_SIZE} strokeWidth={ICON_STROKE} />,
    path: '/graph',
    submenu: [
      { text: 'Graph', icon: <Network size={ICON_SIZE} strokeWidth={ICON_STROKE} />, path: '/graph', color: ContentTypeColors.chunks },
      { text: 'Entities', icon: <Target size={ICON_SIZE} strokeWidth={ICON_STROKE} />, path: '/nodes', color: ContentTypeColors.entities },
      { text: 'Relationships', icon: <LinkIcon size={ICON_SIZE} strokeWidth={ICON_STROKE} />, path: '/edges', color: ContentTypeColors.relationships },
      { text: 'Templates', icon: <FileText size={ICON_SIZE} strokeWidth={ICON_STROKE} />, path: '/templates', color: ContentTypeColors.templates },
    ],
  },
  { text: 'Sources', icon: <FileStack size={ICON_SIZE} strokeWidth={ICON_STROKE} />, path: '/sources' },
  { text: 'Automations', icon: <Zap size={ICON_SIZE} strokeWidth={ICON_STROKE} />, path: '/automations' },
  { text: 'Queues', icon: <Layers size={ICON_SIZE} strokeWidth={ICON_STROKE} />, path: '/queues' },
  { text: 'Settings', icon: <Settings size={ICON_SIZE} strokeWidth={ICON_STROKE} />, path: '/settings' },
];

/** Shared sx for section header labels (Knowledge, System). */
const SECTION_LABEL_SX = {
  fontFamily: "'Exo 2', sans-serif",
  fontSize: '9px',
  fontWeight: 500,
  letterSpacing: '3.5px',
  textTransform: 'uppercase' as const,
  opacity: 0.1,
  px: 1.5,
  pt: 2,
  pb: 0.75,
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/** Sidebar drawer content with navigation, sections, and collapse toggle. */
export default function Sidebar({ isDrawerOpen, onToggleCollapse, onMobileClose }: SidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();

  const handleNavigate = (path: string) => {
    navigate(path);
    onMobileClose();
  };

  const renderMenuItem = (item: { text: string; icon: React.JSX.Element; path: string }) => {
    const isActive = location.pathname === item.path;

    return (
      <ListItem key={item.text} disablePadding sx={{ display: 'block' }}>
        <Tooltip title={!isDrawerOpen ? item.text : ''} placement="right">
          <ListItemButton
            selected={isActive}
            onClick={() => handleNavigate(item.path)}
            sx={{
              justifyContent: isDrawerOpen ? 'initial' : 'center',
              px: 1.5,
              py: 1,
              borderRadius: 1,
              mb: 0.25,
              position: 'relative',
              color: isActive ? 'primary.main' : alpha('#fff', 0.46),
              '&:hover': {
                color: alpha('#fff', 0.58),
                bgcolor: alpha('#fff', 0.02),
              },
              '&.Mui-selected': {
                bgcolor: 'transparent',
                color: 'primary.main',
                '&:hover': {
                  bgcolor: alpha('#fff', 0.02),
                },
              },
              ...(isActive && {
                '&::before': {
                  content: '""',
                  position: 'absolute',
                  left: -12,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  width: 2,
                  height: 20,
                  bgcolor: 'primary.main',
                  borderRadius: '1px',
                  boxShadow: `0 0 8px ${alpha(ChaosCypherPalette.primary, 0.38)}`,
                },
              }),
            }}
          >
            <ListItemIcon
              sx={{
                minWidth: 0,
                mr: isDrawerOpen ? 1.5 : 'auto',
                justifyContent: 'center',
                color: 'inherit',
              }}
            >
              {item.icon}
            </ListItemIcon>
            {isDrawerOpen && (
              <ListItemText
                primary={item.text}
                slotProps={{
                  primary: {
                    sx: {
                      fontFamily: "'Exo 2', sans-serif",
                      fontSize: 13,
                      fontWeight: 400,
                      letterSpacing: '0.5px',
                    },
                  }
                }}
              />
            )}
          </ListItemButton>
        </Tooltip>
      </ListItem>
    );
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Logo / Branding */}
      <Toolbar sx={{ gap: 1, justifyContent: isDrawerOpen ? 'flex-start' : 'center', px: 1.5 }}>
        {isDrawerOpen ? (
          <>
            <Box
              component="img"
              src="/logo.png"
              alt="Logo"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = 'none';
              }}
              sx={{
                height: 32,
                width: 32,
                flexShrink: 0,
                objectFit: 'contain',
                backgroundColor: 'transparent',
              }}
            />
            <Typography variant="h6" noWrap component="div" sx={{ lineHeight: 1.2, fontSize: '1.1rem' }}>
              {APP_NAME}
            </Typography>
          </>
        ) : (
          <Tooltip title={APP_NAME}>
            <Box
              component="img"
              src="/logo.png"
              alt="Logo"
              onError={(e) => {
                const target = e.target as HTMLImageElement;
                target.style.display = 'none';
                const parent = target.parentElement;
                if (parent) {
                  const fallback = document.createElement('div');
                  fallback.textContent = APP_NAME.charAt(0);
                  fallback.style.fontSize = '24px';
                  fallback.style.fontWeight = 'bold';
                  parent.appendChild(fallback);
                }
              }}
              sx={{
                height: 40,
                width: 40,
                objectFit: 'contain',
                backgroundColor: 'transparent',
              }}
            />
          </Tooltip>
        )}
      </Toolbar>

      <Box sx={{ height: 16 }} />

      {/* Navigation Menu */}
      <List sx={{ flexGrow: 1, pl: 1.5, pr: 1 }}>
        {/* Top nav: Dashboard, Lexicon, Chat */}
        {MENU_ITEMS.slice(0, 3).map((item) => renderMenuItem(item))}

        {/* Knowledge section */}
        {isDrawerOpen && (
          <Typography sx={SECTION_LABEL_SX}>
            Knowledge
          </Typography>
        )}
        {MENU_ITEMS[3].submenu
          ? MENU_ITEMS[3].submenu.map((sub) => renderMenuItem({ text: sub.text, icon: sub.icon, path: sub.path }))
          : renderMenuItem(MENU_ITEMS[3])
        }

        {/* System section */}
        {isDrawerOpen && (
          <Typography sx={SECTION_LABEL_SX}>
            System
          </Typography>
        )}
        {MENU_ITEMS.slice(4).map((item) => renderMenuItem(item))}
      </List>

      {/* Collapse toggle */}
      <Tooltip title={!isDrawerOpen ? 'Expand sidebar' : ''} placement="right">
        <Box
          onClick={onToggleCollapse}
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: isDrawerOpen ? 'initial' : 'center',
            gap: 1.5,
            mt: 'auto',
            px: 1.5,
            py: 1.5,
            mx: isDrawerOpen ? 1.5 : 0,
            cursor: 'pointer',
            color: alpha('#fff', 0.19),
            transition: 'all 0.15s',
            '&:hover': {
              color: alpha('#fff', 0.38),
            },
          }}
        >
          {isDrawerOpen ? (
            <ChevronLeftIcon sx={{ fontSize: 16 }} />
          ) : (
            <ChevronRightIcon sx={{ fontSize: 16 }} />
          )}
          {isDrawerOpen && (
            <Typography sx={{ fontSize: 13, letterSpacing: '0.2px' }}>
              Collapse
            </Typography>
          )}
        </Box>
      </Tooltip>
    </Box>
  );
}
