// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  Typography,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  TextField,
  Switch,
  Divider,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Button,
  IconButton,
  Chip,
  Tooltip,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import DnsIcon from '@mui/icons-material/Dns';
import ErrorIcon from '@mui/icons-material/Error';
import type { Settings, OllamaInstance } from '../../../types';
import { accentAccordionSx } from '../../../theme/settings';
import { ACCENT_COLORS } from '../../../theme/accentStyles';

interface InstanceManagerProps {
  /** Current application settings. */
  settings: Settings;
  /** Callback to update settings. */
  setSettings: (settings: Settings) => void;
  /** List of configured Ollama instances. */
  ollamaInstances: OllamaInstance[];
  /** Number of currently enabled instances. */
  enabledInstanceCount: number;
  /** New instance form state. */
  newInstance: { name: string; base_url: string };
  /** Update the new instance form state. */
  setNewInstance: (value: { name: string; base_url: string }) => void;
  /** Handler to add a new instance. */
  onAddInstance: () => void;
  /** Handler to remove an instance by ID. */
  onRemoveInstance: (instanceId: string) => void;
  /** Handler to toggle an instance's enabled state. */
  onToggleInstance: (instanceId: string) => void;
}

/**
 * Ollama instance CRUD management with load balancing configuration.
 *
 * Renders as a collapsible accordion containing the instance list,
 * load balancing strategy selector, and a form to add new instances.
 */
export default function InstanceManager({
  settings,
  setSettings,
  ollamaInstances,
  enabledInstanceCount,
  newInstance,
  setNewInstance,
  onAddInstance,
  onRemoveInstance,
  onToggleInstance,
}: InstanceManagerProps) {
  return (
    <Accordion sx={accentAccordionSx('domain')}>
      <AccordionSummary
        expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.domain }} />}
        sx={{ '&:hover': { bgcolor: 'transparent' }, minHeight: 56 }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <DnsIcon sx={{ fontSize: 18, color: ACCENT_COLORS.domain }} />
          <Typography variant="subtitle2" sx={{
            fontWeight: "medium"
          }}>
            Multiple Instances & Load Balancing
          </Typography>
          <Chip
            size="small"
            label={ollamaInstances.length === 0 ? 'Disabled' : `${enabledInstanceCount} active`}
            color={ollamaInstances.length === 0 ? 'default' : 'primary'}
          />
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {ollamaInstances.length === 0 ? (
            <Alert severity="info">
              Distribute LLM requests across multiple Ollama instances for parallel processing.
            </Alert>
          ) : (
            <>
              <Alert severity="success">
                Load balancing enabled. Requests distributed across {enabledInstanceCount} instance{enabledInstanceCount !== 1 ? 's' : ''}.
              </Alert>

              {/* Load Balancing Strategy */}
              <FormControl fullWidth size="small">
                <InputLabel>Load Balancing Strategy</InputLabel>
                <Select
                  value={settings.llm.ollama_load_balancing || 'round_robin'}
                  label="Load Balancing Strategy"
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      llm: { ...settings.llm, ollama_load_balancing: e.target.value as 'round_robin' | 'least_loaded' | 'random' },
                    })
                  }
                >
                  <MenuItem value="round_robin">Round Robin</MenuItem>
                  <MenuItem value="least_loaded">Least Loaded</MenuItem>
                  <MenuItem value="random">Random</MenuItem>
                </Select>
              </FormControl>
            </>
          )}

          {/* Existing Instances */}
          {ollamaInstances.map((instance) => (
            <Box
              key={instance.id}
              sx={{
                p: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                opacity: instance.enabled ? 1 : 0.6,
                borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <Tooltip title={instance.healthy ? 'Healthy' : instance.last_error || 'Unhealthy'}>
                  {instance.healthy ? (
                    <CheckCircleIcon color="success" />
                  ) : (
                    <ErrorIcon color="error" />
                  )}
                </Tooltip>
                <Box>
                  <Typography sx={{
                    fontWeight: "medium"
                  }}>{instance.name}</Typography>
                  <Typography variant="body2" sx={{
                    color: "text.secondary"
                  }}>
                    {instance.base_url}
                  </Typography>
                </Box>
              </Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Switch
                  checked={instance.enabled}
                  onChange={() => onToggleInstance(instance.id)}
                  size="small"
                />
                <IconButton
                  aria-label="Delete instance"
                  color="error"
                  size="small"
                  onClick={() => onRemoveInstance(instance.id)}
                >
                  <DeleteIcon />
                </IconButton>
              </Box>
            </Box>
          ))}

          {/* Add New Instance Form */}
          <Divider />
          <Typography variant="body2" sx={{
            color: "text.secondary"
          }}>
            Add a new Ollama instance:
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
            <TextField
              label="Name"
              value={newInstance.name}
              onChange={(e) => setNewInstance({ ...newInstance, name: e.target.value })}
              size="small"
              placeholder="e.g., GPU Server 1"
              sx={{ flex: 1 }}
            />
            <TextField
              label="Base URL"
              value={newInstance.base_url}
              onChange={(e) => setNewInstance({ ...newInstance, base_url: e.target.value })}
              size="small"
              placeholder="e.g., http://192.168.1.10:11434"
              sx={{ flex: 2 }}
            />
            <Button
              variant="outlined"
              startIcon={<AddIcon />}
              onClick={onAddInstance}
              disabled={!newInstance.name.trim() || !newInstance.base_url.trim()}
            >
              Add
            </Button>
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}
