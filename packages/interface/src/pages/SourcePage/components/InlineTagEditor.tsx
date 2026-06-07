// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Inline tag editor for assigning, creating, and removing tags on a source.
 *
 * Displays assigned tags as removable chips with an inline autocomplete
 * input for adding existing tags or creating new ones on the fly.
 *
 * Server state (assigned tags + the global tag catalog) is managed by
 * TanStack Query via the source-scoped `useSourceTags` hooks; the
 * add/remove/create flows are mutations that invalidate the
 * `['source', sourceId, 'tags']` key so the chips re-fetch automatically.
 */
import { useState, useRef, useEffect } from 'react';
import {
  Box,
  Chip,
  IconButton,
  Tooltip,
  Autocomplete,
  TextField,
  Typography,
  ClickAwayListener,
  createFilterOptions,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import SettingsIcon from '@mui/icons-material/Settings';
import {
  useSourceAssignedTags,
  useAllTags,
  useAssignTag,
  useUnassignTag,
  useCreateAndAssignTag,
  useRefreshTags,
} from '../../../services/api/useSourceTags';
import type { SourceTag } from '../../../types';
import TagManager from '../../../components/TagManager';
import { TagPalette } from '../../../theme/colors';
import { logger } from '../../../utils/logger';

type TagOption = SourceTag | { id: '__create__'; name: string; inputValue: string };

const tagFilter = createFilterOptions<TagOption>();

const getRandomColor = () => TagPalette[Math.floor(Math.random() * TagPalette.length)];

const EMPTY_TAGS: SourceTag[] = [];

interface InlineTagEditorProps {
  sourceId: string;
}

export function InlineTagEditor({ sourceId }: InlineTagEditorProps) {
  const [tagManagerOpen, setTagManagerOpen] = useState(false);
  const [showTagInput, setShowTagInput] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const isProcessing = useRef(false);

  const { data: assignedTags = EMPTY_TAGS, error: assignedError } =
    useSourceAssignedTags(sourceId);
  const { data: availableTags = EMPTY_TAGS, error: availableError } = useAllTags();

  // Surface load failures the same way the legacy hook did (log-only — the
  // editor degrades gracefully to empty lists).
  useEffect(() => {
    if (assignedError) {
      logger.error('Failed to load tags:', assignedError);
    }
  }, [assignedError]);
  useEffect(() => {
    if (availableError) {
      logger.error('Failed to load available tags:', availableError);
    }
  }, [availableError]);

  const assignTag = useAssignTag(sourceId);
  const unassignTag = useUnassignTag(sourceId);
  const createAndAssignTag = useCreateAndAssignTag(sourceId);
  const refreshTags = useRefreshTags(sourceId);

  const closeInput = () => {
    setShowTagInput(false);
    setInputValue('');
  };

  const handleAssignTag = async (tagId: string) => {
    try {
      await assignTag.mutateAsync(tagId);
    } catch (err) {
      logger.error('Failed to assign tag:', err);
    }
  };

  const handleUnassignTag = async (tagId: string) => {
    try {
      await unassignTag.mutateAsync(tagId);
    } catch (err) {
      logger.error('Failed to unassign tag:', err);
    }
  };

  const handleTagSelected = async (_event: unknown, value: string | TagOption | null) => {
    if (!value) return;
    isProcessing.current = true;

    try {
      if (typeof value === 'string') {
        const trimmed = value.trim();
        if (!trimmed) return;
        const existing = unassignedTags.find((t) => t.name.toLowerCase() === trimmed.toLowerCase());
        if (existing) {
          await handleAssignTag(existing.id);
        } else {
          await createAndAssignTag.mutateAsync({ name: trimmed, color: getRandomColor() });
        }
      } else if ('inputValue' in value) {
        await createAndAssignTag.mutateAsync({ name: value.inputValue, color: getRandomColor() });
      } else {
        await handleAssignTag(value.id);
      }
    } catch (err) {
      logger.error('Failed to add tag:', err);
    } finally {
      isProcessing.current = false;
      closeInput();
    }
  };

  const handleClickAway = () => {
    if (!isProcessing.current && !tagManagerOpen) {
      closeInput();
    }
  };

  const handleTagsChanged = () => {
    refreshTags();
  };

  const unassignedTags = availableTags.filter(
    (tag) => !assignedTags.find((t) => t.id === tag.id),
  );

  return (
    <>
      <Box
        sx={{
          display: "flex",
          flexWrap: "wrap",
          gap: 0.75,
          alignItems: "center"
        }}>
        {assignedTags.map((tag) => (
          <Chip
            key={tag.id}
            label={tag.name}
            size="medium"
            onDelete={() => handleUnassignTag(tag.id)}
            sx={{
              bgcolor: tag.color || '#grey',
              color: 'white',
              fontSize: '0.8rem',
              height: 28,
              '& .MuiChip-deleteIcon': {
                color: 'rgba(255, 255, 255, 0.7)',
                fontSize: '16px',
                '&:hover': { color: 'white' },
              },
            }}
          />
        ))}

        {showTagInput ? (
          <ClickAwayListener onClickAway={handleClickAway}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center"
              }}>
              <Autocomplete<TagOption, false, false, true>
                size="small"
                freeSolo
                openOnFocus
                autoHighlight
                selectOnFocus
                handleHomeEndKeys
                inputValue={inputValue}
                onInputChange={(_e, val) => setInputValue(val)}
                options={unassignedTags as TagOption[]}
                getOptionLabel={(option) => {
                  if (typeof option === 'string') return option;
                  if ('inputValue' in option) return option.inputValue;
                  return option.name;
                }}
                filterOptions={(options, params) => {
                  const filtered = tagFilter(options, params);
                  const input = params.inputValue.trim();
                  if (input && !availableTags.some((t) => t.name.toLowerCase() === input.toLowerCase())) {
                    filtered.push({ id: '__create__', name: `Create "${input}"`, inputValue: input });
                  }
                  return filtered;
                }}
                renderOption={(props, option) => {
                  const { key, ...rest } = props;
                  const isCreate = 'inputValue' in option;
                  return (
                    <li key={key} {...rest}>
                      <Box
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          gap: 1
                        }}>
                        {!isCreate && (
                          <Box
                            sx={{
                              width: 12,
                              height: 12,
                              borderRadius: '50%',
                              bgcolor: (option as SourceTag).color || '#grey',
                              flexShrink: 0,
                            }}
                          />
                        )}
                        <Typography variant="body2" sx={{ fontStyle: isCreate ? 'italic' : 'normal' }}>
                          {option.name}
                        </Typography>
                      </Box>
                    </li>
                  );
                }}
                onChange={handleTagSelected}
                sx={{ minWidth: 180, maxWidth: 240 }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    placeholder="Type tag name..."
                    variant="standard"
                    autoFocus
                    sx={{
                      '& .MuiInput-underline:before': { borderBottom: '1px solid', borderColor: 'divider' },
                      '& .MuiInputBase-input': { fontSize: '0.85rem', py: 0.5 },
                    }}
                  />
                )}
              />
            </Box>
          </ClickAwayListener>
        ) : (
          <Chip
            icon={<AddIcon sx={{ fontSize: 16 }} />}
            label="Add tag"
            size="medium"
            variant="outlined"
            onClick={() => setShowTagInput(true)}
            sx={{
              height: 28,
              fontSize: '0.8rem',
              cursor: 'pointer',
              borderStyle: 'dashed',
              opacity: 0.7,
              '&:hover': { opacity: 1 },
            }}
          />
        )}

        <Tooltip title="Manage tags">
          <IconButton
            aria-label="Manage tags"
            size="small"
            onClick={() => setTagManagerOpen(true)}
            sx={{ opacity: 0.5, '&:hover': { opacity: 1 }, flexShrink: 0 }}
          >
            <SettingsIcon sx={{ fontSize: 16 }} />
          </IconButton>
        </Tooltip>
      </Box>
      <TagManager
        open={tagManagerOpen}
        onClose={() => setTagManagerOpen(false)}
        onTagsChanged={handleTagsChanged}
      />
    </>
  );
}
