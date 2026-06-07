// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * PackageFilters — Ghost-styled search and sort controls for the Lexicon.
 */
import { Box, TextField, MenuItem, InputAdornment } from '@mui/material';
import Search from '@mui/icons-material/Search';
import { ghostInputSx } from '../../theme/ghostStyles';
import { ChaosCypherNeutrals } from '../../theme/palette';
import type { SortOption } from '../../types/lexicon';

interface PackageFiltersProps {
  query: string;
  sortBy: SortOption;
  onQueryChange: (query: string) => void;
  onSortChange: (sort: SortOption) => void;
}

const sortOptions: { value: SortOption; label: string }[] = [
  { value: 'relevance', label: 'Relevance' },
  { value: 'downloads', label: 'Most Downloads' },
  { value: 'updated', label: 'Recently Updated' },
  { value: 'name', label: 'Name (A-Z)' },
];

export function PackageFilters({
  query,
  sortBy,
  onQueryChange,
  onSortChange,
}: PackageFiltersProps) {
  return (
    <Box sx={{ display: 'flex', gap: 2, mb: 3 }}>
      <TextField
        fullWidth
        placeholder="Search packages..."
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        size="small"
        sx={{ maxWidth: 500, ...ghostInputSx }}
        slotProps={{
          input: {
            startAdornment: (
              <InputAdornment position="start">
                <Search sx={{ color: ChaosCypherNeutrals.textMuted }} />
              </InputAdornment>
            ),
          }
        }}
      />
      <TextField
        select
        value={sortBy}
        onChange={(e) => onSortChange(e.target.value as SortOption)}
        size="small"
        sx={{ minWidth: 180, ...ghostInputSx }}
      >
        {sortOptions.map((option) => (
          <MenuItem key={option.value} value={option.value}>
            {option.label}
          </MenuItem>
        ))}
      </TextField>
    </Box>
  );
}
