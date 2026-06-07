// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SearchBar: Search items with highlight
 */

import React, { useState } from 'react';
import { TextField, InputAdornment, IconButton } from '@mui/material';
import SearchIcon from '@mui/icons-material/SearchOutlined';
import ClearIcon from '@mui/icons-material/ClearOutlined';
import { ChaosCypherNeutrals } from '../../../theme/palette';

interface SearchBarProps {
  onSearch: (query: string) => void;
}

export const SearchBar: React.FC<SearchBarProps> = ({ onSearch }) => {
  const [query, setQuery] = useState('');

  const handleSearch = (value: string) => {
    setQuery(value);
    onSearch(value);
  };

  const handleClear = () => {
    setQuery('');
    onSearch('');
  };

  return (
    <TextField
      size="small"
      placeholder="Search graph..."
      value={query}
      onChange={(e) => handleSearch(e.target.value)}
      sx={{
        width: { xs: '100%', sm: 300 },
        maxWidth: { xs: '100%', sm: 300 },
        '& .MuiOutlinedInput-root': {
          height: 36,
          fontSize: 13,
          color: 'text.secondary',
          bgcolor: 'transparent',
          borderRadius: '8px',
          '& fieldset': { borderColor: 'rgba(255, 255, 255, 0.05)' },
          '&:hover fieldset': { borderColor: 'rgba(255, 255, 255, 0.12)' },
          '&.Mui-focused fieldset': {
            borderColor: 'rgba(0, 229, 255, 0.4)',
            borderWidth: '1px',
          },
        },
        '& input::placeholder': { color: ChaosCypherNeutrals.textMuted, opacity: 1 },
      }}
      slotProps={{
        input: {
          startAdornment: (
            <InputAdornment position="start">
              <SearchIcon sx={{ fontSize: 18, opacity: 0.5 }} />
            </InputAdornment>
          ),
          endAdornment: query && (
            <InputAdornment position="end">
              <IconButton aria-label="Clear search" size="small" onClick={handleClear}>
                <ClearIcon sx={{ fontSize: 16 }} />
              </IconButton>
            </InputAdornment>
          ),
        }
      }}
    />
  );
};
