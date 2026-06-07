// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import React from 'react';
import {
  Box,
  Chip,
  IconButton,
  Button,
  Typography,
  Tooltip,
  Switch,
} from '@mui/material';
import EditIcon from '@mui/icons-material/Edit';
import StatsIcon from '@mui/icons-material/Assessment';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import BoltIcon from '@mui/icons-material/BoltOutlined';
import { getCardStyle, CardColors } from '../../theme/cardStyles';
import { ghostButtonSx, ghostSwitchMintSx } from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';

const CYAN = ChaosCypherPalette.primary;

function getTriggerChipColor(allEnabled: boolean, noneEnabled: boolean): 'success' | 'default' | 'warning' {
  if (allEnabled) return 'success';
  if (noneEnabled) return 'default';
  return 'warning';
}

import type { Trigger } from '../../services/api/triggers';

interface TriggerWorkflowGroupProps {
  workflowId: string;
  workflowName: string;
  triggers: Trigger[];
  onToggleEnabled: (trigger: Trigger) => void;
  onOpenStats: (trigger: Trigger) => void;
  onEditInWorkflow: (workflowId: string) => void;
}

export const TriggerWorkflowGroup: React.FC<TriggerWorkflowGroupProps> = ({
  workflowId,
  workflowName,
  triggers,
  onToggleEnabled,
  onOpenStats,
  onEditInWorkflow,
}) => {
  const enabledCount = triggers.filter((t) => t.enabled).length;
  const totalCount = triggers.length;
  const allEnabled = enabledCount === totalCount;
  const noneEnabled = enabledCount === 0;

  return (
    <Box sx={{ mb: 4 }}>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          mb: 2,
          pb: 1,
          borderBottom: 1,
          borderColor: 'rgba(255, 255, 255, 0.06)',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography
            variant="subtitle1"
            sx={{
              fontWeight: 600,
              color: 'text.primary'
            }}>
            {workflowName}
          </Typography>
          <Chip
            label={
              allEnabled ? `${totalCount} active`
                : noneEnabled ? `${totalCount} paused`
                  : `${enabledCount}/${totalCount} active`
            }
            size="small"
            color={getTriggerChipColor(allEnabled, noneEnabled)}
            sx={{ height: 20, fontSize: '0.7rem' }}
          />
        </Box>
        <Button
          size="small"
          variant="outlined"
          startIcon={<OpenInNewIcon />}
          onClick={() => onEditInWorkflow(workflowId)}
          sx={ghostButtonSx(CYAN)}
        >
          Edit in Workflow
        </Button>
      </Box>

      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
        {triggers.map((trigger) => (
          <Box key={trigger.id} sx={{ flex: '1 1 calc(33.333% - 11px)', minWidth: 280 }}>
            <Box
              sx={{
                ...getCardStyle(CardColors.warning, false),
                p: 2.5,
                display: 'flex',
                flexDirection: 'column',
                height: '100%',
                minHeight: 150,
                opacity: trigger.enabled ? 1 : 0.7,
              }}
            >
              <Box sx={{ flexGrow: 1, mb: 2 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1, gap: 2 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
                    <BoltIcon fontSize="small" color="warning" sx={{ flexShrink: 0 }} />
                    <Typography
                      variant="subtitle1"
                      sx={{
                        fontWeight: 600,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap'
                      }}>
                      {trigger.name}
                    </Typography>
                  </Box>
                  <Tooltip title={trigger.enabled ? 'Click to disable' : 'Click to enable'}>
                    <Switch
                      size="small"
                      checked={trigger.enabled}
                      onChange={() => onToggleEnabled(trigger)}
                      sx={{
                        flexShrink: 0,
                        ...ghostSwitchMintSx,
                      }}
                    />
                  </Tooltip>
                </Box>
                <Typography variant="body2" sx={{
                  color: "text.secondary"
                }}>
                  Event: {trigger.event_source}
                </Typography>
                {trigger.priority > 0 && (
                  <Chip label={`Priority: ${trigger.priority}`} size="small" sx={{ mt: 1, height: 18, fontSize: '0.65rem' }} />
                )}
              </Box>
              <Box sx={{ display: 'flex', gap: 0.5, pt: 1, borderTop: 1, borderColor: 'rgba(255, 255, 255, 0.06)', justifyContent: 'flex-end' }}>
                <Tooltip title="View Statistics">
                  <IconButton aria-label="View Statistics" size="small" onClick={() => onOpenStats(trigger)} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
                    <StatsIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Edit in Workflow Builder">
                  <IconButton aria-label="Edit in Workflow Builder" size="small" onClick={() => onEditInWorkflow(trigger.workflow_id)} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
                    <EditIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Box>
            </Box>
          </Box>
        ))}
      </Box>
    </Box>
  );
};
