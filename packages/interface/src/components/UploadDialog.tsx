// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module UploadDialog
 * Dialog shell for uploading documents to the knowledge graph. Orchestrates
 * FileDropZone, FileList, and ExtractionOptions sub-components.
 *
 * A web address typed into the URL field is checked for a site once typing
 * pauses (`web.site_preview_debounce_ms`); when the check finds more than one
 * page, the lazily loaded SiteImportOffer offers the whole site beside "Just
 * this page".
 */

import { lazy, Suspense, useMemo, useState, useCallback, useRef } from 'react';
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
import { ExtractionOptions } from './upload/ExtractionOptions';
import { useAppConfig } from '../contexts/useAppConfig';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useSitePreview } from '../services/api/useSitePreview';
import { getApiErrorMessage } from '../utils/errors';

// PackSelect brings MUI Autocomplete (~7 kB brotlied). This dialog renders
// from Layout, so a static import puts it in the entry chunk and over the
// .size-limit.json budget; loaded lazily it arrives when a file is picked.
const PackSelect = lazy(() =>
  import('./upload/PackSelect').then((m) => ({ default: m.PackSelect })),
);
// The site offer (radio choice, page list) is lazy for the same budget: it
// arrives once an address has been checked.
const SiteImportOffer = lazy(() =>
  import('./upload/SiteImportOffer').then((m) => ({ default: m.SiteImportOffer })),
);

const NORMALIZATION_FILE_TYPES = new Set(['pdf', 'docx', 'doc', 'html', 'htm', 'odt', 'rtf', 'epub']);

/** Whether `value` is an absolute http(s) address worth checking for a site. */
function isWebAddress(value: string): boolean {
  try {
    const url = new URL(value);
    return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname.includes('.');
  } catch {
    return false;
  }
}

/** The whole site chosen for an address, but the `exclude`d pages (the ones unticked). */
interface SiteChoice {
  url: string;
  exclude: string[];
}

const ACCEPTED_EXTENSIONS = '.txt,.md,.log,.pdf,.csv,.json,.jsonl,.html,.htm,.docx,.doc,.odt,.rtf,.epub,.ttl,.rdf,.nt,.zip,.tar.gz,.tgz,.jpg,.jpeg,.png,.gif,.webp,.tiff,.tif,.bmp,.mp3,.wav,.m4a,.flac,.ogg,.wma,.aac,.mp4,.mkv,.avi,.mov,.webm,.wmv,.flv';

// ── Component ─────────────────────────────────────────────────────────────

interface UploadDialogProps {
  open: boolean;
  onClose: () => void;
  selectedFiles: File[];
  analysisDepth: 'quick' | 'full';
  enableNormalization: boolean;
  /** The chosen packs; `null` leaves them to detection (confirmed after upload). */
  selectedPacks: string[] | null;
  availableDomains: ExtractionDomain[];
  onFilesSelected: (files: File[]) => void;
  onAnalysisDepthChange: (value: 'quick' | 'full') => void;
  onNormalizationChange: (value: boolean) => void;
  onPacksChange: (packs: string[] | null) => void;
  onConfirm: () => void;
  uploading?: boolean;
  onCancelUpload?: () => void;
  onClearSelection: () => void;
  onRemoveFile: (index: number) => void;
  onUrlImport: (url: string) => Promise<void>;
  /**
   * Import a site: every page its import finds but the `exclude`d ones (empty:
   * every page). Exclusions, not a page list, so a capped check never drops
   * the pages it did not see.
   */
  onSiteImport: (url: string, exclude: string[]) => Promise<void>;
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
  selectedPacks,
  availableDomains,
  onFilesSelected,
  onAnalysisDepthChange,
  onNormalizationChange,
  onPacksChange,
  onConfirm,
  uploading,
  onCancelUpload,
  onClearSelection,
  onRemoveFile,
  onUrlImport,
  onSiteImport,
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

  // Site check: the address, once typing pauses, if it is a web address.
  const {
    web_site_preview_debounce_ms: previewDebounceMs,
    web_site_max_pages: siteMaxPages,
  } = useAppConfig();
  const trimmedUrl = urlInput.trim();
  const checkedUrl = useDebouncedValue(
    open && selectedFiles.length === 0 && isWebAddress(trimmedUrl) ? trimmedUrl : null,
    previewDebounceMs,
  );
  const sitePreview = useSitePreview(checkedUrl);
  const [siteChoice, setSiteChoice] = useState<SiteChoice | null>(null);
  const preview = checkedUrl === trimmedUrl ? sitePreview.data : undefined;
  const offersSite = !!preview && preview.page_count > 1;
  const choice = offersSite && siteChoice?.url === checkedUrl ? siteChoice : null;
  const importsSite = choice !== null;
  // Every page the check listed is unticked: nothing it found would be imported.
  const noPagesChosen =
    !!choice && !!preview && preview.pages.length > 0 && choice.exclude.length >= preview.pages.length;

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
    setSiteChoice(null);
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
    if (choice && preview) {
      await onSiteImport(preview.url, choice.exclude);
    } else {
      await onUrlImport(trimmed);
    }
    if (!importingUrl) {
      setUrlInput('');
      setSiteChoice(null);
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

          {hasUrl && !hasFiles && checkedUrl === trimmedUrl && checkedUrl && (
            <Suspense fallback={null}>
            <SiteImportOffer
              key={checkedUrl}
              checking={sitePreview.isFetching && !sitePreview.data}
              error={sitePreview.isError ? getApiErrorMessage(sitePreview.error) : null}
              preview={preview}
              onChoiceChange={(exclude) =>
                setSiteChoice(exclude === undefined ? null : { url: checkedUrl, exclude })
              }
              maxPages={siteMaxPages}
            />
            </Suspense>
          )}

          <FileList
            selectedFiles={selectedFiles}
            onRemoveFile={onRemoveFile}
            onClearSelection={onClearSelection}
            onBrowseClick={handleBrowseClick}
          />

          {showOptions && extractEntities && availableDomains.length > 0 && (
            <Suspense fallback={null}>
              <PackSelect
                value={selectedPacks}
                availableDomains={availableDomains}
                onChange={onPacksChange}
                allowAuto
                proposalReviewed={!importsSite}
                contextWindow={contextWindow}
                groupSize={groupSize}
                inputPerChunk={inputPerChunk}
                outputPerChunk={outputPerChunk}
              />
            </Suspense>
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
          disabled={(!hasUrl && !hasFiles) || importingUrl || uploading || noPagesChosen}
          startIcon={(importingUrl || uploading) ? <CircularProgress size={16} sx={{ color: 'primary.main' }} /> : undefined}
          sx={ghostButtonSx(ChaosCypherPalette.primary)}
        >
          {importingUrl ? 'Importing...' : uploading ? 'Uploading...' : importsSite ? 'Import site' : 'Import'}
        </Button>
      </DialogActions>
    </Dialog>
    </>
  );
}
