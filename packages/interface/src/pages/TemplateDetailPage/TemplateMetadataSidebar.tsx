// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Alert, Box, Chip, Typography } from '@mui/material';
import type { Template } from '../../types';
import MetadataCard from '../../components/detail/MetadataCard';
import MetadataRow from '../../components/detail/MetadataRow';
import { ghostInfoAlertSx } from '../../theme/ghostStyles';
import { ChaosCypherNeutrals } from '../../theme/palette';

interface TemplateMetadataSidebarProps {
  template: Template;
}

export function TemplateMetadataSidebar({ template }: TemplateMetadataSidebarProps) {
  const typeChip = (
    <Chip
      label={template.template_type}
      size="small"
      variant="outlined"
      sx={{ borderColor: 'rgba(255, 255, 255, 0.15)', color: ChaosCypherNeutrals.textSecondary }}
    />
  );

  return (
    <MetadataCard collapsible summary={typeChip}>
      <MetadataRow label="ID">
        <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>
          {template.id}
        </Typography>
      </MetadataRow>
      <MetadataRow label="Type">
        <Box sx={{ mt: 0.5 }}>{typeChip}</Box>
      </MetadataRow>
      <MetadataRow label="Properties Count">
        <Typography variant="body2">{template.properties?.length ?? 0}</Typography>
      </MetadataRow>
      <MetadataRow label="Created">
        <Typography variant="body2">
          {new Date(template.created_at).toLocaleString()}
        </Typography>
      </MetadataRow>
      {template.updated_at && (
        <MetadataRow label="Updated">
          <Typography variant="body2">
            {new Date(template.updated_at).toLocaleString()}
          </Typography>
        </MetadataRow>
      )}
      {template.is_system && (
        <Box sx={{ py: 1.5 }}>
          <Alert severity="info" sx={{ ...ghostInfoAlertSx }}>
            This is a system template and cannot be modified or deleted.
          </Alert>
        </Box>
      )}
    </MetadataCard>
  );
}
