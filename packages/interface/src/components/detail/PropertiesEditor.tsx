// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Button, TextField, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import PropertyValue from '../PropertyValue';
import { ghostButtonSx, ghostInputSx } from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';
import { usePropertiesEditor } from './usePropertiesEditor';

interface PropertiesEditorProps {
  /** Current properties object (may be undefined). */
  properties: Record<string, unknown> | undefined;
  /** When false, renders a read-only view via <PropertyValue>. */
  editing: boolean;
  /** Called with the next properties object when the user edits. */
  onChange: (next: Record<string, unknown>) => void;
  /**
   * Keys hidden from the rendered list (both view and edit modes). The
   * underlying object is never mutated for these keys, so they round-trip
   * untouched on save — used to keep system/provenance fields out of the
   * editable list while preserving them on the entity/relationship.
   */
  excludeKeys?: ReadonlySet<string>;
}

/**
 * Shared edit/view widget for a `Record<string, unknown>` properties bag.
 * Used by NodeDetailPage and EdgeDetailPage.
 */
export default function PropertiesEditor({
  properties,
  editing,
  onChange,
  excludeKeys,
}: PropertiesEditorProps) {
  const { newPropertyKey, setNewPropertyKey, handleChange, handleAdd, handleRemove } =
    usePropertiesEditor(properties, onChange);

  const visibleEntries = Object.entries(properties || {}).filter(
    ([key]) => !excludeKeys?.has(key),
  );

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        Properties
      </Typography>

      {visibleEntries.map(([key, value]) => (
        <Box key={key} sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
          {editing ? (
            <>
              <TextField
                label={key}
                value={typeof value === 'object' ? JSON.stringify(value) : value}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(e.target.value);
                    handleChange(key, parsed);
                  } catch {
                    handleChange(key, e.target.value);
                  }
                }}
                fullWidth
                multiline={
                  typeof value === 'object' ||
                  (typeof value === 'string' && value.length > 50)
                }
                rows={typeof value === 'object' ? 3 : 1}
                sx={ghostInputSx}
              />
              <Button
                variant="outlined"
                onClick={() => handleRemove(key)}
                sx={{ ...ghostButtonSx(ChaosCypherPalette.error), mt: 1 }}
              >
                Remove
              </Button>
            </>
          ) : (
            <Box
              sx={{
                py: 1.5,
                width: '100%',
                borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
              }}
            >
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                {key}
              </Typography>
              <Box sx={{ mt: 0.5, color: 'common.white' }}>
                <PropertyValue value={value} propertyKey={key} />
              </Box>
            </Box>
          )}
        </Box>
      ))}

      {editing && (
        <Box sx={{ display: 'flex', gap: 1, mt: 2 }}>
          <TextField
            label="New Property Name"
            value={newPropertyKey}
            onChange={(e) => setNewPropertyKey(e.target.value)}
            size="small"
            onKeyPress={(e) => {
              if (e.key === 'Enter') {
                handleAdd();
              }
            }}
            sx={ghostInputSx}
          />
          <Button
            variant="outlined"
            startIcon={<AddIcon />}
            onClick={handleAdd}
            sx={ghostButtonSx(ChaosCypherPalette.primary)}
          >
            Add Property
          </Button>
        </Box>
      )}
    </Box>
  );
}
