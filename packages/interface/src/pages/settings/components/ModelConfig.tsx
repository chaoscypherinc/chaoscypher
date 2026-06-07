// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ModelConfig: Shared model configuration sub-components.
 *
 * Contains reusable autocomplete and slider components used by all
 * provider-specific model selectors, including OllamaAutocomplete,
 * CloudModelAutocomplete, and ContextWindowSlider.
 */

import { useState, useRef } from 'react';
import {
  Box,
  Typography,
  TextField,
  Chip,
  Slider,
  Autocomplete,
  IconButton,
  Menu,
  MenuItem,
  LinearProgress,
  ListItemIcon,
  ListItemText,
  Tooltip,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import DownloadIcon from '@mui/icons-material/Download';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import DeleteOutlinedIcon from '@mui/icons-material/DeleteOutlined';
import type { CloudModelInfo } from '../../../types';
import { ACCENT_COLORS } from '../../../theme/accentStyles';
import { formatTokens, numberInputSx } from './modelConfigStyles';

// ---------------------------------------------------------------------------
// Ollama Autocomplete
// ---------------------------------------------------------------------------

/** Extended option type used internally for grouping pretested and other installed models. */
interface OllamaGroupedOption {
  id: string;
  name: string;
  description?: string;
  group: 'Recommended' | 'Other Installed';
}

interface OllamaAutocompleteProps {
  /** Label for the autocomplete input. */
  label: string;
  /** Available model options with name and description. */
  options: { id: string; name: string; description: string }[];
  /** Currently selected model ID. */
  value: string;
  /** Called when a model option is selected. */
  onChange: (value: string) => void;
  /** Called when the text input changes (for freeSolo typing). */
  onInputChange: (value: string) => void;
  /** Set of installed model names from Ollama. */
  installedModels?: Set<string>;
  /** Active pull progress keyed by model name. */
  pullProgress?: Record<string, { status: string; completed: number; total: number }>;
  /** Models installed but not in the pretested list. */
  otherInstalledModels?: { id: string; name: string }[];
  /** Callback to pull a model. */
  onPull?: (modelId: string) => void;
  /** Callback to remove a model. */
  onRemove?: (modelId: string) => void;
  /** Callback to show model info. */
  onShowInfo?: (modelId: string) => void;
}

/** Renders an autocomplete for local Ollama models with install status, pull, and remove. */
export function OllamaAutocomplete({
  label,
  options,
  value,
  onChange,
  onInputChange,
  installedModels,
  pullProgress,
  otherInstalledModels,
  onPull,
  onRemove,
  onShowInfo,
}: OllamaAutocompleteProps) {
  const [menuState, setMenuState] = useState<{ modelId: string; top: number; left: number } | null>(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const skipCloseRef = useRef(false);

  // Merge pretested options with other installed models into grouped list
  const groupedOptions: OllamaGroupedOption[] = [
    ...options.map((o) => ({ ...o, group: 'Recommended' as const })),
    ...(otherInstalledModels || []).map((o) => ({ ...o, group: 'Other Installed' as const })),
  ];

  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>, modelId: string) => {
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    skipCloseRef.current = true;
    setMenuState({ modelId, top: rect.bottom, left: rect.right });
  };

  const handleMenuClose = () => {
    setMenuState(null);
    setDropdownOpen(false);
  };

  return (
    <>
      <Autocomplete
        freeSolo
        open={dropdownOpen}
        onOpen={() => setDropdownOpen(true)}
        onClose={() => {
          if (skipCloseRef.current) {
            skipCloseRef.current = false;
            return;
          }
          setDropdownOpen(false);
        }}
        options={groupedOptions}
        groupBy={(option) => typeof option === 'string' ? '' : option.group}
        getOptionLabel={(option) => typeof option === 'string' ? option : option.id}
        value={value}
        onChange={(_, newValue) => {
          const modelId = typeof newValue === 'string' ? newValue : newValue?.id || '';
          onChange(modelId);
          setDropdownOpen(false);
        }}
        onInputChange={(_, newInputValue) => {
          onInputChange(newInputValue);
        }}
        renderOption={(props, option) => {
          const isPlaceholder = !option.id;
          const isInstalled = !isPlaceholder && (installedModels?.has(option.id) ?? false);
          const isPulling = !isPlaceholder && (pullProgress?.[option.id] !== undefined);
          const progress = pullProgress?.[option.id];
          const isPretested = option.group === 'Recommended';

          return (
            <Box component="li" {...props} key={option.id || '__none__'} sx={{ display: 'flex', flexDirection: 'column', alignItems: 'stretch !important', py: 0.5 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                {/* Left icon: installed or not (skip for placeholder entries) */}
                {isPlaceholder ? null : isInstalled ? (
                  <CheckCircleIcon color="success" sx={{ fontSize: 18, flexShrink: 0 }} />
                ) : (
                  <DownloadIcon sx={{ fontSize: 18, flexShrink: 0, color: 'text.disabled' }} />
                )}

                {/* Center: model info */}
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Typography variant="body2" noWrap sx={{
                      fontWeight: "medium"
                    }}>{option.name}</Typography>
                    <Typography variant="caption" noWrap sx={{
                      color: "text.secondary"
                    }}>{option.id}</Typography>
                    {!isPretested && (
                      <Chip label="Untested" size="small" variant="outlined" sx={{ height: 18, fontSize: '0.65rem', color: 'text.disabled', borderColor: 'divider' }} />
                    )}
                  </Box>
                  {isPretested && option.description && (
                    <Typography variant="caption" sx={{
                      color: "text.secondary"
                    }}>{option.description}</Typography>
                  )}
                </Box>

                {/* Right action: three-dot menu or download (skip for placeholder) */}
                {isPlaceholder ? null : isInstalled ? (
                  <IconButton
                    aria-label="More actions"
                    size="small"
                    onMouseDown={(e) => { e.stopPropagation(); e.preventDefault(); }}
                    onClick={(e) => { e.stopPropagation(); e.preventDefault(); handleMenuOpen(e, option.id); }}
                    sx={{ flexShrink: 0 }}
                  >
                    <MoreVertIcon fontSize="small" />
                  </IconButton>
                ) : !isPulling ? (
                  <Tooltip title={`Download ${option.name}`}>
                    <IconButton
                      aria-label={`Download ${option.name}`}
                      size="small"
                      onMouseDown={(e) => { e.stopPropagation(); e.preventDefault(); }}
                      onClick={(e) => { e.stopPropagation(); e.preventDefault(); onPull?.(option.id); }}
                      sx={{ flexShrink: 0 }}
                    >
                      <DownloadIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                ) : null}
              </Box>
              {/* Pull progress bar */}
              {isPulling && progress && (
                <Box sx={{ width: '100%', mt: 0.5 }}>
                  <LinearProgress
                    variant={progress.total > 0 ? 'determinate' : 'indeterminate'}
                    value={progress.total > 0 ? (progress.completed / progress.total) * 100 : 0}
                    sx={{ height: 3, borderRadius: 1 }}
                  />
                  <Typography
                    variant="caption"
                    sx={{
                      color: "text.secondary",
                      fontSize: '0.65rem'
                    }}>
                    {progress.status}
                    {progress.total > 0 && ` (${Math.round((progress.completed / progress.total) * 100)}%)`}
                  </Typography>
                </Box>
              )}
            </Box>
          );
        }}
        renderInput={(params) => {
          // Show error state if a model is selected but not installed
          const hasValue = value && value.trim().length > 0;
          const isModelMissing = hasValue && installedModels && installedModels.size > 0 && !installedModels.has(value);
          return (
            <TextField
              {...params}
              label={label}
              variant="outlined"
              error={!!isModelMissing}
              helperText={isModelMissing ? `${value} is not installed` : undefined}
            />
          );
        }}
        // Three Ollama autocompletes share a row, so the input is narrow.
        // Decouple the dropdown width from the input so model name + id +
        // description aren't truncated to "Qwen3...".
        slotProps={{
          popper: { style: { width: 'fit-content' }, placement: 'bottom-start' },
          paper: { sx: { minWidth: 380, maxWidth: 'min(560px, 92vw)' } },
        }}
        sx={{ flex: 1, minWidth: 0 }}
      />
      {/* Three-dot context menu — anchored by position since dropdown may close */}
      <Menu
        open={Boolean(menuState)}
        onClose={handleMenuClose}
        anchorReference="anchorPosition"
        anchorPosition={menuState ? { top: menuState.top, left: menuState.left } : undefined}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <MenuItem onClick={() => { if (menuState) onShowInfo?.(menuState.modelId); handleMenuClose(); }}>
          <ListItemIcon><InfoOutlinedIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Model Info</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => { if (menuState) onRemove?.(menuState.modelId); handleMenuClose(); }}>
          <ListItemIcon><DeleteOutlinedIcon fontSize="small" color="error" /></ListItemIcon>
          <ListItemText sx={{ color: 'error.main' }}>Remove</ListItemText>
        </MenuItem>
      </Menu>
    </>
  );
}

// ---------------------------------------------------------------------------
// Cloud Provider Autocomplete
// ---------------------------------------------------------------------------

interface CloudModelAutocompleteProps {
  /** Label for the autocomplete input. */
  label: string;
  /** Optional helper text below the input. */
  helperText?: string;
  /** Available cloud model options. */
  options: CloudModelInfo[];
  /** Currently selected model ID. */
  value: string;
  /** Called when a model option is selected or cleared. */
  onChange: (value: string | null, option?: CloudModelInfo) => void;
  /** Called when the text input changes (for freeSolo typing). */
  onInputChange: (value: string) => void;
  /** Flex grow factor (default: 1). */
  flex?: number;
}

/** Shared autocomplete for cloud provider models with pricing/context info. */
export function CloudModelAutocomplete({
  label,
  helperText,
  options,
  value,
  onChange,
  onInputChange,
  flex = 1,
}: CloudModelAutocompleteProps) {
  return (
    <Autocomplete
      freeSolo
      options={options}
      getOptionLabel={(option) => typeof option === 'string' ? option : option.id}
      value={value}
      onChange={(_, newValue) => {
        if (typeof newValue === 'string') {
          onChange(newValue);
        } else if (newValue) {
          onChange(newValue.id, newValue);
        } else {
          onChange(null);
        }
      }}
      onInputChange={(_, newInputValue) => {
        onInputChange(newInputValue);
      }}
      renderOption={(props, option) => (
        <Box component="li" {...props} sx={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start !important' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant="body2" sx={{
              fontWeight: "medium"
            }}>{option.display_name}</Typography>
            {option.recommended && <Chip size="small" label="Recommended" color="primary" />}
          </Box>
          <Typography variant="caption" sx={{
            color: "text.secondary"
          }}>
            {formatTokens(option.context_window || 0)} context, {formatTokens(option.max_output_tokens || 0)} output
          </Typography>
        </Box>
      )}
      renderInput={(params) => (
        <TextField
          {...params}
          label={label}
          variant="outlined"
          {...(helperText && { helperText })}
        />
      )}
      sx={{ flex }}
    />
  );
}

// ---------------------------------------------------------------------------
// Context Window Slider
// ---------------------------------------------------------------------------

interface ContextWindowSliderProps {
  /** Current context window size in tokens. */
  contextValue: number;
  /** Called when the context window slider changes. */
  onContextChange: (value: number) => void;
  /** Minimum context window size. */
  contextMin: number;
  /** Maximum context window size. */
  contextMax: number;
  /** Step size for the context window slider. */
  contextStep: number;
  /** Marks to display on the context window slider. */
  contextMarks: { value: number; label: string }[];
  /** Optional current max output token value. */
  outputValue?: number;
  /** Called when the max output slider changes. */
  onOutputChange?: (value: number) => void;
  /** Minimum max output tokens. */
  outputMin?: number;
  /** Maximum max output tokens. */
  outputMax?: number;
  /** Step size for the output slider. */
  outputStep?: number;
  /** Marks to display on the output slider. */
  outputMarks?: { value: number; label: string }[];
}

/** Reusable context window (and optionally max output) slider section. */
export function ContextWindowSlider({
  contextValue,
  onContextChange,
  contextMin,
  contextMax,
  contextStep,
  contextMarks,
  outputValue,
  onOutputChange,
  outputMin,
  outputMax,
  outputStep,
  outputMarks,
}: ContextWindowSliderProps) {
  return (
    <Box sx={{ mt: 2, py: 1, pr: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, ...(outputValue !== undefined && { mb: 2 }) }}>
        <TextField
          label="Context Window"
          type="number"
          size="small"
          variant="outlined"
          value={contextValue}
          onChange={(e) => onContextChange(parseInt(e.target.value) || contextMin)}
          sx={{ width: 140, ...numberInputSx }}
          slotProps={{ htmlInput: { min: contextMin, step: contextStep } }}
        />
        <Slider
          value={contextValue}
          onChange={(_, value) => onContextChange(value as number)}
          min={contextMin}
          max={contextMax}
          step={contextStep}
          marks={contextMarks}
          valueLabelDisplay="auto"
          valueLabelFormat={formatTokens}
          sx={{ flex: 1, color: ACCENT_COLORS.info, '& .MuiSlider-thumb': { bgcolor: ACCENT_COLORS.info }, '& .MuiSlider-track': { bgcolor: ACCENT_COLORS.info } }}
        />
      </Box>
      {outputValue !== undefined && onOutputChange && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <TextField
            label="Max Output"
            type="number"
            size="small"
            variant="outlined"
            value={outputValue}
            onChange={(e) => onOutputChange(parseInt(e.target.value) || (outputMin || 1024))}
            sx={{ width: 140, ...numberInputSx }}
            slotProps={{ htmlInput: { min: outputMin || 1024, step: outputStep || 1024 } }}
          />
          <Slider
            value={outputValue}
            onChange={(_, value) => onOutputChange(value as number)}
            min={outputMin || 1024}
            max={outputMax || 100000}
            step={outputStep || 1024}
            marks={outputMarks || []}
            valueLabelDisplay="auto"
            valueLabelFormat={formatTokens}
            sx={{ flex: 1, color: ACCENT_COLORS.info, '& .MuiSlider-thumb': { bgcolor: ACCENT_COLORS.info }, '& .MuiSlider-track': { bgcolor: ACCENT_COLORS.info } }}
          />
        </Box>
      )}
    </Box>
  );
}
