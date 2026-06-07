// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Import & Export Section
 *
 * Accordion section within General Settings for importing and exporting
 * database contents as .ccx files, with export options and import warnings.
 */

import { RefObject, useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Button,
  Divider,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  FormHelperText,
  Link,
  CircularProgress,
  Checkbox,
  FormGroup,
  FormControlLabel,
  TextField,
  Paper,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import UploadIcon from '@mui/icons-material/Upload';
import ImportExportIcon from '@mui/icons-material/ImportExport';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import type { Settings } from '../../types';
import { accordionSummarySx, accordionBtnSx, accentAccordionSx } from '../../theme/settings';
import { accentPaperSx, ACCENT_COLORS } from '../../theme/accentStyles';
import {
  ghostButtonSx,
  ghostCancelBtnSx,
  ghostInfoAlertSx,
  ghostSuccessAlertSx,
  ghostErrorAlertSx,
} from '../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../theme/palette';

interface ExportOptions {
  includeTemplates: boolean;
  includeKnowledge: boolean;
  includeLenses: boolean;
  includeWorkflows: boolean;
  includeSources: boolean;
  includeEmbeddings: boolean;
}

interface ImportExportSectionProps {
  settings: Settings;
  setSettings: (settings: Settings) => void;
  importing: boolean;
  exporting: boolean;
  importSuccess: boolean;
  importError: string | null;
  setImportError: (error: string | null) => void;
  fileInputRef: RefObject<HTMLInputElement | null>;
  handleExport: () => Promise<void>;
  handleImport: (event: React.ChangeEvent<HTMLInputElement>) => Promise<void>;
  exportOptions: ExportOptions;
  setExportOptions: (options: ExportOptions) => void;
}

/** Accordion section for database import and export operations. */
export default function ImportExportSection({
  settings,
  setSettings,
  importing,
  exporting,
  importSuccess,
  importError,
  setImportError,
  fileInputRef,
  handleExport,
  handleImport,
  exportOptions,
  setExportOptions,
}: ImportExportSectionProps) {
  const [showExportOptions, setShowExportOptions] = useState(false);
  const [showImportWarning, setShowImportWarning] = useState(false);

  // Reset import warning when import completes or errors. Intentional
  // setState-in-effect: dismissing the warning is a side effect of the
  // import status flipping.
  useEffect(() => {
    if (importSuccess || importError) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShowImportWarning(false);
    }
  }, [importSuccess, importError]);

  return (
    <Accordion sx={accentAccordionSx('file')}>
      <AccordionSummary
        expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.file }} />}
        sx={accordionSummarySx}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flex: 1, mr: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <ImportExportIcon sx={{ fontSize: 18, color: ACCENT_COLORS.file }} />
            <Typography variant="subtitle2" sx={{
              fontWeight: "medium"
            }}>
              Import & Export
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', gap: 1, ml: 'auto' }}>
            <Button
              size="small"
              variant="outlined"
              startIcon={exporting ? <CircularProgress size={14} /> : <DownloadIcon />}
              onClick={(e) => {
                e.stopPropagation();
                if (!showExportOptions) {
                  setShowExportOptions(true);
                } else {
                  handleExport();
                }
              }}
              disabled={exporting || importing}
              sx={accordionBtnSx}
            >
              Export
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".ccx"
              style={{ display: 'none' }}
              onChange={handleImport}
              aria-label="Import backup file"
            />
            <Button
              size="small"
              variant="outlined"
              startIcon={importing ? <CircularProgress size={14} /> : <UploadIcon />}
              onClick={(e) => {
                e.stopPropagation();
                if (!showImportWarning) {
                  setShowImportWarning(true);
                } else {
                  fileInputRef.current?.click();
                }
              }}
              disabled={exporting || importing}
              color={showImportWarning ? 'error' : 'primary'}
              sx={accordionBtnSx}
            >
              Import
            </Button>
          </Box>
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        <Alert severity="info" sx={{ mb: 2, ...ghostInfoAlertSx }}>
          Export your database to a <code>.ccx</code> file or import from a previous export.
          Imports will merge with existing data.
        </Alert>

        {importSuccess && (
          <Alert severity="success" sx={{ mb: 2, ...ghostSuccessAlertSx }}>
            Import successful! Reloading page...
          </Alert>
        )}

        {importError && (
          <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }} onClose={() => setImportError(null)}>
            {importError}
          </Alert>
        )}

        {/* Export Options - Show when Export is clicked */}
        {showExportOptions && (
          <Paper variant="outlined" sx={{ p: 2, mb: 2, ...accentPaperSx('file') }}>
            <Typography variant="subtitle2" gutterBottom>
              Select what to include in the export
            </Typography>
            <FormGroup>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={exportOptions.includeTemplates}
                    onChange={(e) =>
                      setExportOptions({ ...exportOptions, includeTemplates: e.target.checked })
                    }
                  />
                }
                label="Templates - User-created node and edge templates"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={exportOptions.includeKnowledge}
                    onChange={(e) =>
                      setExportOptions({ ...exportOptions, includeKnowledge: e.target.checked })
                    }
                  />
                }
                label="Knowledge Graph - All nodes and relationships"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={exportOptions.includeLenses}
                    onChange={(e) =>
                      setExportOptions({ ...exportOptions, includeLenses: e.target.checked })
                    }
                  />
                }
                label="Lenses - Interpretation and transformation rules"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={exportOptions.includeWorkflows}
                    onChange={(e) =>
                      setExportOptions({ ...exportOptions, includeWorkflows: e.target.checked })
                    }
                  />
                }
                label="Workflows - Automated processes and triggers"
              />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={exportOptions.includeSources}
                    onChange={(e) =>
                      setExportOptions({ ...exportOptions, includeSources: e.target.checked })
                    }
                  />
                }
                label="Sources - Document sources with chunks and embeddings"
              />
              <Divider sx={{ my: 1 }} />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={exportOptions.includeEmbeddings}
                    onChange={(e) =>
                      setExportOptions({ ...exportOptions, includeEmbeddings: e.target.checked })
                    }
                    size="small"
                  />
                }
                label={
                  <Typography variant="body2">
                    Include embedding vectors
                    <Typography
                      component="span"
                      variant="caption"
                      sx={{
                        color: "text.secondary",
                        ml: 0.5
                      }}>
                      (for same-model migration)
                    </Typography>
                  </Typography>
                }
              />
            </FormGroup>

            <Box sx={{ display: 'flex', gap: 1, mt: 1 }}>
              <Button
                variant="outlined"
                size="small"
                startIcon={exporting ? <CircularProgress size={14} sx={{ color: 'primary.main' }} /> : <DownloadIcon />}
                onClick={handleExport}
                disabled={exporting || (!exportOptions.includeTemplates && !exportOptions.includeKnowledge && !exportOptions.includeLenses && !exportOptions.includeWorkflows && !exportOptions.includeSources)}
                sx={ghostButtonSx(ChaosCypherPalette.primary)}
              >
                Export Now
              </Button>
              <Button
                variant="outlined"
                size="small"
                onClick={() => setShowExportOptions(false)}
                sx={ghostCancelBtnSx}
              >
                Cancel
              </Button>
            </Box>
          </Paper>
        )}

        {/* Import Warning - Show when Import is clicked */}
        {showImportWarning && (
          <Paper variant="outlined" sx={{ p: 2, mb: 2, ...accentPaperSx('warning') }}>
            <Typography variant="subtitle2" gutterBottom sx={{
              color: "warning.main"
            }}>
              Warning: Destructive Operation
            </Typography>
            <Typography variant="body2" sx={{ mb: 1 }}>
              Importing will <strong>replace all existing data</strong> in the current database. This action cannot be undone.
            </Typography>
            <Box sx={{ display: 'flex', gap: 1 }}>
              <Button
                variant="outlined"
                size="small"
                startIcon={importing ? <CircularProgress size={14} sx={{ color: 'error.main' }} /> : <UploadIcon />}
                onClick={() => fileInputRef.current?.click()}
                disabled={importing}
                sx={ghostButtonSx(ChaosCypherPalette.error)}
              >
                Confirm Import
              </Button>
              <Button
                variant="outlined"
                size="small"
                onClick={() => setShowImportWarning(false)}
                sx={ghostCancelBtnSx}
              >
                Cancel
              </Button>
            </Box>
          </Paper>
        )}

        <Divider sx={{ my: 2 }} />

        {/* Export Defaults - inline within the accordion */}
        <Typography variant="subtitle2" gutterBottom sx={{
          fontWeight: "medium"
        }}>
          Export Defaults
        </Typography>
        <Typography
          variant="body2"
          sx={{
            color: "text.secondary",
            mb: 2
          }}>
          Default metadata for exported .ccx packages
        </Typography>

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <TextField
            label="Package Name"
            value={settings.export.export_package_name || ''}
            onChange={(e) => setSettings({ ...settings, export: { ...settings.export, export_package_name: e.target.value } })}
            fullWidth
            size="small"
            required
            error={!settings.export.export_package_name}
            helperText={
              !settings.export.export_package_name
                ? 'Required — used as the .ccx bundle identifier and filename'
                : 'e.g., organization/package-name'
            }
          />

          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField
              label="Author"
              value={settings.export.export_author || ''}
              onChange={(e) => setSettings({ ...settings, export: { ...settings.export, export_author: e.target.value } })}
              fullWidth
              size="small"
            />
            <TextField
              label="Version"
              value={settings.export.export_version}
              onChange={(e) => setSettings({ ...settings, export: { ...settings.export, export_version: e.target.value } })}
              fullWidth
              size="small"
              helperText="e.g., 1.0.0"
            />
          </Box>

          <FormControl fullWidth size="small">
            <InputLabel>License</InputLabel>
            <Select
              value={settings.export.export_license}
              label="License"
              onChange={(e) => setSettings({ ...settings, export: { ...settings.export, export_license: e.target.value } })}
            >
              <MenuItem value="CC-BY-SA-4.0">CC-BY-SA-4.0 (Attribution-ShareAlike)</MenuItem>
              <MenuItem value="CC-BY-4.0">CC-BY-4.0 (Attribution)</MenuItem>
            </Select>
            <FormHelperText>
              <Link href="https://creativecommons.org/licenses/" target="_blank" rel="noopener">
                Learn about Creative Commons licenses
              </Link>
            </FormHelperText>
          </FormControl>

          <TextField
            label="Description"
            value={settings.export.export_description || ''}
            onChange={(e) => setSettings({ ...settings, export: { ...settings.export, export_description: e.target.value } })}
            multiline
            rows={2}
            fullWidth
            size="small"
          />

          <TextField
            label="Tags (comma-separated)"
            value={settings.export.export_tags.join(', ')}
            onChange={(e) => setSettings({ ...settings, export: { ...settings.export, export_tags: e.target.value.split(',').map(t => t.trim()).filter(Boolean) } })}
            fullWidth
            size="small"
            helperText="e.g., research, medical, ontology"
          />
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}
