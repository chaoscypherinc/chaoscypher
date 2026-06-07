// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * PortList: Section displaying a list of input or output ports within a step node.
 *
 * Renders a section header (with icon, label, and count chip) followed by
 * individual PortRow components for each field in the port list.
 */

import React, { memo } from 'react';
import { Box, Typography, Chip } from '@mui/material';
import { styled } from '@mui/material/styles';
import InputIcon from '@mui/icons-material/Input';
import OutputIcon from '@mui/icons-material/Output';
import type { FieldSchema } from '../../types';
import { PortRow } from './PortRow';

const PortSection = styled(Box)(({ theme }) => ({
  padding: theme.spacing(0.5, 0),
}));

const PortSectionHeader = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(0.5),
  padding: theme.spacing(0.25, 1),
  backgroundColor: theme.palette.action.hover,
}));

interface PortListProps {
  /** The fields to display in this port section. */
  fields: FieldSchema[];
  /** Whether this is an input or output port section. */
  direction: 'input' | 'output';
  /** Set of connected field names (used for input ports). */
  connectedFields?: Set<string>;
}

/**
 * Renders a port section with a header and a list of port rows.
 */
const PortListComponent: React.FC<PortListProps> = ({ fields, direction, connectedFields }) => {
  const isInput = direction === 'input';
  const Icon = isInput ? InputIcon : OutputIcon;
  const label = isInput ? 'INPUTS' : 'OUTPUTS';

  return (
    <PortSection>
      <PortSectionHeader>
        <Icon sx={{ fontSize: 12, color: 'text.secondary' }} />
        <Typography
          variant="caption"
          sx={{
            fontWeight: 600,
            color: "text.secondary",
            fontSize: '0.6rem'
          }}>
          {label}
        </Typography>
        <Chip
          label={fields.length}
          size="small"
          sx={{
            height: 12,
            fontSize: '0.5rem',
            ml: 'auto',
          }}
        />
      </PortSectionHeader>
      <Box>
        {fields.map((field) => (
          <PortRow
            key={field.name}
            field={field}
            direction={direction}
            connected={connectedFields?.has(field.name)}
          />
        ))}
      </Box>
    </PortSection>
  );
};

export const PortList = memo(PortListComponent);
