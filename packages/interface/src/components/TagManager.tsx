// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  TextField,
  IconButton,
  List,
  ListItem,
  ListItemText,
  ListItemSecondaryAction,
  Chip,
  Typography,
  Tooltip,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import CheckIcon from '@mui/icons-material/Check';
import CloseIcon from '@mui/icons-material/Close';
import { tagsApi } from '../services/api/sources';
import type { SourceTag } from '../types';
import { TagPalette } from '../theme/colors';
import { ghostDialogPaperSx, ghostButtonSx, ghostCancelBtnSx, ghostInputSx } from '../theme/ghostStyles';
import { ChaosCypherPalette } from '../theme/palette';
import { getApiErrorMessage } from '../utils/errors';
import { logger } from '../utils/logger';
import ConfirmDialog from './ConfirmDialog';

const PRESET_COLORS = TagPalette;

// Generate random color from presets
const getRandomColor = () => {
  return PRESET_COLORS[Math.floor(Math.random() * PRESET_COLORS.length)];
};

interface TagManagerProps {
  open: boolean;
  onClose: () => void;
  onTagsChanged?: () => void;
}

export default function TagManager({ open, onClose, onTagsChanged }: TagManagerProps) {
  const [tags, setTags] = useState<SourceTag[]>([]);
  const [_loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Create/Edit state
  const [editingTag, setEditingTag] = useState<SourceTag | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState('');
  const [formColor, setFormColor] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [confirmDelete, setConfirmDelete] = useState<{ open: boolean; id?: string }>({ open: false });

  useEffect(() => {
    if (open) {
      loadTags();
    }
  }, [open]);

  const loadTags = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await tagsApi.list();
      setTags(data);
    } catch (err) {
      logger.error('Failed to load tags:', err);
      setError('Failed to load tags: ' + (getApiErrorMessage(err)));
    } finally {
      setLoading(false);
    }
  };

  const handleCreateNew = () => {
    setEditingTag(null);
    setFormName('');
    setFormColor(getRandomColor());
    setFormDescription('');
    setShowForm(true);
  };

  const handleEdit = (tag: SourceTag) => {
    setEditingTag(tag);
    setFormName(tag.name);
    setFormColor(tag.color || getRandomColor());
    setFormDescription(tag.description || '');
    setShowForm(true);
  };

  const handleSave = async () => {
    try {
      setError(null);
      if (editingTag) {
        // Update existing tag
        await tagsApi.update(editingTag.id, {
          name: formName,
          color: formColor,
          description: formDescription || undefined,
        });
      } else {
        // Create new tag
        await tagsApi.create({
          name: formName,
          color: formColor,
          description: formDescription || undefined,
        });
      }
      setShowForm(false);
      await loadTags();
      onTagsChanged?.();
    } catch (err) {
      logger.error('Failed to save tag:', err);
      setError('Failed to save tag: ' + (getApiErrorMessage(err)));
    }
  };

  const handleDelete = (tagId: string) => {
    setConfirmDelete({ open: true, id: tagId });
  };

  const handleConfirmDelete = async () => {
    if (!confirmDelete.id) {
      setConfirmDelete({ open: false });
      return;
    }

    try {
      setError(null);
      await tagsApi.delete(confirmDelete.id);
      await loadTags();
      onTagsChanged?.();
    } catch (err) {
      logger.error('Failed to delete tag:', err);
      setError('Failed to delete tag: ' + (getApiErrorMessage(err)));
    } finally {
      setConfirmDelete({ open: false });
    }
  };

  const handleCancel = () => {
    setShowForm(false);
    setEditingTag(null);
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth slotProps={{
      paper: { sx: ghostDialogPaperSx }
    }}>
      <DialogTitle>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center"
          }}>
          <Typography variant="h6">Manage Tags</Typography>
          {!showForm && (
            <Button
              startIcon={<AddIcon />}
              onClick={handleCreateNew}
              variant="outlined"
              size="small"
              sx={ghostButtonSx(ChaosCypherPalette.primary)}
            >
              New Tag
            </Button>
          )}
        </Box>
      </DialogTitle>
      <DialogContent>
        {error && (
          <Typography color="error" sx={{ mb: 2 }}>
            {error}
          </Typography>
        )}

        {showForm ? (
          <Box sx={{ py: 2 }}>
            <Typography variant="subtitle2" gutterBottom>
              {editingTag ? 'Edit Tag' : 'Create New Tag'}
            </Typography>

            <TextField
              label="Tag Name"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              fullWidth
              sx={{ ...ghostInputSx, mb: 2 }}
              required
            />

            <Typography variant="body2" gutterBottom>
              Color
            </Typography>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 2 }}>
              {PRESET_COLORS.map((color) => (
                  <Tooltip key={color} title={color}>
                    <IconButton
                      aria-label={color}
                      onClick={() => setFormColor(color)}
                      sx={{
                        width: 40,
                        height: 40,
                        bgcolor: color,
                        border: formColor === color ? '3px solid white' : '1px solid #ccc',
                        boxShadow: formColor === color ? 2 : 0,
                        '&:hover': {
                          bgcolor: color,
                          opacity: 0.8,
                        },
                      }}
                    >
                      {formColor === color && <CheckIcon sx={{ color: 'white' }} />}
                    </IconButton>
                  </Tooltip>
              ))}
            </Box>

            <TextField
              label="Custom Color (Hex)"
              value={formColor}
              onChange={(e) => setFormColor(e.target.value)}
              fullWidth
              placeholder="#3f51b5"
              sx={{ ...ghostInputSx, mb: 2 }}
            />

            <TextField
              label="Description (Optional)"
              value={formDescription}
              onChange={(e) => setFormDescription(e.target.value)}
              fullWidth
              multiline
              rows={2}
              sx={{ ...ghostInputSx, mb: 2 }}
            />

            <Box
              sx={{
                display: "flex",
                justifyContent: "flex-end",
                gap: 1
              }}>
              <Button onClick={handleCancel} startIcon={<CloseIcon />} sx={ghostCancelBtnSx}>
                Cancel
              </Button>
              <Button
                onClick={handleSave}
                variant="outlined"
                startIcon={<CheckIcon />}
                disabled={!formName}
                sx={ghostButtonSx(ChaosCypherPalette.primary)}
              >
                {editingTag ? 'Update' : 'Create'}
              </Button>
            </Box>
          </Box>
        ) : (
          <List>
            {tags.length === 0 ? (
              <ListItem>
                <ListItemText
                  primary="No tags yet"
                  secondary="Create a tag to organize your sources"
                />
              </ListItem>
            ) : (
              tags.map((tag) => (
                <ListItem key={tag.id}>
                  <ListItemText
                    primary={
                      <Box
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          gap: 1
                        }}>
                        <Chip
                          label={tag.name}
                          size="small"
                          sx={{
                            bgcolor: tag.color || '#grey',
                            color: 'white',
                            fontWeight: 600,
                          }}
                        />
                      </Box>
                    }
                    secondary={tag.description}
                  />
                  <ListItemSecondaryAction>
                    <IconButton
                      aria-label="Edit tag"
                      edge="end"
                      onClick={() => handleEdit(tag)}
                      sx={{ mr: 1 }}
                    >
                      <EditIcon />
                    </IconButton>
                    <IconButton
                      aria-label="Delete tag"
                      edge="end"
                      onClick={() => handleDelete(tag.id)}
                      color="error"
                    >
                      <DeleteIcon />
                    </IconButton>
                  </ListItemSecondaryAction>
                </ListItem>
              ))
            )}
          </List>
        )}
      </DialogContent>
      {!showForm && (
        <DialogActions>
          <Button onClick={onClose} sx={ghostCancelBtnSx}>Close</Button>
        </DialogActions>
      )}
      <ConfirmDialog
        open={confirmDelete.open}
        title="Confirm Delete"
        message="Are you sure you want to delete this tag? This will unassign it from all sources."
        onConfirm={handleConfirmDelete}
        onCancel={() => setConfirmDelete({ open: false })}
      />
    </Dialog>
  );
}
