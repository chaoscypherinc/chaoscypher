// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useEffect, useRef, useMemo } from 'react';
import { useSearchParams, useNavigate } from 'react-router';
import {
  Box,
  Chip,
  Dialog,
  Tooltip,
  Typography,
  Paper,
  Divider,
  ToggleButton,
  ToggleButtonGroup,
  FormControlLabel,
  Switch,
} from '@mui/material';
import ViewCompactIcon from '@mui/icons-material/ViewCompact';
import ViewStreamIcon from '@mui/icons-material/ViewStream';
import PhotoLibraryIcon from '@mui/icons-material/PhotoLibrary';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import ErrorOutlinedIcon from '@mui/icons-material/ErrorOutlined';
import ReactMarkdown from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';
import { SyntaxHighlighter, vscDarkPlus } from '../../../utils/syntaxHighlighter';
import {
  useSourceImages,
  pageNumberFromFilename,
} from '../../../services/api/useSourceImages';
import {
  useSourceChunks,
  useChunkOutputFeeds,
  useResolveHighlightChunkPage,
} from '../hooks/useSourceChunks';
import {
  useVisionPages,
  type VisionPageStatus,
} from '../../../services/api/useVisionPages';
import { useChunkDetail } from '../../../services/api/useChunkDetail';
import type {
  SourceChunk,
  ExtractedEntity,
  InferredRelationship,
  ExtractionTask,
  Source,
} from '../../../types';
import { isSourceExtracted } from '../../../types';
import { ChunkOverviewBand } from './chunks/ChunkOverviewBand';
import { PromptsSection } from './pipeline/PromptsSection';
import { useLLMProcessing } from './pipeline/hooks/useLLMProcessing';
import { Overlays } from '../../../theme/overlays';
import { surfaceSx } from '../../../theme/cardStyles';
import GhostPagination from '../../../components/GhostPagination';
import { ChunkPageThumbnail } from './ChunkPageThumbnail';
import { ChunkContentToggle } from './chunks/ChunkContentToggle';
import { ChunkInputDiff } from './chunks/ChunkInputDiff';
import { ChunkInputView } from './chunks/ChunkInputView';
import { ChunkOutputView } from './chunks/ChunkOutputView';

// Stable empty fallbacks for the OUTPUT feeds. Sharing one reference per
// kind keeps the `chunkGroups`/child props referentially stable while the
// feeds query is still loading (new `[]` literals each render would not be).
const EMPTY_ENTITIES: ExtractedEntity[] = [];
const EMPTY_RELATIONSHIPS: InferredRelationship[] = [];
const EMPTY_TASKS: ExtractionTask[] = [];

interface ChunksTabProps {
  source: Source;
  highlightChunkId?: string | null;
}

interface ChunkGroup {
  groupIndex: number | null;
  chunks: SourceChunk[];
}

/** Group consecutive chunks by group_index. Ungrouped chunks (null) each form their own group. */
function groupChunksByHierarchy(chunks: SourceChunk[]): ChunkGroup[] {
  const groups: ChunkGroup[] = [];
  for (const chunk of chunks) {
    const gi = chunk.group_index ?? null;
    const last = groups[groups.length - 1];
    if (gi !== null && last && last.groupIndex === gi) {
      last.chunks.push(chunk);
    } else {
      groups.push({ groupIndex: gi, chunks: [chunk] });
    }
  }
  return groups;
}

interface CombinedChunkDiffProps {
  sourceId: string;
  chunk: SourceChunk;
}

/**
 * One chunk's diff block inside the Original View "Show removed"
 * stack. Fetches the heavy raw_content via useChunkDetail, then
 * renders a ChunkInputDiff. When raw is null (legacy source), falls
 * back to the cleaned content with a small "pre-cleanup unavailable"
 * note appended so the operator knows why this chunk doesn't show
 * red strikethrough.
 */
function CombinedChunkDiff({ sourceId, chunk }: CombinedChunkDiffProps) {
  const { data: detail } = useChunkDetail(sourceId, chunk.id);
  const raw = (detail as { raw_content?: string | null } | undefined)?.raw_content ?? null;
  if (raw === null) {
    return (
      <Box sx={{ mb: 2 }}>
        <Typography
          sx={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.78rem', whiteSpace: 'pre-wrap' }}
        >
          {chunk.content}
        </Typography>
        <Typography
          sx={{ fontSize: '0.62rem', color: '#666', fontStyle: 'italic', mt: 0.5 }}
        >
          (pre-cleanup text unavailable for this chunk — re-extract to repopulate)
        </Typography>
      </Box>
    );
  }
  return (
    <Box sx={{ mb: 2 }}>
      <ChunkInputDiff cleaned={chunk.content} raw={raw} />
    </Box>
  );
}

/**
 * Resolve a chunk group to the extraction task that produced it.
 *
 * Extraction runs per *group*: ``build_extraction_groups`` packs chunks into
 * token-budgeted groups, each group is one LLM call → one ``ExtractionTask``.
 * The task's ``chunk_index`` is therefore the group ordinal (the same number
 * as each member chunk's ``group_index``), NOT an individual chunk index.
 *
 * Match strategy:
 *  1. Primary — a task whose ``small_chunk_ids`` include one of this group's
 *     chunk ids. Unambiguous and robust to any index-numbering drift.
 *  2. Fallback — match the task's ``chunk_index`` to the group ordinal (older
 *     rows that don't populate ``small_chunk_ids``). Ungrouped chunks
 *     (group_index null) fall back to the single chunk's own index.
 */
function findGroupTask(group: ChunkGroup, tasks: ExtractionTask[]): ExtractionTask | null {
  const ids = new Set(group.chunks.map((c) => c.id));
  const byIds = tasks.find((t) => t.small_chunk_ids?.some((id) => ids.has(id)));
  if (byIds) return byIds;

  const ordinal = group.groupIndex ?? group.chunks[0]?.chunk_index ?? null;
  if (ordinal === null) return null;
  return tasks.find((t) => t.chunk_index === ordinal) ?? null;
}

interface MemberChunkRowProps {
  sourceId: string;
  chunk: SourceChunk;
  /** False only for the first chunk in a group — drives the dotted divider. */
  showDivider: boolean;
  /** Tab-level "Show removed text" switch state (applies to all chunks). */
  showRemoved: boolean;
  /** The citation deep-link's target chunk id (null when none). */
  highlightChunkId?: string | null;
  /** Sentence ref(s) to highlight — only applied to the highlighted chunk. */
  highlightSentRef?: string | null;
  imageUrl: string | null;
  expectedImage: boolean;
  pageIsVisionFailed: boolean;
  pageIsRenderFailed: boolean;
  onExpand: (url: string) => void;
}

/**
 * One member chunk's INPUT row: ``#index``/``p.N`` header, the cleaned
 * (or cleaned↔raw diff when ``showRemoved`` is on) text, and the page
 * thumbnail. Re-fetches the heavy ``raw_content`` via ``useChunkDetail``
 * (the list endpoint never carries it) so the diff overlay can render.
 *
 * The INPUT/OUTPUT toggle no longer lives here — it sits once per group in
 * ``GroupExtractionBody`` — because extraction output is per group.
 */
function MemberChunkRow({
  sourceId,
  chunk,
  showDivider,
  showRemoved,
  highlightChunkId,
  highlightSentRef,
  imageUrl,
  expectedImage,
  pageIsVisionFailed,
  pageIsRenderFailed,
  onExpand,
}: MemberChunkRowProps) {
  const { data: detail } = useChunkDetail(sourceId, chunk.id);
  const rawContent =
    (detail as { raw_content?: string | null } | undefined)?.raw_content ?? null;
  const chunkMetadata =
    (detail as { chunk_metadata?: Record<string, unknown> | null } | undefined)?.chunk_metadata ??
    null;
  const isHighlighted = !!highlightChunkId && chunk.id === highlightChunkId;

  return (
    <Box>
      {showDivider && (
        <Divider
          sx={{
            borderStyle: 'dotted',
            borderColor: (theme) =>
              theme.palette.mode === 'dark' ? Overlays.border.dark : Overlays.border.light,
          }}
        />
      )}
      <Box sx={{ px: 2, py: 1.5, display: 'flex', gap: 2, alignItems: 'flex-start' }}>
        {/* Text column */}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <Typography variant="caption" sx={{ color: 'text.secondary', fontWeight: 600 }}>
              #{chunk.chunk_index + 1}
            </Typography>
            {chunk.page_number && (
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                p.{chunk.page_number}
              </Typography>
            )}
          </Box>
          <ChunkInputView
            cleaned={chunk.content}
            rawContent={rawContent}
            showRemoved={showRemoved}
            highlightSentRef={isHighlighted ? highlightSentRef : null}
            chunkMetadata={isHighlighted ? chunkMetadata : null}
          />
        </Box>
        {/* Thumbnail column (renders nothing when the source has no per-page images). */}
        <ChunkPageThumbnail
          imageUrl={imageUrl}
          pageNumber={chunk.page_number}
          expectedImage={expectedImage}
          pageIsVisionFailed={pageIsVisionFailed}
          pageIsRenderFailed={pageIsRenderFailed}
          onExpand={onExpand}
        />
      </Box>
    </Box>
  );
}

interface GroupExtractionBodyProps {
  sourceId: string;
  group: ChunkGroup;
  entities: ExtractedEntity[];
  relationships: InferredRelationship[];
  tasks: ExtractionTask[];
  showRemoved: boolean;
  highlightChunkId?: string | null;
  highlightSentRef?: string | null;
  visionEnabled?: boolean;
  hasAnyImages: boolean;
  pageToImageUrl: Map<number, string>;
  pageStatusByNumber: Map<number, VisionPageStatus>;
  pageErrorByNumber: Map<number, string | null>;
  onExpandImage: (url: string) => void;
}

/**
 * One group's body: a header bar hosting the group-level INPUT/OUTPUT toggle
 * and the "Show filtered" switch, the member chunk INPUT rows (always
 * visible), and — in OUTPUT mode — a single group-level ``ChunkOutputView``.
 *
 * State that lives per *group*: ``view`` (input/output) and ``showFiltered``.
 * These moved up from the old per-chunk ``ChunkRowBody`` because the LLM saw
 * the whole group's combined content and produced one output set for it; a
 * per-chunk toggle mis-attributed that output to individual chunks.
 */
function GroupExtractionBody({
  sourceId,
  group,
  entities,
  relationships,
  tasks,
  showRemoved,
  highlightChunkId,
  highlightSentRef,
  visionEnabled,
  hasAnyImages,
  pageToImageUrl,
  pageStatusByNumber,
  pageErrorByNumber,
  onExpandImage,
}: GroupExtractionBodyProps) {
  const [view, setView] = useState<'input' | 'output'>('input');
  const [showFiltered, setShowFiltered] = useState(false);

  const task = useMemo(() => findGroupTask(group, tasks), [group, tasks]);
  const outputAvailable = !!task && task.status === 'completed';
  const filteringLog = task?.filtering_log ?? null;
  const hasFilteringLog = !!filteringLog && filteringLog.stages.length > 0;
  // Entities/relationships carry the group ordinal as their ``chunk_index``
  // (set per group in ai_entities.py), so filter the OUTPUT view by the
  // matched task's chunk_index — falling back to the group ordinal.
  const outputIndex =
    task?.chunk_index ?? group.groupIndex ?? group.chunks[0]?.chunk_index ?? -1;

  return (
    <Box>
      {/* Group header bar — INPUT/OUTPUT toggle + "Show filtered" (OUTPUT only). */}
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'flex-end',
          alignItems: 'center',
          gap: 1.5,
          px: 2,
          pt: 1.5,
        }}
      >
        {view === 'output' && (
          <Tooltip
            title={
              hasFilteringLog
                ? 'Show items the LLM produced that post-extraction filters dropped from this group.'
                : 'No filtering ran on this group.'
            }
            arrow
            placement="top"
            describeChild
          >
            <span>
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={showFiltered}
                    disabled={!hasFilteringLog}
                    onChange={(_, checked) => setShowFiltered(checked)}
                  />
                }
                label="Show filtered"
                slotProps={{ typography: { sx: { fontSize: '0.72rem', color: 'text.secondary' } } }}
                sx={{ m: 0 }}
              />
            </span>
          </Tooltip>
        )}
        <ChunkContentToggle view={view} outputAvailable={outputAvailable} onChange={setView} />
      </Box>

      {/* Member chunk INPUT rows — always visible (INPUT and OUTPUT modes). */}
      {group.chunks.map((chunk, chunkIdx) => {
        const pageNumber = chunk.page_number;
        const imageUrl = pageNumber != null ? pageToImageUrl.get(pageNumber) ?? null : null;
        // We "expected" an image when (a) vision was on for this source, (b)
        // the chunk has a page number, and (c) the source produced at least
        // one image. Without all three we don't claim failure.
        const expectedImage =
          !imageUrl && visionEnabled === true && pageNumber != null && hasAnyImages;
        const chunkPageStatus =
          pageNumber != null ? pageStatusByNumber.get(pageNumber) : undefined;
        const pageIsVisionFailed = chunkPageStatus === 'failed';
        const pageIsRenderFailed =
          pageIsVisionFailed &&
          pageNumber != null &&
          (pageErrorByNumber.get(pageNumber)?.startsWith('render_failed:') ?? false);
        return (
          <MemberChunkRow
            key={chunk.id}
            sourceId={sourceId}
            chunk={chunk}
            showDivider={chunkIdx > 0}
            showRemoved={showRemoved}
            highlightChunkId={highlightChunkId}
            highlightSentRef={highlightSentRef}
            imageUrl={imageUrl}
            expectedImage={expectedImage}
            pageIsVisionFailed={pageIsVisionFailed}
            pageIsRenderFailed={pageIsRenderFailed}
            onExpand={onExpandImage}
          />
        );
      })}

      {/* Group-level OUTPUT — one block for the whole group's extraction. */}
      {view === 'output' && (
        <Box sx={{ px: 2, pb: 1.5, pt: 1 }}>
          <ChunkOutputView
            chunkIndex={outputIndex}
            // ChunkOutputView's prop type names an `InferredEntity` alias
            // that isn't exported from ../../../types (Wave 3 naming
            // mismatch — its accessors all match ExtractedEntity:
            // name/type/chunk_index/confidence). Cast through unknown so
            // the integration compiles without editing chunks/*.
            entities={entities as unknown as Parameters<typeof ChunkOutputView>[0]['entities']}
            relationships={relationships}
            task={task}
            showFiltered={showFiltered}
          />
        </Box>
      )}
    </Box>
  );
}

export function ChunksTab({ source, highlightChunkId }: ChunksTabProps) {
  const sourceId = source.id;
  const visionEnabled = source.upload_options?.enable_vision;
  // Backs the Chunk Overview band's "View chunk" fallback for older tasks
  // that lack ``small_chunk_ids``, plus any external
  // ``?highlight_chunk_index=N`` deep link. The index → id resolution
  // feeds the same highlight path as ``?highlight=<chunk_id>``. We
  // intentionally do not mutate the URL; the resolved id is held in
  // local state.
  const [searchParams] = useSearchParams();
  const highlightChunkIndexParam = searchParams.get('highlight_chunk_index');
  // Citation deep-links append ?sentence=Sn so the cited sentence is marked.
  const highlightSentRef = searchParams.get('sentence');
  const highlightChunkIndex = useMemo(() => {
    if (highlightChunkIndexParam == null) return null;
    const parsed = Number.parseInt(highlightChunkIndexParam, 10);
    return Number.isFinite(parsed) ? parsed : null;
  }, [highlightChunkIndexParam]);

  // `userPage` is null until the operator paginates; the effective page is
  // derived (below) as userPage → resolved highlight page → 1. Deriving
  // rather than snapping via an effect avoids a setState-in-effect cascade.
  const [userPage, setUserPage] = useState<number | null>(null);
  const navigate = useNavigate();
  const { state: llm, selectChunk } = useLLMProcessing(sourceId, isSourceExtracted(source));
  // In-page jump target set by the Chunk Overview band's "View chunk".
  const [localHighlightId, setLocalHighlightId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'separate' | 'combined' | 'images'>(
    'separate',
  );
  const [expandedImage, setExpandedImage] = useState<string | null>(null);
  // Tab-level "Show removed text" — flips every chunk to the
  // cleaned↔raw diff overlay in one go. Works in both Separate mode
  // (per-chunk diff blocks) and Original View (concatenated diff
  // stack via CombinedDiffStack). Disabled in Images mode (page
  // thumbnails have no text to diff).
  const [showRemoved, setShowRemoved] = useState(false);
  const pageSize = 50;

  // Tab-level OUTPUT feeds used by every Separate-mode <ChunkRowBody>:
  // entities + relationships filtered per-chunk in OUTPUT view, and the
  // extraction tasks list used to find the per-chunk filtering_log.
  // Loaded once at the tab level (single combined query) so we don't N+1
  // across chunks. On failure the OUTPUT view simply shows empty panels —
  // the rest of the tab (chunk list, INPUT view) still works.
  const { data: outputFeeds } = useChunkOutputFeeds(sourceId);
  const entities = outputFeeds?.entities ?? EMPTY_ENTITIES;
  const relationships = outputFeeds?.relationships ?? EMPTY_RELATIONSHIPS;
  const extractionTasks = outputFeeds?.tasks ?? EMPTY_TASKS;

  const highlightedGroupRef = useRef<HTMLDivElement>(null);

  // Fetch page images once; we use this both for the per-chunk
  // thumbnail and to infer "render failed" placeholders for pages we
  // expected an image for.
  const { data: sourceImages = [] } = useSourceImages(sourceId);

  // page number → full image URL. Built once per image-list change so
  // we don't rebuild it on every chunk render. Filenames that don't
  // match the `page_N.png` convention are dropped (defensive against
  // future non-PDF rendered-image schemes).
  const pageToImageUrl = useMemo(() => {
    const map = new Map<number, string>();
    for (const img of sourceImages) {
      const pageNum = pageNumberFromFilename(img.filename);
      if (pageNum != null) {
        map.set(pageNum, img.url);
      }
    }
    return map;
  }, [sourceImages]);

  // Whether this source ever wrote per-page images. Used as a
  // belt-and-braces guard alongside `visionEnabled` so a non-PDF
  // source with vision=true but zero rendered images (e.g. a TXT
  // file) doesn't show "Render failed" placeholders for every chunk.
  const hasAnyImages = pageToImageUrl.size > 0;

  // Per-page vision status, used to drive the "Vision failed" / "Render
  // failed" overlays on chunk thumbnails and the Images grid. The query
  // returns ``job: null`` for sources without a vision_job (text-only)
  // and we tolerate the absence by yielding an empty lookup map.
  const { data: visionData } = useVisionPages(sourceId);
  const pageStatusByNumber = useMemo(
    () =>
      new Map<number, VisionPageStatus>(
        (visionData?.pages ?? []).map((p) => [p.page_number, p.status]),
      ),
    [visionData],
  );
  // Quick lookup for the per-page error message — used to distinguish
  // "vision processing failed" from "PDF render failed" via the
  // ``render_failed:`` prefix written by the vision_page handler
  // (vision_operations_service.py). The dedicated
  // ``loader_pdf_failed_pages`` column was dropped by migration 0034, so
  // we recover the distinction from the per-page error string instead.
  const pageErrorByNumber = useMemo(
    () =>
      new Map<number, string | null>(
        (visionData?.pages ?? []).map((p) => [p.page_number, p.error_message]),
      ),
    [visionData],
  );

  // Resolve the correct page for the highlighted chunk before loading the
  // list. With no highlight this resolves immediately (page: null).
  const { page: highlightPage, resolved: initialPageResolved } =
    useResolveHighlightChunkPage(sourceId, localHighlightId ?? highlightChunkId, pageSize);

  // Once the operator paginates, `userPage` wins; until then we open on the
  // resolved highlight page (or page 1 when there's nothing to highlight).
  const page = userPage ?? highlightPage ?? 1;

  // Chunk list query — gated on the highlight page being resolved so we fetch
  // the right page first time instead of page 1 then the resolved page.
  const {
    chunks,
    total,
    isLoading: chunksLoading,
  } = useSourceChunks(sourceId, page, pageSize, initialPageResolved);
  const loading = chunksLoading;

  // Translate ``?highlight_chunk_index=N`` → chunk id once chunks load.
  // Derived (not stored via a setState-in-effect) so there's no cascading
  // re-render; recomputes when the param or the loaded chunks change.
  const resolvedIndexHighlightId = useMemo(() => {
    if (highlightChunkIndex == null) return null;
    return chunks.find((c) => c.chunk_index === highlightChunkIndex)?.id ?? null;
  }, [highlightChunkIndex, chunks]);
  // The local highlight (from the band) wins; then the id-style highlight
  // (passed by the parent); then the index-style highlight resolved from chunks.
  const effectiveHighlightChunkId =
    localHighlightId ?? highlightChunkId ?? resolvedIndexHighlightId ?? null;

  // Band "View chunk": modern tasks carry small_chunk_ids → jump in-page by
  // setting the local highlight (reset pagination so the resolved highlight
  // page wins). Older tasks without member ids fall back to the same-route
  // index deep-link the existing machinery already understands.
  const handleBandViewChunk = (task: ExtractionTask) => {
    // The scroll target (highlightedGroupRef) only exists in Separate mode, so
    // jumping from Original View / Images would otherwise silently do nothing.
    setViewMode('separate');
    const firstChunkId = task.small_chunk_ids?.[0];
    if (firstChunkId) {
      setUserPage(null);
      setLocalHighlightId(firstChunkId);
    } else {
      navigate(
        `/sources/${sourceId}?highlight=&highlight_chunk_index=${task.chunk_index}&tab=chunks`,
      );
    }
  };

  // Scroll to highlighted group when loaded
  useEffect(() => {
    if (effectiveHighlightChunkId && chunks.length > 0 && highlightedGroupRef.current) {
      const timer = setTimeout(() => {
        highlightedGroupRef.current?.scrollIntoView({
          behavior: 'smooth',
          block: 'center',
        });
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [effectiveHighlightChunkId, chunks]);

  // Compute groups and highlight target
  const chunkGroups = useMemo(() => groupChunksByHierarchy(chunks), [chunks]);

  const highlightGroupIndex = useMemo(() => {
    if (!effectiveHighlightChunkId) return undefined;
    const found = chunks.find((c) => c.id === effectiveHighlightChunkId);
    return found?.group_index ?? undefined;
  }, [effectiveHighlightChunkId, chunks]);

  if (loading && chunks.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          p: 4
        }}>
        <Typography sx={{
          color: "text.secondary"
        }}>Loading chunks...</Typography>
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="h6" sx={{ fontWeight: 700, mb: 2.5 }}>
        Document Chunks
      </Typography>
      <ChunkOverviewBand
        source={source}
        llm={llm}
        onSelectChunk={selectChunk}
        onViewChunk={handleBandViewChunk}
      />
      {/* View Mode Toggle */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          mb: 2
        }}>
        <Typography sx={{ fontSize: '1rem', fontWeight: 600 }}>Chunk text</Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Tooltip
            title={
              viewMode === 'images'
                ? 'Switch to Separate or Original View to inspect what cleanup removed.'
                : 'Cleanup removes this text before both AI extraction AND search indexing. The red strikethrough text was never seen by the LLM and is not vector-indexed.'
            }
            arrow
            placement="top"
            describeChild
          >
            <span>
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={showRemoved}
                    disabled={viewMode === 'images'}
                    onChange={(_, checked) => setShowRemoved(checked)}
                  />
                }
                label="Show removed text"
                slotProps={{ typography: { sx: { fontSize: '0.78rem', color: 'text.secondary' } } }}
                sx={{ m: 0 }}
              />
            </span>
          </Tooltip>
          <ToggleButtonGroup
            value={viewMode}
            exclusive
            onChange={(_, newMode) => newMode && setViewMode(newMode)}
            size="small"
          >
            <ToggleButton value="separate">
              <ViewCompactIcon sx={{ mr: 1 }} />
              Separate
            </ToggleButton>
            <ToggleButton value="combined">
              <ViewStreamIcon sx={{ mr: 1 }} />
              Original View
            </ToggleButton>
            {/* "Images" only renders when the source actually has page
                images — otherwise the toggle is hidden entirely so the
                control stays clean for text-only sources. */}
            {hasAnyImages && (
              <ToggleButton value="images">
                <PhotoLibraryIcon sx={{ mr: 1 }} />
                Images
              </ToggleButton>
            )}
          </ToggleButtonGroup>
        </Box>
      </Box>
      {viewMode === 'images' ? (
        <Paper variant="outlined" sx={{ p: 2, ...surfaceSx }}>
          <Typography
            variant="subtitle2"
            sx={{ mb: 1.5, color: 'text.secondary' }}
          >
            {pageToImageUrl.size} page{pageToImageUrl.size === 1 ? '' : 's'} rendered
          </Typography>
          {/* CSS Grid with auto-fill + 1fr columns: the browser fits
              as many ~180px columns as the container allows and
              stretches each cell to fill the row evenly, so there's
              no dead space on the right edge. Each thumbnail uses
              `aspect-ratio` to keep its portrait shape instead of a
              fixed pixel height, so the cells scale together as the
              viewport widens. */}
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
              gap: 1.5,
            }}
          >
            {[...pageToImageUrl.entries()]
              .sort(([a], [b]) => a - b)
              .map(([pageNum, url]) => {
                // Drive the overlay state from the vision_pages query:
                // any ``failed`` row means vision failed for that page,
                // and a ``render_failed:`` error_message prefix means
                // PDF rendering failed before vision ever ran (the
                // dedicated ``loader_pdf_failed_pages`` column was
                // dropped by migration 0034 — we recover the
                // distinction from the per-page error string).
                const pageStatus = pageStatusByNumber.get(pageNum);
                const isVisionFailed = pageStatus === 'failed';
                const isRenderFailed =
                  isVisionFailed &&
                  (pageErrorByNumber.get(pageNum)?.startsWith('render_failed:') ?? false);
                const overlayBorderColor = isRenderFailed
                  ? 'error.main'
                  : isVisionFailed
                    ? 'warning.main'
                    : 'divider';
                const overlayLabel = isRenderFailed
                  ? '⚠ Render'
                  : isVisionFailed
                    ? '⚠ Vision'
                    : null;
                const overlayChipColor: 'error' | 'warning' = isRenderFailed
                  ? 'error'
                  : 'warning';
                const overlayIcon = isRenderFailed ? (
                  <ErrorOutlinedIcon sx={{ fontSize: 12 }} />
                ) : (
                  <WarningAmberIcon sx={{ fontSize: 12 }} />
                );
                const tooltipText = isRenderFailed
                  ? `Page ${pageNum} — PDF rendering failed on this page.`
                  : isVisionFailed
                    ? `Page ${pageNum} — vision processing failed on this page.`
                    : `Page ${pageNum} — click to expand`;
                return (
                  <Tooltip key={pageNum} title={tooltipText} arrow>
                    <Box sx={{ position: 'relative' }}>
                      <Box
                        component="img"
                        src={url}
                        alt={`Page ${pageNum}`}
                        loading="lazy"
                        onClick={() => setExpandedImage(url)}
                        sx={{
                          width: '100%',
                          aspectRatio: '180 / 230',
                          objectFit: 'cover',
                          objectPosition: 'top',
                          borderRadius: 1,
                          border: '1px solid',
                          borderColor: overlayBorderColor,
                          cursor: 'pointer',
                          transition: 'transform 0.15s, border-color 0.15s',
                          '&:hover': {
                            transform: 'scale(1.02)',
                            borderColor: isRenderFailed || isVisionFailed
                              ? overlayBorderColor
                              : 'primary.main',
                          },
                        }}
                      />
                      {overlayLabel && (
                        <Chip
                          label={overlayLabel}
                          size="small"
                          color={overlayChipColor}
                          icon={overlayIcon}
                          sx={{
                            position: 'absolute',
                            top: 4,
                            left: 4,
                            height: 20,
                            fontSize: '0.65rem',
                            fontWeight: 600,
                            // Stop the click from reaching the image
                            // (which expands it); operators still see
                            // the same context via tooltip + the badge
                            // on the sources list.
                            pointerEvents: 'none',
                          }}
                        />
                      )}
                    </Box>
                  </Tooltip>
                );
              })}
          </Box>
        </Paper>
      ) : viewMode === 'separate' ? (
        <>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 1
            }}>
            {chunkGroups.map((group, groupIdx) => {
              const isGroupHighlighted =
                highlightGroupIndex !== undefined &&
                group.groupIndex !== null &&
                group.groupIndex === highlightGroupIndex;
              // For ungrouped chunks, highlight if the chunk itself matches
              const isUngroupedHighlight =
                group.groupIndex === null &&
                group.chunks.length === 1 &&
                group.chunks[0].id === effectiveHighlightChunkId;
              const isHighlighted = isGroupHighlighted || isUngroupedHighlight;

              return (
                <Paper
                  key={`group-${groupIdx}`}
                  ref={isHighlighted ? highlightedGroupRef : null}
                  variant="outlined"
                  sx={{
                    display: 'flex',
                    overflow: 'hidden',
                    transition: 'all 0.3s ease',
                    // Shared dark surface so the chunk-text rows read as one
                    // dark zone with the extraction grid above. The highlight
                    // spread below overrides it when a group is selected.
                    ...surfaceSx,
                    ...(isHighlighted && {
                      borderColor: 'primary.main',
                      borderWidth: 2,
                      backgroundColor: (theme) =>
                        theme.palette.mode === 'dark'
                          ? 'rgba(144, 202, 249, 0.08)'
                          : 'rgba(25, 118, 210, 0.04)',
                    }),
                  }}
                >
                  {/* Group label column */}
                  <Box
                    sx={{
                      width: 48,
                      minWidth: 48,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      borderRight: 1,
                      borderColor: 'divider',
                      backgroundColor: (theme) =>
                        theme.palette.mode === 'dark'
                          ? Overlays.subtle.dark
                          : Overlays.subtle.light,
                    }}
                  >
                    <Typography
                      variant="caption"
                      sx={{
                        color: "text.secondary",
                        fontWeight: 600,
                        fontSize: '0.7rem'
                      }}>
                      {group.groupIndex !== null ? `G${group.groupIndex + 1}` : '\u2014'}
                    </Typography>
                  </Box>
                  {/* Chunks column — one group-level INPUT/OUTPUT body. */}
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <GroupExtractionBody
                      sourceId={sourceId}
                      group={group}
                      entities={entities}
                      relationships={relationships}
                      tasks={extractionTasks}
                      showRemoved={showRemoved}
                      highlightChunkId={effectiveHighlightChunkId}
                      highlightSentRef={highlightSentRef}
                      visionEnabled={visionEnabled}
                      hasAnyImages={hasAnyImages}
                      pageToImageUrl={pageToImageUrl}
                      pageStatusByNumber={pageStatusByNumber}
                      pageErrorByNumber={pageErrorByNumber}
                      onExpandImage={setExpandedImage}
                    />
                  </Box>
                </Paper>
              );
            })}
          </Box>

          <GhostPagination
            page={page}
            totalPages={Math.ceil(total / pageSize)}
            total={total}
            pageSize={pageSize}
            onPageChange={setUserPage}
          />
        </>
      ) : (
        <Paper variant="outlined" sx={{ p: 3, ...surfaceSx }}>
          <Box sx={{ maxHeight: '70vh', overflow: 'auto' }}>
            {showRemoved ? (
              // Diff stack — one CombinedChunkDiff per chunk on this page.
              // Markdown formatting is dropped while the overlay is on so
              // the red strikethrough mapping stays accurate (same trade-off
              // as the per-chunk INPUT view in Separate mode).
              chunks.map((c) => (
                <CombinedChunkDiff key={c.id} sourceId={sourceId} chunk={c} />
              ))
            ) : (
              <ReactMarkdown
                children={chunks.map((c) => c.content).join('\n\n')}
                rehypePlugins={[rehypeSanitize]}
                components={{
                  code({ node, children, ...props }) {
                    const className = (node?.properties?.className as string[])?.join(' ') || '';
                    const match = /language-(\w+)/.exec(className);
                    return match ? (
                      <SyntaxHighlighter
                        children={String(children).replace(/\n$/, '')}
                        style={vscDarkPlus}
                        language={match[1]}
                        PreTag="div"
                      />
                    ) : (
                      <code className={className} {...props}>
                        {children}
                      </code>
                    );
                  },
                }}
              />
            )}
          </Box>
        </Paper>
      )}
      {/* Click-to-expand dialog for chunk page thumbnails (shared
          across all rows so we don't multiply Dialogs into the DOM). */}
      <Dialog
        open={!!expandedImage}
        onClose={() => setExpandedImage(null)}
        maxWidth="lg"
      >
        {expandedImage && (
          <Box
            component="img"
            src={expandedImage}
            alt="Expanded page"
            sx={{ maxWidth: '100%', maxHeight: '85vh', display: 'block' }}
            onClick={() => setExpandedImage(null)}
          />
        )}
      </Dialog>
      <PromptsSection stats={llm.stats} />
    </Box>
  );
}
