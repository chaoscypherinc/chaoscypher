// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * StateZero component — shown when the omnibar opens with an empty query.
 * Displays recent items and quick actions as vertical command rows.
 */
import { useContext, useEffect, useRef } from 'react';
import { Box, Typography } from '@mui/material';
import { useNavigate } from 'react-router';
import { useRecentItems } from './useRecentItems';
import { UploadDialogContext } from '../../contexts/UploadDialogContext';
import type { RecentItem } from './types';
import { ChaosCypherPalette, ChaosCypherNeutrals } from '../../theme/palette';

const TYPE_COLORS: Record<string, string> = {
  entity: ChaosCypherPalette.primary,
  source: ChaosCypherPalette.secondary,
  chat: ChaosCypherPalette.success,
};

interface StateZeroProps {
  onClose: () => void;
  selectedIndex: number;
  onItemCount: (count: number) => void;
  onActivateMode: (prefix: string) => void;
}

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/.test(navigator.userAgent);

export function StateZero({ onClose, selectedIndex, onItemCount, onActivateMode }: StateZeroProps) {
  const navigate = useNavigate();
  const { items: recentItems } = useRecentItems();
  const uploadCtx = useContext(UploadDialogContext);
  const openUploadDialog = uploadCtx?.openUploadDialog ?? (() => {});

  const handleRecentClick = (item: RecentItem) => {
    switch (item.type) {
      case 'entity':
        navigate(`/nodes/${item.id}`);
        break;
      case 'source':
        navigate(`/sources/${item.id}`);
        break;
      case 'chat':
        navigate(`/chat/${item.id}`);
        break;
    }
    onClose();
  };

  const quickActions = [
    {
      icon: '💬',
      label: 'New Chat',
      hint: '/',
      action: () => { onActivateMode('/'); },
    },
    {
      icon: '📁',
      label: 'Import Source',
      hint: isMac ? '⌘I' : 'Ctrl+I',
      action: () => { openUploadDialog(); onClose(); },
    },
    {
      icon: '🕸️',
      label: 'Explore Graph',
      hint: 'G',
      action: () => { navigate('/graph'); onClose(); },
    },
  ];

  // Build flat list for keyboard nav: recent items + quick actions
  const allItems = [
    ...recentItems.map((item) => ({ type: 'recent' as const, data: item })),
    ...quickActions.map((action) => ({ type: 'action' as const, data: action })),
  ];

  // Report count to parent
  const count = allItems.length;
  if (onItemCount) {
    // Use requestAnimationFrame to avoid setState-during-render
    requestAnimationFrame(() => onItemCount(count));
  }

  const handleExecute = (index: number) => {
    const item = allItems[index];
    if (!item) return;
    if (item.type === 'recent') handleRecentClick(item.data as RecentItem);
    else (item.data as (typeof quickActions)[0]).action();
  };

  // Handle Enter on selected item
  useKeyboardExecute(selectedIndex, count, handleExecute);

  let globalIndex = 0;

  return (
    <Box>
      {/* Recent Items */}
      {recentItems.length > 0 && (
        <Box sx={{ px: 1.5, pt: 1 }}>
          <Typography sx={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.1em', color: ChaosCypherNeutrals.textMuted, px: 1, mb: 0.5 }}>
            Recent
          </Typography>
          {recentItems.map((item) => {
            const idx = globalIndex++;
            const isSelected = idx === selectedIndex;
            return (
              <CommandRow
                key={`${item.type}-${item.id}`}
                icon={item.icon}
                label={item.title}
                hint={item.type}
                hintColor={TYPE_COLORS[item.type]}
                isSelected={isSelected}
                onClick={() => handleRecentClick(item)}
              />
            );
          })}
        </Box>
      )}

      {/* Quick Actions */}
      <Box sx={{ px: 1.5, pt: recentItems.length > 0 ? 0.5 : 1, pb: 1 }}>
        <Typography sx={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.1em', color: ChaosCypherNeutrals.textMuted, px: 1, mb: 0.5 }}>
          Quick Actions
        </Typography>
        {quickActions.map((action) => {
          const idx = globalIndex++;
          const isSelected = idx === selectedIndex;
          return (
            <CommandRow
              key={action.label}
              icon={action.icon}
              label={action.label}
              hint={action.hint}
              isSelected={isSelected}
              onClick={action.action}
            />
          );
        })}
      </Box>
    </Box>
  );
}

/** A single command row — icon, label, right-aligned keyboard hint. */
function CommandRow({
  icon,
  label,
  hint,
  hintColor,
  isSelected,
  onClick,
}: {
  icon: string;
  label: string;
  hint: string;
  hintColor?: string;
  isSelected: boolean;
  onClick: () => void;
}) {
  return (
    <Box
      data-selected={isSelected || undefined}
      onClick={onClick}
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.25,
        px: 1.25,
        py: 0.75,
        borderRadius: '6px',
        cursor: 'pointer',
        bgcolor: isSelected ? 'rgba(0, 229, 255, 0.08)' : 'transparent',
        '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' },
      }}
    >
      <Typography sx={{ fontSize: 15, lineHeight: 1, width: 20, textAlign: 'center' }}>
        {icon}
      </Typography>
      <Typography sx={{ color: 'text.primary', fontSize: 13, flex: 1 }}>
        {label}
      </Typography>
      <Typography
        sx={{
          color: hintColor ?? ChaosCypherNeutrals.textMuted,
          fontSize: 11,
          fontFamily: 'monospace',
          bgcolor: ChaosCypherNeutrals.surfaceRaised,
          px: 0.75,
          py: 0.15,
          borderRadius: '3px',
          textTransform: hintColor ? 'capitalize' : 'none',
        }}
      >
        {hint}
      </Typography>
    </Box>
  );
}

/** Attach Enter key handler for state zero keyboard navigation. */
function useKeyboardExecute(
  selectedIndex: number,
  itemCount: number,
  onExecute: (index: number) => void,
) {
  const executeRef = useRef(onExecute);
  const indexRef = useRef(selectedIndex);
  const countRef = useRef(itemCount);

  useEffect(() => { executeRef.current = onExecute; }, [onExecute]);
  useEffect(() => { indexRef.current = selectedIndex; }, [selectedIndex]);
  useEffect(() => { countRef.current = itemCount; }, [itemCount]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && countRef.current > 0) {
        e.preventDefault();
        executeRef.current(indexRef.current);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);
}

