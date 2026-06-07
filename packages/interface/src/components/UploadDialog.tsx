// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module UploadDialog
 * Dialog shell for uploading documents to the knowledge graph. Orchestrates
 * FileDropZone, FileList, and ExtractionOptions sub-components.
 */

import { useMemo, useState, useCallback, useRef } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  CircularProgress,
} from '@mui/material';
import { ChaosCypherPalette } from '../theme/palette';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../theme/ghostStyles';
import type { ExtractionDomain } from '../services/api/sources';
import { FileDropZone } from './upload/FileDropZone';
import { FileList } from './upload/FileList';
import { DomainSelect } from './upload/DomainSelect';
import { ExtractionOptions } from './upload/ExtractionOptions';

const NORMALIZATION_FILE_TYPES = new Set(['pdf', 'docx', 'doc', 'html', 'htm', 'odt', 'rtf', 'epub']);

const ACCEPTED_EXTENSIONS = '.txt,.md,.log,.pdf,.csv,.json,.jsonl,.html,.htm,.docx,.doc,.odt,.rtf,.epub,.ttl,.rdf,.nt,.zip,.tar.gz,.tgz,.jpg,.jpeg,.png,.gif,.webp,.tiff,.tif,.bmp,.mp3,.wav,.m4a,.flac,.ogg,.wma,.aac,.mp4,.mkv,.avi,.mov,.webm,.wmv,.flv';

// ── Component ─────────────────────────────────────────────────────────────

interface UploadDialogProps {
  open: boolean;
  onClose: () => void;
  selectedFiles: File[];
  analysisDepth: 'quick' | 'full';
  enableNormalization: boolean;
  selectedDomain: string;
  availableDomains: ExtractionDomain[];
  onFilesSelected: (files: File[]) => void;
  onAnalysisDepthChange: (value: 'quick' | 'full') => void;
  onNormalizationChange: (value: boolean) => void;
  onDomainChange: (value: string) => void;
  onConfirm: () => void;
  uploading?: boolean;
  onCancelUpload?: () => void;
  onClearSelection: () => void;
  onRemoveFile: (index: number) => void;
  onUrlImport: (url: string) => Promise<void>;
  importingUrl: boolean;
  extractEntities: boolean;
  onExtractEntitiesChange: (value: boolean) => void;
  enableVision: boolean;
  onEnableVisionChange: (value: boolean) => void;
  filteringMode: string;
  onFilteringModeChange: (value: string) => void;
  contentFiltering: boolean;
  onContentFilteringChange: (value: boolean) => void;
  contextWindow: number;
  groupSize: number;
  inputPerChunk: number;
  outputPerChunk: number;
  skipDuplicates: boolean;
  onSkipDuplicatesChange: (value: boolean) => void;
}

export function UploadDialog({
  open,
  onClose,
  selectedFiles,
  analysisDepth,
  enableNormalization,
  selectedDomain,
  availableDomains,
  onFilesSelected,
  onAnalysisDepthChange,
  onNormalizationChange,
  onDomainChange,
  onConfirm,
  uploading,
  onCancelUpload,
  onClearSelection,
  onRemoveFile,
  onUrlImport,
  importingUrl,
  extractEntities,
  onExtractEntitiesChange,
  enableVision,
  onEnableVisionChange,
  filteringMode,
  onFilteringModeChange,
  contentFiltering,
  onContentFilteringChange,
  contextWindow,
  groupSize,
  inputPerChunk,
  outputPerChunk,
  skipDuplicates,
  onSkipDuplicatesChange,
}: UploadDialogProps) {
  const [urlInput, setUrlInput] = useState('');
  const [urlError, setUrlError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0) {
        onFilesSelected(Array.from(e.target.files));
        e.target.value = '';
      }
    },
    [onFilesSelected]
  );

  const handleBrowseClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleClose = () => {
    onClearSelection();
    setUrlInput('');
    setUrlError('');
    onClose();
  };

  const showNormalizationOption = useMemo(() => {
    if (urlInput.trim().length > 0) return true;
    if (selectedFiles.length === 0) return false;
    return selectedFiles.some((file) => {
      const ext = file.name.split('.').pop()?.toLowerCase() || '';
      return NORMALIZATION_FILE_TYPES.has(ext);
    });
  }, [selectedFiles, urlInput]);

  const hasUrl = urlInput.trim().length > 0;
  const hasFiles = selectedFiles.length > 0;
  const showOptions = hasUrl || hasFiles;

  const handleUrlSubmit = async () => {
    const trimmed = urlInput.trim();
    if (!trimmed) return;

    if (!trimmed.startsWith('http://') && !trimmed.startsWith('https://')) {
      setUrlError('URL must start with http:// or https://');
      return;
    }

    setUrlError('');
    await onUrlImport(trimmed);
    if (!importingUrl) {
      setUrlInput('');
    }
  };

  return (
    <>
    {/* Hidden file input — outside Dialog to avoid focus trap interference */}
    <input
      ref={fileInputRef}
      type="file"
      multiple
      accept={ACCEPTED_EXTENSIONS}
      onChange={handleInputChange}
      style={{ display: 'none' }}
      aria-label="Upload files"
    />
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      disableEnforceFocus
      slotProps={{
        paper: { sx: ghostDialogPaperSx }
      }}
    >
      <DialogTitle sx={{ color: 'text.primary' }}>Add Source</DialogTitle>
      <DialogContent>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2
          }}>
          <FileDropZone
            hasFiles={hasFiles}
            hasUrl={hasUrl}
            urlInput={urlInput}
            urlError={urlError}
            importingUrl={importingUrl}
            onFilesSelected={onFilesSelected}
            onUrlInputChange={setUrlInput}
            onUrlErrorClear={() => setUrlError('')}
            onUrlSubmit={handleUrlSubmit}
            onBrowseClick={handleBrowseClick}
          />

          <FileList
            selectedFiles={selectedFiles}
            onRemoveFile={onRemoveFile}
            onClearSelection={onClearSelection}
            onBrowseClick={handleBrowseClick}
          />

          {showOptions && extractEntities && availableDomains.length > 0 && (
            <DomainSelect
              selectedDomain={selectedDomain}
              availableDomains={availableDomains}
              onDomainChange={onDomainChange}
              contextWindow={contextWindow}
              groupSize={groupSize}
              inputPerChunk={inputPerChunk}
              outputPerChunk={outputPerChunk}
            />
          )}

          {showOptions && (
            <ExtractionOptions
              extractEntities={extractEntities}
              onExtractEntitiesChange={onExtractEntitiesChange}
              enableVision={enableVision}
              onEnableVisionChange={onEnableVisionChange}
              showNormalizationOption={showNormalizationOption}
              enableNormalization={enableNormalization}
              onNormalizationChange={onNormalizationChange}
              analysisDepth={analysisDepth}
              onAnalysisDepthChange={onAnalysisDepthChange}
              contentFiltering={contentFiltering}
              onContentFilteringChange={onContentFilteringChange}
              filteringMode={filteringMode}
              onFilteringModeChange={onFilteringModeChange}
              skipDuplicates={skipDuplicates}
              onSkipDuplicatesChange={onSkipDuplicatesChange}
            />
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button
          onClick={handleClose}
          sx={ghostCancelBtnSx}
        >
          Cancel
        </Button>
        {uploading && onCancelUpload && (
          <Button
            variant="outlined"
            onClick={onCancelUpload}
            sx={ghostButtonSx(ChaosCypherPalette.error)}
          >
            Cancel Upload
          </Button>
        )}
        <Button
          variant="outlined"
          onClick={hasUrl ? handleUrlSubmit : onConfirm}
          disabled={(!hasUrl && !hasFiles) || importingUrl || uploading}
          startIcon={(importingUrl || uploading) ? <CircularProgress size={16} sx={{ color: 'primary.main' }} /> : undefined}
          sx={ghostButtonSx(ChaosCypherPalette.primary)}
        >
          {importingUrl ? 'Importing...' : uploading ? 'Uploading...' : 'Import'}
        </Button>
      </DialogActions>
    </Dialog>
    </>
  );
}
