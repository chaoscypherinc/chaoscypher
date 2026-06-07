// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * CanvasControlMenus: Popup menus for canvas controls
 * - Layout & Display settings
 * - Filters
 * - Keyboard shortcuts
 */

import React, { useEffect, useMemo } from 'react';
import {
  Popover,
  Box,
  Typography,
  Switch,
  FormControlLabel,
  Divider,
  Chip,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  OutlinedInput,
  Checkbox,
  SelectChangeEvent,
  Button,
} from '@mui/material';
import SaveIcon from '@mui/icons-material/SaveOutlined';
import FolderOpenIcon from '@mui/icons-material/FolderOpenOutlined';
import { Template } from '../../../types';
import { useAppConfig } from '../../../contexts/useAppConfig';
import { useTemplates } from '../../../services/api/useTemplates';
import { useSourceSummaries } from '../../../services/api/useSources';
import { hexToRgba } from '../../../theme/cardStyles';
import { GraphColors } from '../../../theme/colors';
import { Overlays } from '../../../theme/overlays';
import { logger } from '../../../utils/logger';

// Layout & Display Menu
interface LayoutDisplayMenuProps {
  anchorEl: HTMLElement | null;
  open: boolean;
  onClose: () => void;
  showLabels: boolean;
  onShowLabelsChange: (show: boolean) => void;
  onSaveLayout?: () => void;
  onLoadLayout?: () => void;
}

export const LayoutDisplayMenu: React.FC<LayoutDisplayMenuProps> = ({
  anchorEl,
  open,
  onClose,
  showLabels,
  onShowLabelsChange,
  onSaveLayout,
  onLoadLayout,
}) => {
  const POPUP_GREY = GraphColors.popupBase;

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{
        vertical: 'bottom',
        horizontal: 'right',
      }}
      transformOrigin={{
        vertical: 'top',
        horizontal: 'right',
      }}
      slotProps={{
        paper: {
          sx: {
            p: 2,
            width: 280,
            borderRadius: 2,
            bgcolor: hexToRgba(POPUP_GREY, 0.9),
            border: `1px solid ${hexToRgba(POPUP_GREY, 0.5)}`,
            boxShadow: 4,
            backdropFilter: 'blur(10px)',
            '& .MuiTypography-colorTextSecondary': {
              color: Overlays.prominent.dark, // Better contrast on grey
            },
          }
        }
      }}
    >
      <Typography variant="subtitle1" gutterBottom sx={{
        fontWeight: 600
      }}>
        Display Settings
      </Typography>
      <Box sx={{ mt: 2 }}>
        <Typography variant="subtitle2" gutterBottom sx={{ mb: 1 }}>
          Display Options
        </Typography>
        <FormControlLabel
          control={
            <Switch
              checked={showLabels}
              onChange={(e) => onShowLabelsChange(e.target.checked)}
            />
          }
          label="Show node labels"
        />
      </Box>
      {(onSaveLayout || onLoadLayout) && (
        <>
          <Divider sx={{ my: 2 }} />
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            <Typography variant="subtitle2" gutterBottom>
              Saved Layouts
            </Typography>
            {onLoadLayout && (
              <Button
                variant="outlined"
                size="small"
                startIcon={<FolderOpenIcon />}
                onClick={onLoadLayout}
                fullWidth
                sx={{
                  color: Overlays.strong.dark,
                  borderColor: Overlays.border.dark,
                  '&:hover': {
                    borderColor: Overlays.borderHover.dark,
                    bgcolor: Overlays.light.dark,
                  }
                }}
              >
                Load Layout
              </Button>
            )}
            {onSaveLayout && (
              <Button
                variant="outlined"
                size="small"
                startIcon={<SaveIcon />}
                onClick={onSaveLayout}
                fullWidth
                sx={{
                  color: Overlays.strong.dark,
                  borderColor: Overlays.border.dark,
                  '&:hover': {
                    borderColor: Overlays.borderHover.dark,
                    bgcolor: Overlays.light.dark,
                  }
                }}
              >
                Save Layout
              </Button>
            )}
          </Box>
        </>
      )}
    </Popover>
  );
};

// Filters Menu
interface FiltersMenuProps {
  anchorEl: HTMLElement | null;
  open: boolean;
  onClose: () => void;
  selectedTemplateFilters: string[];
  onTemplateFiltersChange: (templateIds: string[]) => void;
  selectedSourceFilters: string[];
  onSourceFiltersChange: (sourceIds: string[]) => void;
}

export const FiltersMenu: React.FC<FiltersMenuProps> = ({
  anchorEl,
  open,
  onClose,
  selectedTemplateFilters,
  onTemplateFiltersChange,
  selectedSourceFilters,
  onSourceFiltersChange,
}) => {
  const POPUP_GREY = GraphColors.popupBase;
  const config = useAppConfig();
  const sourcePageSize = config.batch_graph_source_page_size;

  // Node-template list (lens/workflow excluded) for the "Show Templates" filter.
  const { data: allTemplates, isError: templatesError, error: templatesQueryError } =
    useTemplates('node', { enabled: open });

  // Source summaries for the "Source Documents" filter (page-size lookup).
  const {
    data: sourcesData,
    isError: sourcesError,
    error: sourcesQueryError,
  } = useSourceSummaries(sourcePageSize, { enabled: open });
  const sources = useMemo(() => sourcesData ?? [], [sourcesData]);

  const templates = useMemo<Template[]>(
    () =>
      (allTemplates ?? []).filter(t => {
        if (t.template_type !== 'node') return false;
        const templateId = t.id.toLowerCase();
        return !templateId.includes('lens') && !templateId.includes('workflow');
      }),
    [allTemplates],
  );

  useEffect(() => {
    if (templatesError) {
      logger.error('Error loading templates:', templatesQueryError);
    }
  }, [templatesError, templatesQueryError]);

  useEffect(() => {
    if (sourcesError) {
      logger.error('Error loading sources:', sourcesQueryError);
    }
  }, [sourcesError, sourcesQueryError]);

  const handleTemplateFilterChange = (event: SelectChangeEvent<string[]>) => {
    const value = event.target.value;
    const newFilters = typeof value === 'string' ? value.split(',') : value;
    onTemplateFiltersChange(newFilters);
  };

  const handleSourceFilterChange = (event: SelectChangeEvent<string[]>) => {
    const value = event.target.value;
    const newFilters = typeof value === 'string' ? value.split(',') : value;
    onSourceFiltersChange(newFilters);
  };

  const selectSx = {
    '& .MuiOutlinedInput-notchedOutline': {
      borderColor: Overlays.border.dark,
    },
    '&:hover .MuiOutlinedInput-notchedOutline': {
      borderColor: Overlays.borderHover.dark,
    },
    '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
      borderColor: Overlays.prominent.dark,
    },
  };

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{
        vertical: 'bottom',
        horizontal: 'left',
      }}
      transformOrigin={{
        vertical: 'top',
        horizontal: 'left',
      }}
      slotProps={{
        paper: {
          sx: {
            p: 2,
            width: 320,
            borderRadius: 2,
            bgcolor: hexToRgba(POPUP_GREY, 0.9),
            border: `1px solid ${hexToRgba(POPUP_GREY, 0.5)}`,
            boxShadow: 4,
            backdropFilter: 'blur(10px)',
            maxHeight: 500,
            '& .MuiTypography-colorTextSecondary': {
              color: Overlays.prominent.dark,
            },
          }
        }
      }}
    >
      <Typography variant="subtitle1" gutterBottom sx={{
        fontWeight: 600
      }}>
        Filters
      </Typography>
      {/* Source Documents Filter */}
      <Typography variant="caption" sx={{ display: 'block', mb: 1, color: Overlays.prominent.dark }}>
        Filter by source documents. Leave empty to show all.
      </Typography>
      {sources.length > 0 ? (
        <>
          <FormControl fullWidth size="small">
            <InputLabel sx={{ color: Overlays.prominent.dark }}>Source Documents</InputLabel>
            <Select
              multiple
              value={selectedSourceFilters}
              onChange={handleSourceFilterChange}
              input={<OutlinedInput label="Source Documents" />}
              renderValue={(selected) => (
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                  {selected.map((value) => {
                    const source = sources.find(s => s.id === value);
                    return (
                      <Chip
                        key={value}
                        label={source?.title || source?.filename || value}
                        size="small"
                        sx={{ height: 24 }}
                      />
                    );
                  })}
                </Box>
              )}
              MenuProps={{
                slotProps: {
                  paper: {
                    sx: {
                      maxHeight: 300,
                    }
                  }
                }
              }}
              sx={selectSx}
            >
              {sources.map((source) => (
                <MenuItem key={source.id} value={source.id}>
                  <Checkbox checked={selectedSourceFilters.indexOf(source.id) > -1} />
                  <Typography variant="body2" noWrap>{source.title || source.filename}</Typography>
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          {selectedSourceFilters.length > 0 && (
            <Typography variant="caption" sx={{ display: 'block', mt: 1, fontWeight: 500, color: Overlays.strong.dark }}>
              Showing {selectedSourceFilters.length} of {sources.length} sources
            </Typography>
          )}
        </>
      ) : (
        <Typography variant="body2" sx={{
          color: "text.secondary"
        }}>
          No sources available
        </Typography>
      )}
      <Divider sx={{ my: 2 }} />
      {/* Template Type Filter */}
      <Typography variant="caption" sx={{ display: 'block', mb: 1, color: Overlays.prominent.dark }}>
        Filter items by template type. Leave empty to show all.
      </Typography>
      {templates.length > 0 ? (
        <>
          <FormControl fullWidth size="small">
            <InputLabel sx={{ color: Overlays.prominent.dark }}>Show Templates</InputLabel>
            <Select
              multiple
              value={selectedTemplateFilters}
              onChange={handleTemplateFilterChange}
              input={<OutlinedInput label="Show Templates" />}
              renderValue={(selected) => (
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                  {selected.map((value) => {
                    const template = templates.find(t => t.id === value);
                    return (
                      <Chip
                        key={value}
                        label={template?.name || value}
                        size="small"
                        sx={{ height: 24 }}
                      />
                    );
                  })}
                </Box>
              )}
              MenuProps={{
                slotProps: {
                  paper: {
                    sx: {
                      maxHeight: 300,
                    }
                  }
                }
              }}
              sx={selectSx}
            >
              {templates.map((template) => (
                <MenuItem key={template.id} value={template.id}>
                  <Checkbox checked={selectedTemplateFilters.indexOf(template.id) > -1} />
                  <Typography variant="body2">{template.name}</Typography>
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          {selectedTemplateFilters.length > 0 && (
            <Typography variant="caption" sx={{ display: 'block', mt: 1, fontWeight: 500, color: Overlays.strong.dark }}>
              Showing {selectedTemplateFilters.length} of {templates.length} template types
            </Typography>
          )}
        </>
      ) : (
        <Typography variant="body2" sx={{
          color: "text.secondary"
        }}>
          No templates available
        </Typography>
      )}
    </Popover>
  );
};

// Keyboard Shortcuts Menu
interface KeyboardShortcutsMenuProps {
  anchorEl: HTMLElement | null;
  open: boolean;
  onClose: () => void;
}

const ShortcutItem: React.FC<{ keys: string[]; description: string }> = ({ keys, description }) => (
  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 2, py: 0.75 }}>
    <Typography variant="body2" sx={{ flex: 1, fontSize: '0.875rem' }}>
      {description}
    </Typography>
    <Box sx={{ display: 'flex', gap: 0.5, flexShrink: 0 }}>
      {keys.map((key, idx) => (
        <React.Fragment key={key}>
          {idx > 0 && <Typography variant="caption" sx={{ mx: 0.5, color: 'text.disabled' }}>or</Typography>}
          <Chip
            label={key}
            size="small"
            sx={{
              height: 22,
              fontSize: '0.7rem',
              fontFamily: 'monospace',
              bgcolor: 'action.hover',
            }}
          />
        </React.Fragment>
      ))}
    </Box>
  </Box>
);

export const KeyboardShortcutsMenu: React.FC<KeyboardShortcutsMenuProps> = ({
  anchorEl,
  open,
  onClose,
}) => {
  const POPUP_GREY = GraphColors.popupBase;

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{
        vertical: 'bottom',
        horizontal: 'right',
      }}
      transformOrigin={{
        vertical: 'top',
        horizontal: 'right',
      }}
      slotProps={{
        paper: {
          sx: {
            p: 2,
            width: 400,
            borderRadius: 2,
            bgcolor: hexToRgba(POPUP_GREY, 0.9),
            border: `1px solid ${hexToRgba(POPUP_GREY, 0.5)}`,
            boxShadow: 4,
            backdropFilter: 'blur(10px)',
            maxHeight: 500,
            overflowY: 'auto',
            '& .MuiTypography-colorTextSecondary': {
              color: Overlays.prominent.dark, // Better contrast on grey
            },
          }
        }
      }}
    >
      <Typography variant="subtitle1" gutterBottom sx={{
        fontWeight: 600
      }}>
        Keyboard Shortcuts
      </Typography>
      <Box sx={{ mt: 2 }}>
        <ShortcutItem keys={['Delete', 'Backspace']} description="Delete selected item/link" />
        <ShortcutItem keys={['F']} description="Fit view to canvas" />
        <ShortcutItem keys={['+', '=']} description="Zoom in" />
        <ShortcutItem keys={['-']} description="Zoom out" />
        <ShortcutItem keys={['Escape']} description="Deselect all" />
        <ShortcutItem keys={['D']} description="Duplicate selected item" />

        <Divider sx={{ my: 2 }} />

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          <Typography variant="caption" sx={{ fontStyle: 'italic', color: Overlays.prominent.dark }}>
            • Double-click a node to open the properties panel
          </Typography>
          <Typography variant="caption" sx={{ fontStyle: 'italic', color: Overlays.prominent.dark }}>
            • Right-click on canvas to create new items
          </Typography>
          <Typography variant="caption" sx={{ fontStyle: 'italic', color: Overlays.prominent.dark }}>
            • Drag nodes to reposition them
          </Typography>
          <Typography variant="caption" sx={{ fontStyle: 'italic', color: Overlays.prominent.dark }}>
            • Use "Create Link" to connect nodes
          </Typography>
        </Box>
      </Box>
    </Popover>
  );
};
