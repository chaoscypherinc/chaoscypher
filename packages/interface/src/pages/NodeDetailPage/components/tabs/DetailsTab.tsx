// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, TextField } from '@mui/material';
import type { Node } from '../../../../types';
import PropertiesEditor from '../../../../components/detail/PropertiesEditor';
import { ghostInputSx } from '../../../../theme/ghostStyles';
import { SYSTEM_PROPERTY_KEYS } from '../../../../utils/propertyKeys';

interface DetailsTabProps {
  entity: Node;
  editing: boolean;
  formData: Partial<Node>;
  onFormDataChange: (data: Partial<Node>) => void;
}

/**
 * "Details" tab for NodeDetailPage: label input + properties editor.
 */
export default function DetailsTab({
  entity,
  editing,
  formData,
  onFormDataChange,
}: DetailsTabProps) {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <TextField
        label="Label"
        value={editing ? formData.label || '' : entity.label}
        onChange={(e) => onFormDataChange({ ...formData, label: e.target.value })}
        fullWidth
        disabled={!editing}
        sx={ghostInputSx}
      />

      <Box sx={{ borderBottom: '1px solid rgba(255, 255, 255, 0.06)', my: 1 }} />

      <PropertiesEditor
        properties={editing ? formData.properties : entity.properties}
        editing={editing}
        onChange={(properties) => onFormDataChange({ ...formData, properties })}
        excludeKeys={SYSTEM_PROPERTY_KEYS}
      />
    </Box>
  );
}
