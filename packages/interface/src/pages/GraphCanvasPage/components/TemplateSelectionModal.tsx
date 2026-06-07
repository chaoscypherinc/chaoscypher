// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TemplateSelectionModal: Modern template selection interface
 * Features:
 * - Searchable template grid
 * - Template cards with icons and descriptions
 * - Recently used templates section
 * - Responsive grid layout
 */

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  TextField,
  Card,
  CardActionArea,
  CardContent,
  Typography,
  Chip,
  InputAdornment,
  IconButton,
  useTheme,
  alpha,
  Divider,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/SearchOutlined';
import CloseIcon from '@mui/icons-material/CloseOutlined';
import DescriptionIcon from '@mui/icons-material/DescriptionOutlined';
import PropertyIcon from '@mui/icons-material/StorageOutlined';
import HistoryIcon from '@mui/icons-material/HistoryOutlined';
import { Template } from '../../../types';
import { useTemplates } from '../../../services/api/useTemplates';
import TemplateIcon from '../../../components/TemplateIcon';
import { logger } from '../../../utils/logger';

interface TemplateSelectionModalProps {
  open: boolean;
  onClose: () => void;
  onSelect: (templateId: string) => void;
  templateType?: 'node' | 'edge';
}

const RECENT_TEMPLATES_KEY = 'recent_templates';
const MAX_RECENT = 5;

export const TemplateSelectionModal: React.FC<TemplateSelectionModalProps> = ({
  open,
  onClose,
  onSelect,
  templateType = 'node',
}) => {
  const theme = useTheme();
  const [searchQuery, setSearchQuery] = useState('');
  const [recentTemplateIds, setRecentTemplateIds] = useState<string[]>([]);

  const { data: allTemplates, isError, error: queryError } = useTemplates(templateType, {
    enabled: open,
  });

  // Surface load failures through the logger to match the legacy behaviour.
  useEffect(() => {
    if (isError) {
      logger.error('Error loading templates:', queryError);
    }
  }, [isError, queryError]);

  // Filter templates by type and exclude system (lens/workflow) node templates.
  const templates = useMemo<Template[]>(() => {
    return (allTemplates ?? []).filter(t => {
      if (t.template_type !== templateType) return false;

      // For node templates, exclude lens and workflow types
      if (templateType === 'node') {
        const templateId = t.id.toLowerCase();
        if (templateId.includes('lens') || templateId.includes('workflow')) {
          return false;
        }
      }

      return true;
    });
  }, [allTemplates, templateType]);

  const loadRecentTemplates = useCallback(() => {
    try {
      const stored = localStorage.getItem(RECENT_TEMPLATES_KEY);
      if (stored) {
        setRecentTemplateIds(JSON.parse(stored));
      }
    } catch (err) {
      logger.error('Error loading recent templates:', err);
    }
  }, []);

  // Load the recently-used ids from localStorage whenever the modal opens.
  useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      loadRecentTemplates();
    }
  }, [open, templateType, loadRecentTemplates]);

  const saveRecentTemplate = (templateId: string) => {
    try {
      const updated = [
        templateId,
        ...recentTemplateIds.filter(id => id !== templateId)
      ].slice(0, MAX_RECENT);

      localStorage.setItem(RECENT_TEMPLATES_KEY, JSON.stringify(updated));
      setRecentTemplateIds(updated);
    } catch (err) {
      logger.error('Error saving recent template:', err);
    }
  };

  const handleSelect = (templateId: string) => {
    saveRecentTemplate(templateId);
    onSelect(templateId);
    handleClose();
  };

  const handleClose = () => {
    setSearchQuery('');
    onClose();
  };

  // Filter templates by search query
  const filteredTemplates = useMemo(() => {
    if (!searchQuery.trim()) return templates;

    const query = searchQuery.toLowerCase();
    return templates.filter(t =>
      t.name.toLowerCase().includes(query) ||
      t.description?.toLowerCase().includes(query)
    );
  }, [templates, searchQuery]);

  // Get recent templates that still exist
  const recentTemplates = useMemo(() => {
    return recentTemplateIds
      .map(id => templates.find(t => t.id === id))
      .filter(Boolean) as Template[];
  }, [recentTemplateIds, templates]);

  // Templates to show (excluding recent ones from main list)
  const mainTemplates = useMemo(() => {
    const recentIds = new Set(recentTemplateIds);
    return filteredTemplates.filter(t => !recentIds.has(t.id));
  }, [filteredTemplates, recentTemplateIds]);

  const TemplateCard = ({ template }: { template: Template }) => (
    <Card
      sx={{
        height: '100%',
        transition: 'all 0.2s ease-in-out',
        border: `1px solid ${theme.palette.divider}`,
        '&:hover': {
          transform: 'translateY(-4px)',
          boxShadow: 4,
          borderColor: theme.palette.primary.main,
          bgcolor: alpha(theme.palette.primary.main, 0.05),
        },
      }}
    >
      <CardActionArea
        onClick={() => handleSelect(template.id)}
        sx={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'stretch' }}
      >
        <CardContent sx={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
          {/* Icon and Type */}
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <TemplateIcon
              template={template}
              variant={template.template_type === 'edge' ? 'edge' : 'node'}
              size={20}
              containerSize={36}
              filled
            />
            {template.is_system && (
              <Chip label="System" size="small" color="default" sx={{ height: 20 }} />
            )}
          </Box>

          {/* Name */}
          <Typography variant="h6" component="div" sx={{ fontSize: '1rem', fontWeight: 600 }}>
            {template.name}
          </Typography>

          {/* Description */}
          {template.description && (
            <Typography
              variant="body2"
              sx={{
                color: "text.secondary",
                fontSize: '0.875rem',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                minHeight: '2.5em'
              }}>
              {template.description}
            </Typography>
          )}

          {/* Metadata */}
          <Box sx={{ display: 'flex', gap: 1, mt: 'auto' }}>
            <Chip
              icon={<PropertyIcon />}
              label={`${template.properties.length} props`}
              size="small"
              variant="outlined"
              sx={{ fontSize: '0.75rem' }}
            />
          </Box>
        </CardContent>
      </CardActionArea>
    </Card>
  );

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="md"
      fullWidth
      slotProps={{
        paper: {
          sx: {
            height: '80vh',
            maxHeight: 800,
          }
        }
      }}
    >
      <DialogTitle>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography variant="h6">
            Select {templateType === 'node' ? 'Item' : 'Link'} Template
          </Typography>
          <IconButton aria-label="Close" onClick={handleClose} size="small">
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3, height: '100%' }}>
          {/* Search Bar */}
          <TextField
            fullWidth
            placeholder="Search templates..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            autoFocus
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon />
                  </InputAdornment>
                ),
              }
            }}
          />

          {/* Templates Grid */}
          <Box sx={{ flex: 1, overflowY: 'auto' }}>
            {/* Recently Used Templates */}
            {recentTemplates.length > 0 && !searchQuery && (
              <Box sx={{ mb: 3 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                  <HistoryIcon fontSize="small" color="action" />
                  <Typography
                    variant="subtitle2"
                    sx={{
                      color: "text.secondary",
                      fontWeight: 600
                    }}>
                    Recently Used
                  </Typography>
                </Box>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
                  {recentTemplates.map(template => (
                    <Box key={template.id} sx={{ flex: '1 1 calc(33.333% - 11px)', minWidth: 250 }}>
                      <TemplateCard template={template} />
                    </Box>
                  ))}
                </Box>
                <Divider sx={{ mt: 3, mb: 2 }} />
              </Box>
            )}

            {/* All Templates */}
            {mainTemplates.length > 0 ? (
              <>
                {!searchQuery && recentTemplates.length > 0 && (
                  <Typography
                    variant="subtitle2"
                    sx={{
                      color: "text.secondary",
                      fontWeight: 600,
                      mb: 2
                    }}>
                    All Templates
                  </Typography>
                )}
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
                  {mainTemplates.map(template => (
                    <Box key={template.id} sx={{ flex: '1 1 calc(33.333% - 11px)', minWidth: 250 }}>
                      <TemplateCard template={template} />
                    </Box>
                  ))}
                </Box>
              </>
            ) : (
              <Box
                sx={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  height: '100%',
                  gap: 2,
                  color: 'text.secondary',
                }}
              >
                <DescriptionIcon sx={{ fontSize: 64, opacity: 0.3 }} />
                <Typography variant="h6" sx={{
                  color: "text.secondary"
                }}>
                  {searchQuery ? 'No templates found' : 'No templates available'}
                </Typography>
                {searchQuery && (
                  <Typography variant="body2" sx={{
                    color: "text.disabled"
                  }}>
                    Try adjusting your search terms
                  </Typography>
                )}
              </Box>
            )}
          </Box>
        </Box>
      </DialogContent>
    </Dialog>
  );
};
