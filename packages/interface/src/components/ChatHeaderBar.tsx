// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useEffect } from 'react';
import {
  Box,
  ButtonBase,
  IconButton,
  Typography,
  TextField,
  Tooltip,
  Popover,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Divider,
  InputAdornment,
  Menu,
  MenuItem,
} from '@mui/material';
import ConfirmDialog from './ConfirmDialog';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import ExportIcon from '@mui/icons-material/Download';
import ChevronDownIcon from '@mui/icons-material/KeyboardArrowDown';
import SearchIcon from '@mui/icons-material/Search';
import FilterIcon from '@mui/icons-material/FilterList';
import type { ChatMetadata } from '../types';
import { useChatSearch } from '../services/api/useChats';
import ScopeBadge from './chat/ScopeBadge';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';

interface ChatHeaderBarProps {
  chats: ChatMetadata[];
  currentChat: { id: string; title: string; source_ids?: string[] | null } | null;
  onSelectChat: (chatId: string) => void;
  onNewChat: () => void;
  onRenameChat: (chatId: string, newTitle: string) => void;
  onDeleteChat: (chatId: string) => void;
  onExportChat: (chatId: string, format?: 'json' | 'markdown') => void;
  onClearAllChats: () => void;
  onScopeBadgeClick?: () => void;
  /** Number of pending scope sources (for new chat, before first message) */
  pendingScopeCount?: number;
}

/** Format a date string as a relative time label. */
function formatDate(dateString: string): string {
  try {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;

    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return `${months[date.getMonth()]} ${date.getDate()}`;
  } catch {
    return '';
  }
}

/**
 * Header bar for the chat area replacing the old persistent sidebar.
 *
 * Left: chat title dropdown trigger + new-chat button.
 * Right: rename / export / delete actions for the active chat.
 * Dropdown: search + chat list popover.
 */
export default function ChatHeaderBar({
  chats,
  currentChat,
  onSelectChat,
  onNewChat,
  onRenameChat,
  onDeleteChat,
  onExportChat,
  onClearAllChats,
  onScopeBadgeClick,
  pendingScopeCount = 0,
}: ChatHeaderBarProps) {
  // Dropdown popover
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  const dropdownOpen = Boolean(anchorEl);

  // Search within dropdown
  const [search, setSearch] = useState('');

  // Rename dialog
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameValue, setRenameValue] = useState('');

  // Delete confirm dialog
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

  // Clear all confirm dialog
  const [confirmClearAllOpen, setConfirmClearAllOpen] = useState(false);

  // Server-side title search (debounced): the `chats` prop only holds the
  // first page, so filtering it client-side could never find older chats.
  const [debouncedSearch, setDebouncedSearch] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(timer);
  }, [search]);
  const { data: searchResults, isFetching: searchPending } = useChatSearch(debouncedSearch);

  const searchActive = search.trim().length > 0;
  const filteredChats = searchActive ? (searchResults ?? []) : chats;

  // --- Dropdown handlers ---

  const handleOpenDropdown = (event: React.MouseEvent<HTMLElement>) => {
    setAnchorEl(event.currentTarget);
    setSearch('');
  };

  const handleCloseDropdown = () => {
    setAnchorEl(null);
    setSearch('');
  };

  const handleSelectFromDropdown = (chatId: string) => {
    onSelectChat(chatId);
    handleCloseDropdown();
  };

  const handleNewChatFromDropdown = () => {
    onNewChat();
    handleCloseDropdown();
  };

  // --- Action handlers ---

  const handleRenameClick = () => {
    if (currentChat) {
      setRenameValue(currentChat.title);
      setRenameDialogOpen(true);
    }
  };

  const handleRenameSubmit = () => {
    if (currentChat && renameValue.trim()) {
      onRenameChat(currentChat.id, renameValue.trim());
    }
    setRenameDialogOpen(false);
    setRenameValue('');
  };

  const handleDeleteClick = () => {
    if (currentChat) {
      setConfirmDeleteOpen(true);
    }
  };

  const handleConfirmDelete = () => {
    if (currentChat) {
      onDeleteChat(currentChat.id);
    }
    setConfirmDeleteOpen(false);
  };

  const [exportAnchor, setExportAnchor] = useState<HTMLElement | null>(null);

  const handleExportClick = (event: React.MouseEvent<HTMLElement>) => {
    if (currentChat) {
      setExportAnchor(event.currentTarget);
    }
  };

  const handleExportFormat = (format: 'json' | 'markdown') => {
    if (currentChat) {
      onExportChat(currentChat.id, format);
    }
    setExportAnchor(null);
  };

  const handleClearAllClick = () => {
    setConfirmClearAllOpen(true);
    handleCloseDropdown();
  };

  const handleConfirmClearAll = () => {
    onClearAllChats();
    setConfirmClearAllOpen(false);
  };

  return (
    <>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          px: 2,
          py: 1,
          borderBottom: 1,
          borderColor: 'divider',
          flexShrink: 0,
          gap: 1,
        }}
      >
        {/* Left: title dropdown trigger + new chat */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0, flexShrink: 1 }}>
          <ButtonBase
            onClick={handleOpenDropdown}
            aria-label="Switch chat"
            aria-haspopup="listbox"
            aria-expanded={dropdownOpen}
            sx={{
              display: 'flex',
              alignItems: 'center',
              borderRadius: 1,
              px: 1,
              py: 0.5,
              minWidth: 0,
              '&:hover': { bgcolor: 'action.hover' },
            }}
          >
            <Typography variant="subtitle1" noWrap sx={{ fontWeight: 600 }}>
              {currentChat?.title || 'New Chat'}
            </Typography>
            <ChevronDownIcon
              fontSize="small"
              sx={{
                ml: 0.5,
                flexShrink: 0,
                transition: 'transform 0.2s',
                transform: dropdownOpen ? 'rotate(180deg)' : 'none',
              }}
            />
          </ButtonBase>

          <Tooltip title="New chat">
            <IconButton aria-label="New chat" size="small" onClick={onNewChat} color="primary">
              <AddIcon fontSize="small" />
            </IconButton>
          </Tooltip>

          {/* Scope badge (existing chat with scope) */}
          {currentChat?.source_ids && currentChat.source_ids.length > 0 && (
            <ScopeBadge
              sourceIds={currentChat.source_ids}
              onClick={onScopeBadgeClick}
            />
          )}

          {/* Scope button (new chat or no scope yet) */}
          {!(currentChat?.source_ids && currentChat.source_ids.length > 0) && onScopeBadgeClick && (
            <Tooltip title={pendingScopeCount > 0 ? `Scoped to ${pendingScopeCount} source(s)` : 'Scope to sources'}>
              <IconButton
                aria-label={pendingScopeCount > 0 ? `Scoped to ${pendingScopeCount} source(s)` : 'Scope to sources'}
                size="small"
                onClick={onScopeBadgeClick}
                color={pendingScopeCount > 0 ? 'primary' : 'default'}
              >
                <FilterIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
        </Box>

        {/* Right: active chat actions */}
        {currentChat && (
          <Box sx={{ display: 'flex', alignItems: 'center', ml: 'auto', gap: 0.5 }}>
            <Tooltip title="Rename">
              <IconButton
                aria-label="Rename"
                size="small"
                onClick={handleRenameClick}
                sx={{ color: 'text.disabled', '&:hover': { color: 'text.primary' }, transition: 'color 0.15s' }}
              >
                <EditIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="Export">
              <IconButton
                aria-label="Export"
                size="small"
                onClick={handleExportClick}
                sx={{ color: 'text.disabled', '&:hover': { color: 'text.primary' }, transition: 'color 0.15s' }}
              >
                <ExportIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Menu
              anchorEl={exportAnchor}
              open={Boolean(exportAnchor)}
              onClose={() => setExportAnchor(null)}
            >
              <MenuItem onClick={() => handleExportFormat('json')}>Export as JSON</MenuItem>
              <MenuItem onClick={() => handleExportFormat('markdown')}>Export as Markdown</MenuItem>
            </Menu>
            <Tooltip title="Delete">
              <IconButton
                aria-label="Delete"
                size="small"
                onClick={handleDeleteClick}
                sx={{ color: 'text.disabled', '&:hover': { color: 'error.main' }, transition: 'color 0.15s' }}
              >
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>
        )}
      </Box>
      {/* Chat list dropdown popover */}
      <Popover
        open={dropdownOpen}
        anchorEl={anchorEl}
        onClose={handleCloseDropdown}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        slotProps={{
          paper: {
            sx: { width: 320, maxHeight: 440, display: 'flex', flexDirection: 'column' },
          },
        }}
      >
        {/* Search */}
        <Box sx={{ p: 1.5, pb: 0 }}>
          <TextField
            autoFocus
            fullWidth
            size="small"
            placeholder="Search chats..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              },
            }}
          />
        </Box>

        {/* New chat row */}
        <ListItemButton
          onClick={handleNewChatFromDropdown}
          sx={{ mx: 1.5, mt: 1, borderRadius: 1 }}
        >
          <AddIcon fontSize="small" sx={{ mr: 1 }} color="primary" />
          <Typography variant="body2" color="primary" sx={{ fontWeight: 500 }}>
            New Chat
          </Typography>
        </ListItemButton>

        <Divider sx={{ mt: 1 }} />

        {/* Chat list */}
        <List sx={{ overflowY: 'auto', py: 0.5, flexGrow: 1 }}>
          {filteredChats.length === 0 ? (
            <Box sx={{ p: 2, textAlign: 'center' }}>
              <Typography variant="body2" sx={{
                color: "text.secondary"
              }}>
                {searchActive && searchPending
                  ? 'Searching…'
                  : search
                    ? 'No matches'
                    : 'No chats yet'}
              </Typography>
            </Box>
          ) : (
            filteredChats.map((chat) => (
              <ListItem key={chat.id} disablePadding>
                <ListItemButton
                  selected={chat.id === currentChat?.id}
                  onClick={() => handleSelectFromDropdown(chat.id)}
                  sx={{ px: 2, py: 0.75 }}
                >
                  <ListItemText
                    primary={
                      <Typography variant="body2" noWrap>
                        {chat.title}
                      </Typography>
                    }
                    secondary={
                      <Typography variant="caption" sx={{
                        color: "text.secondary"
                      }}>
                        {formatDate(chat.updated_at)}
                      </Typography>
                    }
                  />
                  {chat.source_ids && chat.source_ids.length > 0 && (
                    <Tooltip title="Scoped chat" placement="right">
                      <FilterIcon sx={{ fontSize: 14, ml: 1, opacity: 0.5, flexShrink: 0 }} color="primary" />
                    </Tooltip>
                  )}
                </ListItemButton>
              </ListItem>
            ))
          )}
        </List>

        {/* Clear all */}
        {chats.length > 0 && (
          <>
            <Divider />
            <Box sx={{ p: 1 }}>
              <Button
                fullWidth
                size="small"
                color="error"
                startIcon={<DeleteIcon fontSize="small" />}
                onClick={handleClearAllClick}
              >
                Clear All Chats
              </Button>
            </Box>
          </>
        )}
      </Popover>
      {/* Rename Dialog */}
      <Dialog open={renameDialogOpen} onClose={() => setRenameDialogOpen(false)} slotProps={{
        paper: { sx: ghostDialogPaperSx }
      }}>
        <DialogTitle>Rename Chat</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            label="Title"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleRenameSubmit();
            }}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameDialogOpen(false)} sx={ghostCancelBtnSx}>Cancel</Button>
          <Button onClick={handleRenameSubmit} variant="outlined" sx={ghostButtonSx(ChaosCypherPalette.primary)}>
            Rename
          </Button>
        </DialogActions>
      </Dialog>
      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        open={confirmDeleteOpen}
        title="Confirm Delete"
        message="Are you sure you want to delete this chat?"
        onConfirm={handleConfirmDelete}
        onCancel={() => setConfirmDeleteOpen(false)}
      />
      {/* Clear All Confirmation Dialog */}
      <ConfirmDialog
        open={confirmClearAllOpen}
        title="Clear All Chats"
        message={`Are you sure you want to delete all ${chats.length} chat${chats.length !== 1 ? 's' : ''}? This cannot be undone.`}
        confirmLabel="Clear All"
        onConfirm={handleConfirmClearAll}
        onCancel={() => setConfirmClearAllOpen(false)}
      />
    </>
  );
}
