// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useEffect } from 'react';
import {
  Drawer,
  Box,
  Typography,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Button,
  Divider,
  TextField,
  InputAdornment,
  CircularProgress,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import RemoveIcon from '@mui/icons-material/Delete';
import AddIcon from '@mui/icons-material/Add';
import SearchIcon from '@mui/icons-material/Search';
import ClearScopeIcon from '@mui/icons-material/FilterListOff';
import { sourcesApi } from '../../services/api/sources';
import type { Source, SourceSummary } from '../../types';

interface ScopePanelProps {
  open: boolean;
  onClose: () => void;
  sourceIds: string[];
  onUpdateScope: (sourceIds: string[]) => Promise<void>;
  onClearScope: () => Promise<void>;
}

/**
 * Slide-out panel for viewing and managing the source scope of a chat.
 * Shows scoped sources with remove buttons, and a search to add more.
 */
export default function ScopePanel({
  open,
  onClose,
  sourceIds,
  onUpdateScope,
  onClearScope,
}: ScopePanelProps) {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [allSources, setAllSources] = useState<SourceSummary[]>([]);
  const [loadingAll, setLoadingAll] = useState(false);

  // Load scoped sources. Intentional setState-in-effect: when the panel
  // closes or scope is cleared, we need to clear the cached source list.
  useEffect(() => {
    if (!open || sourceIds.length === 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSources([]);
      return;
    }
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      const results: Source[] = [];
      for (const id of sourceIds) {
        try {
          const source = await sourcesApi.get(id);
          if (!cancelled) results.push(source);
        } catch {
          // Skip
        }
      }
      if (!cancelled) {
        setSources(results);
        setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [open, sourceIds]);

  // Load available sources when panel opens. Intentional setState-in-effect
  // for the same reason as above (clear cache on close).
  useEffect(() => {
    if (!open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAllSources([]);
      return;
    }
    let cancelled = false;
    const load = async () => {
      setLoadingAll(true);
      try {
        const response = await sourcesApi.list({ page_size: 100 });
        if (!cancelled) setAllSources(response.data || []);
      } catch {
        if (!cancelled) setAllSources([]);
      }
      if (!cancelled) setLoadingAll(false);
    };
    load();
    return () => { cancelled = true; };
  }, [open]);

  // Filter available sources: exclude already-scoped, apply search, sort by newest
  const availableSources = allSources
    .filter((s) => !sourceIds.includes(s.id))
    .filter((s) => {
      if (!search.trim()) return true;
      const q = search.toLowerCase();
      return (s.title || '').toLowerCase().includes(q) || (s.filename || '').toLowerCase().includes(q);
    })
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

  const handleRemove = async (removeId: string) => {
    const newIds = sourceIds.filter((id) => id !== removeId);
    if (newIds.length === 0) {
      await onClearScope();
    } else {
      await onUpdateScope(newIds);
    }
  };

  const handleAdd = async (addId: string) => {
    const newIds = [...sourceIds, addId];
    await onUpdateScope(newIds);
  };

  const handleClearAll = async () => {
    await onClearScope();
    onClose();
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      slotProps={{
        paper: { sx: { width: 360, display: 'flex', flexDirection: 'column' } },
      }}
    >
      <Box sx={{ p: 2, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <Typography variant="h6" sx={{
          fontWeight: 600
        }}>Source Scope</Typography>
        <IconButton aria-label="Close" onClick={onClose} size="small">
          <CloseIcon />
        </IconButton>
      </Box>
      <Divider />
      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
          <CircularProgress size={24} />
        </Box>
      ) : sourceIds.length === 0 ? (
        <Box sx={{ p: 3, textAlign: 'center' }}>
          <Typography variant="body2" sx={{
            color: "text.secondary"
          }}>
            No source scope set. This chat can access all sources.
          </Typography>
        </Box>
      ) : (
        <>
          {/* Scoped sources list */}
          <List sx={{ py: 0 }}>
            {sources.map((source) => (
              <ListItem
                key={source.id}
                secondaryAction={
                  <IconButton
                    aria-label="Remove source from scope"
                    edge="end"
                    size="small"
                    onClick={() => handleRemove(source.id)}
                    color="error"
                  >
                    <RemoveIcon fontSize="small" />
                  </IconButton>
                }
              >
                <ListItemText
                  primary={
                    <Typography variant="body2" noWrap>
                      {source.title || source.filename}
                    </Typography>
                  }
                  secondary={
                    <Typography variant="caption" sx={{
                      color: "text.secondary"
                    }}>
                      {source.file_type?.toUpperCase()} {source.filename !== source.title ? `- ${source.filename}` : ''}
                    </Typography>
                  }
                />
              </ListItem>
            ))}
          </List>

          <Divider />

          {/* Clear scope button */}
          <Box sx={{ p: 1.5 }}>
            <Button
              fullWidth
              size="small"
              color="warning"
              startIcon={<ClearScopeIcon />}
              onClick={handleClearAll}
            >
              Clear Scope
            </Button>
          </Box>
        </>
      )}
      <Divider />
      {/* Add sources */}
      <Box sx={{ p: 1.5, display: 'flex', flexDirection: 'column', minHeight: 0, flexGrow: 1 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>Add Sources</Typography>
        <TextField
          fullWidth
          size="small"
          placeholder="Filter sources..."
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
        {loadingAll ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
            <CircularProgress size={20} />
          </Box>
        ) : (
          <List sx={{ py: 0.5, overflowY: 'auto', flexGrow: 1 }}>
            {availableSources.map((source) => (
              <ListItem key={source.id} disablePadding>
                <Button
                  fullWidth
                  size="small"
                  startIcon={<AddIcon fontSize="small" />}
                  onClick={() => handleAdd(source.id)}
                  sx={{ justifyContent: 'flex-start', textTransform: 'none', px: 2 }}
                >
                  <Typography variant="body2" noWrap>
                    {source.title || source.filename}
                  </Typography>
                </Button>
              </ListItem>
            ))}
            {!loadingAll && availableSources.length === 0 && (
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  px: 2,
                  py: 1,
                  display: 'block'
                }}>
                {search.trim() ? 'No matching sources' : allSources.length > 0 ? 'All sources already added' : 'No sources available'}
              </Typography>
            )}
          </List>
        )}
      </Box>
    </Drawer>
  );
}
