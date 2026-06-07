// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * HorizontalLayoutSelector: SpeedDial-style layout algorithm selector
 * - Matches the style of the bottom-left SpeedDial for creating nodes
 * - Shows layout icon and expands to show all layout options
 * - Also includes settings and keyboard shortcuts
 * - Auto-closes after selection
 */

import React, { useState } from 'react';
import { SpeedDial, SpeedDialAction } from '@mui/material';
import BubbleChartIcon from '@mui/icons-material/BubbleChartOutlined';
import GridOnIcon from '@mui/icons-material/GridOnOutlined';
import PanToolIcon from '@mui/icons-material/PanToolOutlined';
import HubIcon from '@mui/icons-material/HubOutlined';
import ForkRightIcon from '@mui/icons-material/ForkRightOutlined';
import RadioButtonCheckedIcon from '@mui/icons-material/RadioButtonCheckedOutlined';
import DashboardCustomizeIcon from '@mui/icons-material/DashboardCustomizeOutlined';
import SettingsIcon from '@mui/icons-material/SettingsOutlined';
import KeyboardIcon from '@mui/icons-material/KeyboardOutlined';

type LayoutType = 'force' | 'grid' | 'mindmap' | 'hierarchical' | 'radial' | 'manual';

interface HorizontalLayoutSelectorProps {
  currentLayout: LayoutType;
  onLayoutChange: (layout: LayoutType) => void;
  onOpenSettings: (anchor: HTMLElement) => void;
  onOpenKeyboardShortcuts: (anchor: HTMLElement) => void;
}

export const HorizontalLayoutSelector: React.FC<HorizontalLayoutSelectorProps> = ({
  currentLayout,
  onLayoutChange,
  onOpenSettings,
  onOpenKeyboardShortcuts
}) => {
  const [open, setOpen] = useState(false);

  const layouts: Array<{ type: LayoutType; icon: React.ReactNode; label: string }> = [
    { type: 'mindmap', icon: <HubIcon />, label: 'Mindmap Layout' },
    { type: 'force', icon: <BubbleChartIcon />, label: 'Force-Directed Layout' },
    { type: 'hierarchical', icon: <ForkRightIcon />, label: 'Hierarchical Layout' },
    { type: 'radial', icon: <RadioButtonCheckedIcon />, label: 'Radial Layout' },
    { type: 'grid', icon: <GridOnIcon />, label: 'Grid Layout' },
    { type: 'manual', icon: <PanToolIcon />, label: 'Manual Layout' },
  ];

  const handleLayoutChange = (layout: LayoutType) => {
    onLayoutChange(layout);
    setOpen(false); // Close after selection
  };

  const handleSettings = (e: React.MouseEvent<HTMLDivElement>) => {
    onOpenSettings(e.currentTarget as HTMLElement);
    setOpen(false);
  };

  const handleKeyboardShortcuts = (e: React.MouseEvent<HTMLDivElement>) => {
    onOpenKeyboardShortcuts(e.currentTarget as HTMLElement);
    setOpen(false);
  };

  return (
    <SpeedDial
      ariaLabel="Layout Options"
      FabProps={{ size: 'small' }}
      sx={{
        position: 'absolute',
        top: 24,
        right: 24,
        '& .MuiSpeedDial-fab': {
          bgcolor: 'transparent',
          border: '1px solid',
          borderColor: 'secondary.main',
          color: 'secondary.main',
          boxShadow: 'none',
          '&:hover': { bgcolor: 'rgba(255, 0, 128, 0.1)' },
        },
        '& .MuiSpeedDialAction-fab': {
          bgcolor: 'transparent',
          border: '1px solid rgba(255, 255, 255, 0.15)',
          color: 'text.primary',
          boxShadow: 'none',
          '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.08)', borderColor: 'secondary.main', color: 'secondary.main' },
        },
      }}
      icon={<DashboardCustomizeIcon />}
      onClose={() => setOpen(false)}
      onOpen={() => setOpen(true)}
      open={open}
      direction="down"
    >
      {/* Layout options */}
      {layouts.map(({ type, icon, label }) => (
        <SpeedDialAction
          key={type}
          icon={icon}
          title={label}
          onClick={() => handleLayoutChange(type)}
          slotProps={{
            fab: {
              size: 'small',
              color: currentLayout === type ? 'primary' : 'default',
              sx: {
                width: 36,
                height: 36,
                bgcolor: currentLayout === type ? 'primary.main' : undefined,
                '&:hover': {
                  bgcolor: currentLayout === type ? 'primary.dark' : undefined,
                },
                '& svg': {
                  fontSize: '1.1rem',
                }
              }
            }
          }}
        />
      ))}

      {/* Settings and shortcuts */}
      <SpeedDialAction
        key="settings"
        icon={<SettingsIcon />}
        title="Display Settings"
        onClick={handleSettings}
        slotProps={{
          fab: {
            size: 'small',
            sx: {
              width: 36,
              height: 36,
              '& svg': {
                fontSize: '1.1rem',
              }
            }
          }
        }}
      />
      <SpeedDialAction
        key="shortcuts"
        icon={<KeyboardIcon />}
        title="Keyboard Shortcuts"
        onClick={handleKeyboardShortcuts}
        slotProps={{
          fab: {
            size: 'small',
            sx: {
              width: 36,
              height: 36,
              '& svg': {
                fontSize: '1.1rem',
              }
            }
          }
        }}
      />
    </SpeedDial>
  );
};
