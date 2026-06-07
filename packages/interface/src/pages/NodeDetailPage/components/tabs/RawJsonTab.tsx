// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, TextField, Typography } from '@mui/material';
import type { Node } from '../../../../types';
import { ghostInputSx } from '../../../../theme/ghostStyles';

interface RawJsonTabProps {
  entity: Node;
  editing: boolean;
  formData: Partial<Node>;
  onFormDataChange: (data: Partial<Node>) => void;
}

/**
 * "Raw JSON" tab for NodeDetailPage: JSON editor for the properties bag.
 */
export default function RawJsonTab({
  entity,
  editing,
  formData,
  onFormDataChange,
}: RawJsonTabProps) {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        Properties (JSON)
      </Typography>
      <TextField
        multiline
        rows={12}
        value={
          editing
            ? JSON.stringify(formData.properties || {}, null, 2)
            : JSON.stringify(entity.properties || {}, null, 2)
        }
        onChange={(e) => {
          try {
            const properties = JSON.parse(e.target.value);
            onFormDataChange({ ...formData, properties });
          } catch (_error) {
            // Invalid JSON, don't update
          }
        }}
        fullWidth
        disabled={!editing}
        placeholder="{}"
        sx={{ fontFamily: 'monospace', ...ghostInputSx }}
      />
    </Box>
  );
}
