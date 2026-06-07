// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Box,
  TextField,
  MenuItem,
  IconButton,
  Menu,
  Popover,
  Badge,
  Tooltip,
  Button,
  Typography,
  Divider,
  ListItemIcon,
  ListItemText,
  InputAdornment,
} from '@mui/material';
import SortIcon from '@mui/icons-material/Sort';
import FilterListIcon from '@mui/icons-material/FilterList';
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward';
import ClearIcon from '@mui/icons-material/Clear';
import SearchIcon from '@mui/icons-material/Search';

interface SourcesFiltersProps {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  stageFilter: 'all' | 'queued' | 'processing' | 'active';
  statusFilter: string;
  typeFilter: string;
  sortField: 'created_at' | 'size';
  sortDirection: 'asc' | 'desc';
  onStageChange: (value: 'all' | 'queued' | 'processing' | 'active') => void;
  onStatusChange: (value: string) => void;
  onTypeChange: (value: string) => void;
  onSortChange: (field: 'created_at' | 'size', direction: 'asc' | 'desc') => void;
}

export function SourcesFilters({
  searchQuery,
  onSearchChange,
  stageFilter,
  statusFilter,
  typeFilter,
  sortField,
  sortDirection,
  onStageChange,
  onStatusChange,
  onTypeChange,
  onSortChange,
}: SourcesFiltersProps) {
  // Sort menu state
  const [sortAnchor, setSortAnchor] = useState<HTMLElement | null>(null);
  // Filter popover state
  const [filterAnchor, setFilterAnchor] = useState<HTMLElement | null>(null);

  // Count active filters (excluding 'all' values)
  const activeFilterCount = [
    stageFilter !== 'all',
    statusFilter !== '',
    typeFilter !== '',
  ].filter(Boolean).length;

  // Sort options
  const sortOptions = [
    { field: 'created_at' as const, label: 'Date Added' },
    { field: 'size' as const, label: 'Size' },
  ];

  // Handle sort option click
  const handleSortClick = (field: 'created_at' | 'size') => {
    if (sortField === field) {
      // Toggle direction if same field
      onSortChange(field, sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      // Default to desc for new field
      onSortChange(field, 'desc');
    }
    setSortAnchor(null);
  };

  // Clear all filters
  const handleClearFilters = () => {
    onStageChange('all');
    onStatusChange('');
    onTypeChange('');
  };

  // Get sort direction label
  const getSortLabel = () => {
    const fieldLabel = sortField === 'created_at' ? 'Date' : 'Size';
    const dirLabel = sortDirection === 'desc' ? 'newest' : 'oldest';
    const sizeDir = sortDirection === 'desc' ? 'largest' : 'smallest';
    return `${fieldLabel} (${sortField === 'created_at' ? dirLabel : sizeDir} first)`;
  };

  return (
    <Box sx={{ p: 1.5, mb: 2 }}>
      <Box
        sx={{
          display: "flex",
          gap: 1,
          alignItems: "center"
        }}>
        {/* Search Bar - Ghost Input */}
        <TextField
          size="small"
          placeholder="Search sources..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          sx={{
            flexGrow: 1,
            '& .MuiOutlinedInput-root': {
              backgroundColor: 'transparent',
              '& .MuiOutlinedInput-notchedOutline': {
                borderColor: 'rgba(255, 255, 255, 0.08)',
                borderBottomWidth: 1,
                borderBottomColor: 'rgba(255, 255, 255, 0.08)',
              },
              '&:hover .MuiOutlinedInput-notchedOutline': {
                borderColor: 'rgba(255, 255, 255, 0.15)',
              },
              '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                borderWidth: 1,
                borderColor: 'rgba(0, 229, 255, 0.3)',
              },
            },
          }}
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon sx={{ color: 'action.active' }} />
                </InputAdornment>
              ),
            }
          }}
        />

        {/* Sort button with dropdown menu */}
        <Tooltip title={`Sort by ${getSortLabel()}`}>
          <IconButton aria-label={`Sort by ${getSortLabel()}`} onClick={(e) => setSortAnchor(e.currentTarget)}>
            <SortIcon />
          </IconButton>
        </Tooltip>

        {/* Filter button with badge */}
        <Tooltip title={activeFilterCount > 0 ? `${activeFilterCount} filter(s) active` : 'Filters'}>
          <IconButton aria-label={activeFilterCount > 0 ? `${activeFilterCount} filter(s) active` : 'Filters'} onClick={(e) => setFilterAnchor(e.currentTarget)}>
            <Badge badgeContent={activeFilterCount} color="primary">
              <FilterListIcon />
            </Badge>
          </IconButton>
        </Tooltip>
      </Box>
      {/* Sort Menu */}
      <Menu
        anchorEl={sortAnchor}
        open={Boolean(sortAnchor)}
        onClose={() => setSortAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        {sortOptions.map((option) => (
          <MenuItem
            key={option.field}
            onClick={() => handleSortClick(option.field)}
            selected={sortField === option.field}
          >
            <ListItemIcon>
              {sortField === option.field ? (
                sortDirection === 'desc' ? (
                  <ArrowDownwardIcon fontSize="small" />
                ) : (
                  <ArrowUpwardIcon fontSize="small" />
                )
              ) : null}
            </ListItemIcon>
            <ListItemText>{option.label}</ListItemText>
            {sortField === option.field && (
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  ml: 1
                }}>
                {sortDirection === 'desc' ? '(desc)' : '(asc)'}
              </Typography>
            )}
          </MenuItem>
        ))}
      </Menu>
      {/* Filter Popover */}
      <Popover
        anchorEl={filterAnchor}
        open={Boolean(filterAnchor)}
        onClose={() => setFilterAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Box sx={{ p: 2, minWidth: 220 }}>
          <Typography variant="subtitle2" gutterBottom>
            Filters
          </Typography>

          <TextField
            select
            label="Stage"
            value={stageFilter}
            onChange={(e) => onStageChange(e.target.value as 'all' | 'queued' | 'processing' | 'active')}
            fullWidth
            size="small"
            sx={{ mb: 2 }}
          >
            <MenuItem value="all">All Stages</MenuItem>
            <MenuItem value="queued">Queued</MenuItem>
            <MenuItem value="processing">Processing</MenuItem>
            <MenuItem value="active">Active</MenuItem>
          </TextField>

          <TextField
            select
            label="Status"
            value={statusFilter}
            onChange={(e) => onStatusChange(e.target.value)}
            fullWidth
            size="small"
            sx={{ mb: 2 }}
          >
            <MenuItem value="">All Status</MenuItem>
            <MenuItem value="pending">Pending</MenuItem>
            <MenuItem value="indexing">Indexing</MenuItem>
            <MenuItem value="extracting">Extracting</MenuItem>
            <MenuItem value="extracted">Extracted</MenuItem>
            <MenuItem value="awaiting_confirmation">Awaiting confirmation</MenuItem>
            <MenuItem value="active">Active</MenuItem>
            <MenuItem value="archived">Archived</MenuItem>
            <MenuItem value="error">Error</MenuItem>
          </TextField>

          <TextField
            select
            label="Type"
            value={typeFilter}
            onChange={(e) => onTypeChange(e.target.value)}
            fullWidth
            size="small"
            sx={{ mb: 2 }}
          >
            <MenuItem value="">All Types</MenuItem>
            <MenuItem value="pdf">PDF</MenuItem>
            <MenuItem value="text">Text</MenuItem>
            <MenuItem value="txt">TXT</MenuItem>
            <MenuItem value="csv">CSV</MenuItem>
            <MenuItem value="json">JSON</MenuItem>
            <MenuItem value="html">HTML</MenuItem>
            <MenuItem value="docx">DOCX</MenuItem>
          </TextField>

          {activeFilterCount > 0 && (
            <>
              <Divider sx={{ my: 1 }} />
              <Button
                fullWidth
                size="small"
                startIcon={<ClearIcon />}
                onClick={handleClearFilters}
              >
                Clear Filters
              </Button>
            </>
          )}
        </Box>
      </Popover>
    </Box>
  );
}
