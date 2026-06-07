// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module useUploadWizard
 *
 * Orchestration hook for the upfront domain-confirmation upload wizard
 * (single-file uploads only). Composes the three wizard phases:
 *
 *   1   Select   — owned by the caller (UploadDialog / useSourcesUpload).
 *   1.5 Analyzing — a targeted poll of GET /sources/{id} at a fast cadence
 *                   (WIZARD_POLL_MS) with a custom predicate that keeps
 *                   polling until the eager `detection_proposal` is populated,
 *                   bounded by a hard timeout (WIZARD_ANALYZE_TIMEOUT_MS).
 *   2   Review   — the existing ConfirmExtractionDialog, fed from the polled
 *                   source; confirm hits the state-aware confirm endpoint.
 *
 * Branching happens in `start`:
 *   - Auto domain   → POST /sources with NO forced domain (engages detection)
 *                     → phase 'analyzing' → poll → 'review'.
 *   - Specific domain → POST with forced_domain + auto_confirm=true (override
 *                     fast-path): the gate is bypassed, no poll/review, the
 *                     wizard reports 'fast-path' so the caller closes normally.
 *   - Skipped duplicate → the upload short-circuits server-side; no wizard.
 *
 * The poll predicate is intentionally NOT `isSourceProcessing` /
 * `hasProcessingSources`: those treat `awaiting_confirmation` as terminal and
 * would stop at the wrong moment. The wizard polls strictly until the
 * detection proposal lands (which happens while the source is still INDEXING,
 * well before the analysis-stage gate would park it).
 *
 * On timeout the wizard closes gracefully: the source proceeds through the
 * existing pipeline and surfaces the universal `awaiting_confirmation` chip.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { sourcesApi } from '../services/api/sources';
import type { ConfirmExtractionOptions } from '../services/api/sourceProcessing';
import { useConfirmExtraction } from '../services/api/useSources';
import type { Source, UnifiedSource } from '../types';
import type { RankedDomain } from '../types/source';
import { getApiErrorMessage, isAbortError } from '../utils/errors';
import { logger } from '../utils/logger';

// ── Timing constants ────────────────────────────────────────────────────────
// Deliberately faster than the 3s list/detail poll: the eager backend
// detection writes the proposal within ~1-2s of upload, so the wizard wants a
// sub-second cadence to feel instant. Named (not inlined) per spec.
export const WIZARD_POLL_MS = 700;
// Hard ceiling on the Analyzing step. If the proposal hasn't landed by now
// (slow/large/vision doc, backend hiccup) the wizard bails to the chip.
export const WIZARD_ANALYZE_TIMEOUT_MS = 8000;

// ── Public types ─────────────────────────────────────────────────────────────

export type WizardPhase = 'idle' | 'analyzing' | 'review' | 'error';

/** Result of `start` — tells the caller how the upload was handled. */
export type WizardStartOutcome =
  /** Auto domain: the wizard took over (analyzing → review). */
  | 'wizard'
  /** Specific domain: override fast-path, gate bypassed, no review. */
  | 'fast-path'
  /** The upload was skipped as a duplicate server-side; no wizard. */
  | 'skipped'
  /** The upload POST itself failed; the wizard shows its error phase. */
  | 'error';

/**
 * Rich result of `start`. The caller (useSourcesUpload) uses `outcome` to
 * decide flow and `source` to surface the duplicate toast (skipped) — that
 * feedback lives in the upload hook which owns the onError/onInfo callbacks.
 */
export interface WizardStartResult {
  outcome: WizardStartOutcome;
  /** The upload POST result, when one came back (skipped / fast-path). */
  source: Source | null;
}

/** Upload parameters the wizard forwards to `sourcesApi.upload`. */
export interface WizardUploadParams {
  file: File;
  extractEntities: boolean;
  analysisDepth: 'quick' | 'full';
  enableNormalization: boolean;
  enableVision: boolean;
  /** filteringMode '' is treated as "use default" (sent as undefined). */
  filteringMode: string;
  contentFiltering: boolean;
  skipDuplicates: boolean;
  /** '__auto__' (or undefined) → auto-detect; anything else → forced domain. */
  domain: string;
  /** Abort signal for the upload POST (the caller's Cancel-Upload affordance). */
  signal?: AbortSignal;
}

export interface UseUploadWizardReturn {
  phase: WizardPhase;
  /** Polled source (mapped for ConfirmExtractionDialog) once review-ready. */
  source: UnifiedSource | null;
  /** Human-readable error message for the error phase, or null. */
  error: string | null;
  /** True while the confirm mutation is in flight (disable the Confirm btn). */
  confirming: boolean;
  /** Start the wizard for a single selected file. Resolves to the result. */
  start: (params: WizardUploadParams) => Promise<WizardStartResult>;
  /** Confirm the reviewed domain → state-aware confirm endpoint. */
  confirm: (options: ConfirmExtractionOptions) => Promise<void>;
  /** Close/reset the wizard (cancel review, dismiss error, stop polling). */
  cancel: () => void;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

const AUTO_SENTINEL = '__auto__';

/**
 * Whether the polled source carries the eager detection proposal yet. The
 * backend surfaces the (excluded) `detection_proposal` blob verbatim as
 * `proposed_extraction_options`; it is null until the proposal is written, so
 * a non-null value is the precise "proposal populated" signal. (The no_text
 * short-circuit also populates it, with low_confidence/no_text flags inside.)
 */
function hasDetectionProposal(source: Source | undefined): boolean {
  return !!source && source.proposed_extraction_options != null;
}

/**
 * Map the polled full Source (SourceResponse) to the minimal UnifiedSource the
 * ConfirmExtractionDialog reads: id + the derived detection_* fields. Both
 * SourceResponse and SourceSummaryResponse expose the identical detection
 * surface, so this is a focused projection rather than the full
 * `mapSourceToUnified` (which is typed for the list SourceSummary).
 */
function toReviewSource(source: Source): UnifiedSource {
  return {
    id: source.id,
    stage: 'processing',
    title: source.title ?? source.filename ?? '',
    source_type: source.source_type ?? source.file_type ?? '',
    size: source.file_size ?? 0,
    status: source.status,
    created_at: source.indexing_started_at ?? new Date().toISOString(),
    confirmation_required: source.confirmation_required,
    extraction_confirmed_at: source.extraction_confirmed_at ?? null,
    detection_ranking: (source.detection_ranking as RankedDomain[] | undefined) ?? undefined,
    detection_confidence: source.detection_confidence ?? undefined,
    detection_low_confidence: source.detection_low_confidence ?? undefined,
    proposed_extraction_options:
      (source.proposed_extraction_options as UnifiedSource['proposed_extraction_options']) ??
      undefined,
  };
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useUploadWizard(): UseUploadWizardReturn {
  const [phase, setPhase] = useState<WizardPhase>('idle');
  const [error, setError] = useState<string | null>(null);
  // Id of the source being analyzed; null disables the poll query.
  const [sourceId, setSourceId] = useState<string | null>(null);
  // Wall-clock start of the Analyzing step, used to bound the poll cadence
  // from inside refetchInterval (which only sees query state).
  const analyzeStartedAt = useRef<number>(0);
  const timeoutTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const confirmMutation = useConfirmExtraction();

  const pollQuery = useQuery<Source>({
    queryKey: ['wizard', 'source', sourceId],
    queryFn: () => sourcesApi.get(sourceId as string),
    // Only poll during the Analyzing step for a known source id.
    enabled: !!sourceId && phase === 'analyzing',
    // Always hit the network — a stale cached row would defeat the poll.
    staleTime: 0,
    gcTime: 0,
    refetchInterval: (query) => {
      if (phase !== 'analyzing') return false;
      const data = query.state.data;
      // Stop the instant the eager proposal lands → caller flips to 'review'.
      if (hasDetectionProposal(data)) return false;
      // Stop once the hard ceiling is exceeded; the timeout effect flips the
      // phase to the chip fallback independently (so it fires even if no
      // further poll re-render happens).
      if (Date.now() - analyzeStartedAt.current >= WIZARD_ANALYZE_TIMEOUT_MS) {
        return false;
      }
      return WIZARD_POLL_MS;
    },
  });

  // Derive the proposal→review transition from the poll result during render
  // (cheap, idempotent) so the review source is available the same commit the
  // proposal arrives.
  const polled = pollQuery.data;
  if (phase === 'analyzing' && hasDetectionProposal(polled)) {
    setPhase('review');
  }

  const reviewSource =
    phase === 'review' && polled ? toReviewSource(polled) : null;

  const clearTimeoutTimer = useCallback(() => {
    if (timeoutTimer.current) {
      clearTimeout(timeoutTimer.current);
      timeoutTimer.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    clearTimeoutTimer();
    setPhase('idle');
    setError(null);
    setSourceId(null);
    analyzeStartedAt.current = 0;
  }, [clearTimeoutTimer]);

  // Hard-timeout guard: once Analyzing begins, arm a one-shot timer that closes
  // the wizard if the proposal never lands. The source then proceeds to the
  // universal awaiting_confirmation chip. Leaving Analyzing (review / reset)
  // clears the timer. Re-arms whenever a new analyze session starts (keyed on
  // sourceId).
  useEffect(() => {
    if (phase !== 'analyzing' || !sourceId) return;
    const timer = setTimeout(() => {
      logger.warn('Upload wizard analyze timed out; falling back to chip', {
        sourceId,
      });
      // Same teardown as a cancel/close — fold the timeout into `reset()` so
      // the idle-restore stays in one place. `error` is already null on the
      // Analyzing path (errors flip the phase to 'error'), so the extra
      // setError(null) is a no-op here.
      reset();
    }, WIZARD_ANALYZE_TIMEOUT_MS);
    timeoutTimer.current = timer;
    return () => {
      clearTimeout(timer);
      if (timeoutTimer.current === timer) timeoutTimer.current = null;
    };
  }, [phase, sourceId, reset]);

  const start = useCallback(
    async (params: WizardUploadParams): Promise<WizardStartResult> => {
      setError(null);
      const isAuto = !params.domain || params.domain === AUTO_SENTINEL;
      const domainToUse = isAuto ? undefined : params.domain;

      let result: Source;
      try {
        result = await sourcesApi.upload(
          params.file,
          params.extractEntities,
          params.analysisDepth,
          params.enableNormalization,
          domainToUse,
          undefined, // progress handled by the caller's own upload path
          params.enableVision,
          params.filteringMode || undefined,
          params.signal,
          params.contentFiltering,
          params.skipDuplicates,
          // Override fast-path: a specific domain bypasses the gate entirely.
          !isAuto,
        );
      } catch (err) {
        // A user-initiated cancel (Cancel-Upload) is not an error: close quietly.
        if (isAbortError(err)) {
          reset();
          return { outcome: 'error', source: null };
        }
        logger.error('Upload wizard upload failed:', err);
        setError('Upload failed: ' + getApiErrorMessage(err));
        setPhase('error');
        return { outcome: 'error', source: null };
      }

      // Duplicate skipped server-side → no wizard; caller surfaces the toast.
      if (result.skipped_duplicate) {
        reset();
        return { outcome: 'skipped', source: result };
      }

      // Specific domain → gate bypassed; nothing to review.
      if (!isAuto) {
        reset();
        return { outcome: 'fast-path', source: result };
      }

      // Auto domain → take over: begin the targeted Analyzing poll.
      analyzeStartedAt.current = Date.now();
      setSourceId(result.id);
      setPhase('analyzing');
      return { outcome: 'wizard', source: result };
    },
    [reset],
  );

  const confirm = useCallback(
    async (options: ConfirmExtractionOptions) => {
      if (!sourceId) return;
      setError(null);
      try {
        await confirmMutation.mutateAsync({ sourceId, options });
        reset();
      } catch (err) {
        logger.error('Upload wizard confirm failed:', err);
        setError('Confirm failed: ' + getApiErrorMessage(err));
        setPhase('error');
      }
    },
    [sourceId, confirmMutation, reset],
  );

  const cancel = useCallback(() => {
    reset();
  }, [reset]);

  return {
    phase,
    source: reviewSource,
    error,
    confirming: confirmMutation.isPending,
    start,
    confirm,
    cancel,
  };
}
