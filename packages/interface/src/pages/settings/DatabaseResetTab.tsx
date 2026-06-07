// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Divider,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import CleaningServicesIcon from '@mui/icons-material/CleaningServices';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import ChatBubbleOutlinedIcon from '@mui/icons-material/ChatBubbleOutlined';
import PlaylistRemoveIcon from '@mui/icons-material/PlaylistRemove';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { settingsApi } from '../../services/api/settings';
import ResetConfirmationDialog from '../../components/ResetConfirmationDialog';
import { accordionSummarySx as summarySx, accordionBtnSx as btnSx, accentAccordionSx } from '../../theme/settings';
import { ACCENT_COLORS } from '../../theme/accentStyles';
import { ghostSuccessAlertSx, ghostErrorAlertSx, ghostInfoAlertSx } from '../../theme/ghostStyles';
import { getApiErrorMessage } from '../../utils/errors';

interface ResetDialogState {
  open: boolean;
  type: string;
  title: string;
  description: string;
  requireConfirmText: boolean;
}

export default function DatabaseResetTab() {
  const [resetDialog, setResetDialog] = useState<ResetDialogState>({
    open: false,
    type: '',
    title: '',
    description: '',
    requireConfirmText: false,
  });
  const [resetting, setResetting] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);


  const handleResetClick = (
    e: React.MouseEvent,
    type: string,
    title: string,
    description: string,
    requireConfirmText: boolean = false
  ) => {
    e.stopPropagation();
    setResetDialog({ open: true, type, title, description, requireConfirmText });
    setSuccess(null);
    setError(null);
  };

  const handleConfirmReset = async () => {
    try {
      setResetting(true);
      setSuccess(null);
      setError(null);

      let result;
      switch (resetDialog.type) {
        case 'knowledge':
          result = await settingsApi.resetKnowledge();
          setSuccess(
            `Knowledge base reset. Removed: ` +
            `${result.sources_deleted || 0} sources, ` +
            `${result.chunks_deleted || 0} chunks, ` +
            `${result.triples_deleted || 0} graph triples.`
          );
          break;
        case 'workflows':
          result = await settingsApi.resetWorkflows();
          setSuccess(
            `Workflows reset. ${result.workflows_deleted || 0} workflows, ` +
            `${result.triggers_deleted || 0} triggers removed.`
          );
          break;
        case 'chats':
          result = await settingsApi.resetChats();
          setSuccess(`Chats reset. ${result.chats_deleted || 0} chats removed.`);
          break;
        case 'queue':
          result = await settingsApi.resetQueue();
          setSuccess(
            `Queue reset. ${result.tasks_deleted || 0} tasks, ` +
            `${result.arq_jobs_deleted || 0} jobs deleted.`
          );
          break;
        case 'all':
          await settingsApi.resetAll('CONFIRM');
          setSuccess('All data reset. Reloading...');
          setTimeout(() => window.location.reload(), 2000);
          break;
        case 'cleanup_orphans':
          result = await settingsApi.cleanupOrphans();
          setSuccess(
            `Cleanup complete. Removed ${result.nodes_removed || 0} orphaned nodes, ` +
            `${result.templates_removed || 0} orphaned templates.`
          );
          break;
      }
      setResetDialog({ ...resetDialog, open: false });
    } catch (err) {
      setError(getApiErrorMessage(err) || 'Operation failed. Please try again.');
      setResetDialog({ ...resetDialog, open: false });
    } finally {
      setResetting(false);
    }
  };


  return (
    <Box sx={{ p: 3 }}>
      {success && (
        <Alert severity="success" sx={{ mb: 2, ...ghostSuccessAlertSx }} onClose={() => setSuccess(null)}>
          {success}
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Typography variant="h6" gutterBottom>
        Maintenance
      </Typography>
      <Typography variant="body2" color="textSecondary" gutterBottom sx={{ mb: 2 }}>
        Clean up and maintain the knowledge graph.
      </Typography>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2
        }}>
        {/* Clean Up Orphans */}
        <Accordion sx={accentAccordionSx('domain')}>
          <AccordionSummary expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.domain }} />} sx={summarySx}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <CleaningServicesIcon sx={{ fontSize: 18, color: ACCENT_COLORS.domain }} />
                <Typography variant="subtitle2" sx={{
                  fontWeight: "medium"
                }}>
                  Clean Up Orphaned Items
                </Typography>
              </Box>
              <Button
                size="small"
                variant="outlined"
                color="warning"
                onClick={(e) => handleResetClick(
                  e,
                  'cleanup_orphans',
                  'Clean Up Orphaned Graph Items',
                  'This will remove all nodes, edges, and templates that have no associated source citations. This includes any manually added items. This action cannot be undone.'
                )}
                disabled={resetting}
                sx={btnSx}
              >
                Clean Up
              </Button>
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            <Alert severity="info" sx={ghostInfoAlertSx}>
              <Typography variant="body2">
                Removes nodes, edges, and templates with no source citations.
                This includes manually added items not created through imports.
                System templates are preserved.
              </Typography>
            </Alert>
          </AccordionDetails>
        </Accordion>

        <Divider sx={{ my: 1 }} />

        <Typography variant="h6" gutterBottom>
          Reset
        </Typography>
        <Typography variant="body2" color="textSecondary" gutterBottom sx={{ mb: 1 }}>
          Reset sections of the database. These operations cannot be undone.
        </Typography>

        {/* Reset Knowledge Base */}
        <Accordion sx={accentAccordionSx('error')}>
          <AccordionSummary expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.error }} />} sx={summarySx}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <DeleteSweepIcon sx={{ fontSize: 18, color: ACCENT_COLORS.error }} />
                <Typography variant="subtitle2" sx={{
                  fontWeight: "medium"
                }}>
                  Reset Knowledge Base
                </Typography>
              </Box>
              <Button
                size="small"
                variant="outlined"
                color="error"
                onClick={(e) => handleResetClick(
                  e,
                  'knowledge',
                  'Reset Knowledge Base',
                  'This will delete all imported knowledge content including sources, chunks, the knowledge graph, and search indices. Workflows, chats, and queue stats will be preserved.'
                )}
                disabled={resetting}
                sx={btnSx}
              >
                Reset
              </Button>
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            <Alert severity="info" sx={ghostInfoAlertSx}>
              <Typography variant="body2">
                Deletes all sources, chunks, graph data (nodes, edges, templates, lenses), and search indices.
                Preserves workflows, chats, and queue statistics.
              </Typography>
            </Alert>
          </AccordionDetails>
        </Accordion>

        {/* Reset Workflows */}
        <Accordion sx={accentAccordionSx('error')}>
          <AccordionSummary expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.error }} />} sx={summarySx}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <RestartAltIcon sx={{ fontSize: 18, color: ACCENT_COLORS.error }} />
                <Typography variant="subtitle2" sx={{
                  fontWeight: "medium"
                }}>
                  Reset Workflows
                </Typography>
              </Box>
              <Button
                size="small"
                variant="outlined"
                color="error"
                onClick={(e) => handleResetClick(
                  e,
                  'workflows',
                  'Reset Workflow System',
                  'This will delete all workflows, tools, triggers, and execution history. System defaults will be restored.'
                )}
                disabled={resetting}
                sx={btnSx}
              >
                Reset
              </Button>
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            <Alert severity="info" sx={ghostInfoAlertSx}>
              <Typography variant="body2">
                Deletes all workflows, tools, and triggers. System defaults will be restored.
              </Typography>
            </Alert>
          </AccordionDetails>
        </Accordion>

        {/* Reset Chats */}
        <Accordion sx={accentAccordionSx('error')}>
          <AccordionSummary expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.error }} />} sx={summarySx}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <ChatBubbleOutlinedIcon sx={{ fontSize: 18, color: ACCENT_COLORS.error }} />
                <Typography variant="subtitle2" sx={{
                  fontWeight: "medium"
                }}>
                  Reset Chats
                </Typography>
              </Box>
              <Button
                size="small"
                variant="outlined"
                color="error"
                onClick={(e) => handleResetClick(
                  e,
                  'chats',
                  'Reset Chats',
                  'This will delete all chats and messages. This action cannot be undone.'
                )}
                disabled={resetting}
                sx={btnSx}
              >
                Reset
              </Button>
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            <Alert severity="info" sx={ghostInfoAlertSx}>
              <Typography variant="body2">
                Deletes all chat conversations and messages permanently.
              </Typography>
            </Alert>
          </AccordionDetails>
        </Accordion>

        {/* Reset Queue */}
        <Accordion sx={accentAccordionSx('error')}>
          <AccordionSummary expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.error }} />} sx={summarySx}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <PlaylistRemoveIcon sx={{ fontSize: 18, color: ACCENT_COLORS.error }} />
                <Typography variant="subtitle2" sx={{
                  fontWeight: "medium"
                }}>
                  Reset Queue
                </Typography>
              </Box>
              <Button
                size="small"
                variant="outlined"
                color="error"
                onClick={(e) => handleResetClick(
                  e,
                  'queue',
                  'Reset Queue System',
                  'This will cancel all active jobs, clear task records, and reset queue statistics.'
                )}
                disabled={resetting}
                sx={btnSx}
              >
                Reset
              </Button>
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            <Alert severity="info" sx={ghostInfoAlertSx}>
              <Typography variant="body2">
                Cancels active jobs, clears completed task records, and resets token/cost statistics.
              </Typography>
            </Alert>
          </AccordionDetails>
        </Accordion>

        <Divider sx={{ my: 1 }} />

        {/* Danger Zone - Reset ALL */}
        <Accordion
          variant="outlined"
          sx={{
            '&:before': { display: 'none' },
            borderRadius: 1,
            border: '1px solid',
            borderColor: 'error.main',
            bgcolor: 'rgba(255, 0, 60, 0.08)',
            '&:hover': { borderColor: 'error.dark' },
            transition: 'all 0.2s ease-in-out',
          }}
        >
          <AccordionSummary
            expandIcon={<ExpandMoreIcon sx={{ color: 'error.main' }} />}
            sx={{
              color: 'error.main',
              minHeight: 56,
              borderRadius: '4px 4px 0 0',
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, mr: 2 }}>
              <WarningAmberIcon />
              <Typography variant="subtitle2" sx={{
                fontWeight: "bold"
              }}>
                Reset ALL Data
              </Typography>
              <Button
                size="small"
                variant="outlined"
                color="error"
                onClick={(e) => handleResetClick(
                  e,
                  'all',
                  'Reset ALL Data',
                  'This will delete the ENTIRE database and recreate it with defaults. ALL data will be permanently lost.',
                  true
                )}
                disabled={resetting}
                sx={btnSx}
              >
                Reset Everything
              </Button>
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            <Alert severity="error" sx={ghostErrorAlertSx}>
              <Typography variant="body2">
                This will delete the entire database — knowledge graph, workflows, chats, sources,
                queue stats — and recreate it with defaults. This action <strong>cannot be undone</strong>.
              </Typography>
            </Alert>
          </AccordionDetails>
        </Accordion>
      </Box>
      <ResetConfirmationDialog
        open={resetDialog.open}
        title={resetDialog.title}
        description={resetDialog.description}
        requireConfirmText={resetDialog.requireConfirmText}
        onConfirm={handleConfirmReset}
        onCancel={() => setResetDialog({ ...resetDialog, open: false })}
      />
    </Box>
  );
}
