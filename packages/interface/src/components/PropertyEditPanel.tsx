// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  Button,
  Tab,
  Tabs,
  TextField,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import type { UsePropertyEditorReturn } from '../hooks/usePropertyEditor';

type PropertyEditPanelProps = Pick<
  UsePropertyEditorReturn,
  | 'properties'
  | 'newPropertyKey'
  | 'setNewPropertyKey'
  | 'activeTab'
  | 'setActiveTab'
  | 'handlePropertyChange'
  | 'handleAddProperty'
  | 'handleRemoveProperty'
  | 'handleJsonChange'
>;

/**
 * Tabbed property editor panel with a Properties tab and a Raw JSON tab.
 *
 * Renders the identical UI used in the NodesPage and EdgesPage create/edit
 * dialogs. Accepts state and handlers from the `usePropertyEditor` hook.
 */
export default function PropertyEditPanel({
  properties,
  newPropertyKey,
  setNewPropertyKey,
  activeTab,
  setActiveTab,
  handlePropertyChange,
  handleAddProperty,
  handleRemoveProperty,
  handleJsonChange,
}: PropertyEditPanelProps) {
  return (
    <>
      <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}>
        <Tabs value={activeTab} onChange={(_, newValue) => setActiveTab(newValue)}>
          <Tab label="Properties" />
          <Tab label="Raw JSON" />
        </Tabs>
      </Box>

      {/* Properties Tab */}
      {activeTab === 0 && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {Object.entries(properties || {}).map(([key, value]) => (
            <Box key={key} sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
              <TextField
                label={key}
                value={typeof value === 'object' ? JSON.stringify(value) : value}
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(e.target.value);
                    handlePropertyChange(key, parsed);
                  } catch {
                    handlePropertyChange(key, e.target.value);
                  }
                }}
                fullWidth
                multiline={typeof value === 'object' || (typeof value === 'string' && value.length > 50)}
                rows={typeof value === 'object' ? 3 : 1}
              />
              <Button
                color="error"
                onClick={() => handleRemoveProperty(key)}
                sx={{ mt: 1 }}
              >
                Remove
              </Button>
            </Box>
          ))}

          <Box sx={{ display: 'flex', gap: 1, mt: 2 }}>
            <TextField
              label="New Property Name"
              value={newPropertyKey}
              onChange={(e) => setNewPropertyKey(e.target.value)}
              size="small"
              onKeyPress={(e) => {
                if (e.key === 'Enter') {
                  handleAddProperty();
                }
              }}
            />
            <Button
              variant="outlined"
              startIcon={<AddIcon />}
              onClick={handleAddProperty}
            >
              Add Property
            </Button>
          </Box>
        </Box>
      )}

      {/* Raw JSON Tab */}
      {activeTab === 1 && (
        <TextField
          multiline
          rows={8}
          value={JSON.stringify(properties || {}, null, 2)}
          onChange={(e) => handleJsonChange(e.target.value)}
          fullWidth
          placeholder='{"key": "value"}'
          sx={{ fontFamily: 'monospace' }}
        />
      )}
    </>
  );
}
