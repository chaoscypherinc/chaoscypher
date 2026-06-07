// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactNode } from 'react';
import {
  Box,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
} from '@mui/material';
import { ghostInputSx } from '../theme/ghostStyles';

/** A single filter dropdown rendered beside the search field. */
interface FilterDefinition {
  /** Label shown on the dropdown (e.g. "Category"). */
  label: string;
  /** Current selected value. */
  value: string;
  /** Available options. */
  options: { value: string; label: string }[];
  /** Called when the user picks a new value. */
  onChange: (value: string) => void;
  /** Minimum width for the dropdown (default 150). */
  minWidth?: number;
}

interface SearchFilterBarProps {
  /** Label shown on the search TextField (e.g. "Search tools"). */
  searchLabel: string;
  /** Current search value. */
  searchValue: string;
  /** Called when the search text changes. */
  onSearchChange: (value: string) => void;
  /** Optional filter dropdowns rendered after the search field. */
  filters?: FilterDefinition[];
  /** Optional extra controls (e.g. a "Create" button) placed at the end. */
  children?: ReactNode;
}

/**
 * Reusable search text field + optional category/filter dropdowns.
 *
 * Appears on CRUD pages (Tools, Triggers, Workflows, etc.) with identical
 * structure: a growing search input followed by zero or more filter Selects.
 */
export default function SearchFilterBar({
  searchLabel,
  searchValue,
  onSearchChange,
  filters,
  children,
}: SearchFilterBarProps) {
  return (
    <Box sx={{ mb: 3, display: 'flex', gap: 2 }}>
      <TextField
        label={searchLabel}
        variant="outlined"
        size="small"
        value={searchValue}
        onChange={(e) => onSearchChange(e.target.value)}
        sx={{ flexGrow: 1, ...ghostInputSx }}
      />
      {filters?.map((filter) => (
        <FormControl
          key={filter.label}
          size="small"
          sx={{ minWidth: filter.minWidth ?? 150, ...ghostInputSx }}
        >
          <InputLabel>{filter.label}</InputLabel>
          <Select
            value={filter.value}
            label={filter.label}
            onChange={(e) => filter.onChange(e.target.value)}
          >
            {filter.options.map((opt) => (
              <MenuItem key={opt.value} value={opt.value}>
                {opt.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      ))}
      {children}
    </Box>
  );
}
