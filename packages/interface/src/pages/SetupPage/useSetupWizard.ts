// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useSetupWizard — state + navigation for the first-run setup flow.
 *
 * Step 0 (Account) creates the credential and flips the auth context.
 * Steps 1-3 then render the existing Settings-page tab components
 * (`LLMProviderTab`, `EmbeddingProviderConfig`) against a working
 * `Settings` draft seeded from the loaded context settings. Nothing is
 * PATCHed until the user clicks Finish on step 3.
 *
 * If the user closes the tab during steps 1-3, no settings change leaves
 * the browser. The wizard re-shows on next login because
 * `settings.setup_completed` is still false; AuthGuard routes them back
 * to /setup until completion.
 */

import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { settingsApi } from '../../services/api/settings';
import { SettingsContext } from '../../contexts/settingsContextValue';
import { getApiErrorMessage } from '../../utils/errors';
import { logger } from '../../utils/logger';
import { useAuth } from '../../contexts/useAuth';
import { LLM_HEALTH_KEY } from '../../hooks/useLLMHealth';
import { mergeWizardSecretsFromContext, stripWizardSecrets } from './wizardHelpers';
import type { Settings } from '../../types';

/** Wizard's recommended starter preset for Ollama. Picked to fit common
 *  consumer GPUs (RTX 4070 Ti / 4080 / 7900 XT class). Users with bigger
 *  or smaller cards switch via the dropdown. */
const DEFAULT_OLLAMA_PRESET_ID = 'vram_16gb';

/** Embedding default derived from the chat provider. Ollama-chat users
 *  get GPU-accelerated Qwen3 8B at its native 4096d — they already have
 *  a GPU available, so we lean on the higher-quality model. Cloud-chat
 *  users fall back to local CPU embeddings on Qwen3 0.6B at its native
 *  1024d (avoids hitting another paid API and stays light on CPU). Both
 *  support MRL, so the user can truncate to a smaller index later
 *  without re-embedding. */
function deriveEmbeddingDefault(
  chatProvider: string,
): { provider: 'ollama' | 'local'; model: string; dimensions: number } {
  if (chatProvider === 'ollama') {
    return { provider: 'ollama', model: 'qwen3-embedding:8b', dimensions: 4096 };
  }
  return { provider: 'local', model: 'Qwen/Qwen3-Embedding-0.6B', dimensions: 1024 };
}

export type WizardStep = 0 | 1 | 2;

interface UseSetupWizardReturn {
  step: WizardStep;
  /** Working draft seeded from context settings; null until settings load (steps 1-3). */
  working: Settings | null;
  setWorking: (settings: Settings) => void;
  submitting: boolean;
  error: string | null;
  /** Called by AccountStep after the credential is created and the cookie is live. */
  onAccountComplete: (network: { allow_external_access: boolean; allowed_hosts: string[] }) => void;
  /** Advance to the next step (or Finish if on step 3). */
  continueStep: () => Promise<void>;
  /** Go back one step (no-op on step 0 / step 1). */
  backStep: () => void;
}

const isFinalStep = (step: WizardStep): boolean => step === 2;

interface UseSetupWizardOptions {
  /** Starting step. SetupPage passes 1 when the user is already authenticated (resumed wizard). */
  initialStep?: WizardStep;
}

export function useSetupWizard({
  initialStep = 0,
}: UseSetupWizardOptions = {}): UseSetupWizardReturn {
  const navigate = useNavigate();

  const { user } = useAuth();
  const queryClient = useQueryClient();
  const sessionKey = user?.username ? `setup-wizard-state:${user.username}` : null;

  const settingsCtx = useContext(SettingsContext);
  const contextSettings = settingsCtx?.settings ?? null;
  // Stable identity for the noop fallback so the `finish` useCallback below
  // doesn't re-create on every render.
  const refreshSettings = useMemo(
    () => settingsCtx?.refreshSettings ?? (async () => {}),
    [settingsCtx?.refreshSettings],
  );

  // The persisted draft never contains API keys / passwords (stripped before
  // write — see the persist effect). We can't restore it synchronously here
  // because the secrets must be re-merged from context settings, which load
  // asynchronously. So stash the parsed draft and let the seed-from-context
  // effect below merge the secrets back in once context is available.
  const rehydratedDraftRef = useRef<Settings | null>(null);

  const [step, setStep] = useState<WizardStep>(initialStep);
  const [working, setWorking] = useState<Settings | null>(() => {
    if (!sessionKey) return null;
    try {
      const stored = sessionStorage.getItem(sessionKey);
      if (stored) {
        rehydratedDraftRef.current = JSON.parse(stored) as Settings;
      }
    } catch {
      // Corrupted storage — fall through to the default null.
    }
    return null;
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pendingNetworkRef = useRef<{ allow_external_access: boolean; allowed_hosts: string[] } | null>(null);

  // Once the user advances to (or past) step 2, they own the embedding
  // slice. Up to and including step 1, the wizard mirrors the chat
  // provider into the embedding defaults — Ollama-chat → Ollama-embed,
  // cloud-chat → local-CPU embed.
  const [embeddingFollowsChat, setEmbeddingFollowsChat] = useState(true);

  // Persist the working draft to sessionStorage whenever it changes so
  // a browser refresh restores the user's in-progress edits. API keys /
  // passwords are stripped first — they'd otherwise sit in cleartext in
  // browser storage until Finish; they're re-merged from context on
  // rehydrate (see the seed-from-context effect).
  useEffect(() => {
    if (!sessionKey || working === null) return;
    try {
      sessionStorage.setItem(sessionKey, JSON.stringify(stripWizardSecrets(working)));
    } catch {
      // Quota exceeded or private-browsing restrictions — silently skip.
    }
  }, [sessionKey, working]);

  // Seed the working draft from context settings the first time they
  // become available. Subsequent context updates (e.g. refresh after
  // PATCH) don't blow away in-progress edits — the seed only runs while
  // working is still null.
  useEffect(() => {
    if (contextSettings && working === null) {
      // A rehydrated draft (browser refresh) takes precedence over a fresh
      // seed — restore the user's edits, re-merging the API keys / passwords
      // that were stripped before persisting from context settings.
      const rehydrated = rehydratedDraftRef.current;
      if (rehydrated) {
        setWorking(mergeWizardSecretsFromContext(rehydrated, contextSettings));
        rehydratedDraftRef.current = null;
        return;
      }
      const seed = pendingNetworkRef.current
        ? { ...contextSettings, security: { ...pendingNetworkRef.current } }
        : contextSettings;
      setWorking(seed);
      pendingNetworkRef.current = null;
    }
  }, [contextSettings, working]);

  // While the user is on / before step 1, keep the embedding slice in
  // sync with the chat provider. The check is idempotent — if embedding
  // already matches the derived default we no-op — so this useEffect
  // doesn't churn on unrelated edits to other fields.
  useEffect(() => {
    if (!working || !embeddingFollowsChat) return;
    const desired = deriveEmbeddingDefault(working.llm.chat_provider);
    if (
      working.embedding.provider === desired.provider &&
      working.embedding.model === desired.model &&
      working.search.vector_dimensions === desired.dimensions
    ) {
      return;
    }
    setWorking({
      ...working,
      embedding: { ...working.embedding, provider: desired.provider, model: desired.model },
      search: { ...working.search, vector_dimensions: desired.dimensions },
    });
  }, [working, embeddingFollowsChat]);

  // Pre-fill cloud providers' chat / extraction / vision model fields with
  // each provider's `recommended` model on first mount. Without this, the
  // backend's stale Pydantic defaults (e.g. `gpt-4.1`) show up in the
  // dropdowns and the user has to manually pick the latest model every
  // time they switch providers. Stamps for OpenAI, Anthropic, AND Gemini —
  // not just the active provider — so a switch on step 1 lands on a
  // pre-populated form regardless of which provider the user picks.
  const cloudModelsSeeded = useRef(false);
  useEffect(() => {
    if (cloudModelsSeeded.current) return;
    if (!working) return;
    cloudModelsSeeded.current = true;

    settingsApi
      .getCloudModels()
      .then((res) => {
        const providers = res?.providers ?? {};
        setWorking((prev) => {
          if (!prev) return prev;
          const next = { ...prev.llm };

          for (const provider of ['openai', 'anthropic', 'gemini'] as const) {
            const list = providers[provider]?.models ?? [];
            const rec = list.find((m) => m.recommended) ?? list[0];
            if (!rec) continue;
            const visionFallback = rec.supports_vision
              ? rec.id
              : (list.find((m) => m.supports_vision)?.id ?? rec.id);

            if (provider === 'openai') {
              next.openai_chat_model = rec.id;
              next.openai_extraction_model = rec.id;
              next.openai_vision_model = visionFallback;
            } else if (provider === 'anthropic') {
              next.anthropic_chat_model = rec.id;
              next.anthropic_extraction_model = rec.id;
              next.anthropic_vision_model = visionFallback;
            } else if (provider === 'gemini') {
              next.gemini_chat_model = rec.id;
              next.gemini_extraction_model = rec.id;
              next.gemini_vision_model = visionFallback;
            }
          }

          return { ...prev, llm: next };
        });
      })
      .catch((err) => {
        logger.warn('Failed to seed cloud chat-model defaults; backend defaults will show.', err);
      });
  }, [working]);

  // Pre-apply the 16GB VRAM preset on first wizard mount when the user
  // lands on Ollama with no preset selected. Mirrors what
  // `useProviderSettings.handleApplyPreset` does — copies the preset's
  // model + context recommendations into the working draft so the chat /
  // extraction model fields are filled out for the user. Runs once;
  // subsequent edits (including switching to a different preset) are
  // preserved.
  const presetSeeded = useRef(false);
  useEffect(() => {
    if (presetSeeded.current) return;
    if (!working) return;
    if (working.llm.chat_provider !== 'ollama') return;
    if (working.llm.ollama_quick_preset) return;

    presetSeeded.current = true;
    settingsApi
      .listPresets()
      .then((res) => {
        const presets = res?.presets ?? [];
        const preset = presets.find((p) => p.name === DEFAULT_OLLAMA_PRESET_ID);
        if (!preset) return;
        setWorking((prev) => {
          if (!prev) return prev;
          // Bail if the user has since switched provider or already picked a preset.
          if (prev.llm.chat_provider !== 'ollama' || prev.llm.ollama_quick_preset) {
            return prev;
          }
          return {
            ...prev,
            llm: {
              ...prev.llm,
              ollama_quick_preset: preset.name,
              ollama_chat_model:
                preset.ollama_settings.ollama_chat_model || prev.llm.ollama_chat_model,
              ollama_extraction_model:
                preset.ollama_settings.ollama_extraction_model ?? prev.llm.ollama_extraction_model,
              ollama_vision_model:
                preset.ollama_settings.ollama_vision_model ?? prev.llm.ollama_vision_model,
              ollama_num_ctx: preset.ollama_settings.ollama_num_ctx ?? prev.llm.ollama_num_ctx,
              ollama_num_batch:
                preset.ollama_settings.ollama_num_batch ?? prev.llm.ollama_num_batch,
              ai_context_window:
                preset.llm_settings.ai_context_window ?? prev.llm.ai_context_window,
              ai_max_tokens: preset.llm_settings.ai_max_tokens ?? prev.llm.ai_max_tokens,
              extraction_max_tokens:
                preset.llm_settings.extraction_max_tokens ?? prev.llm.extraction_max_tokens,
            },
          };
        });
      })
      .catch((err) => {
        logger.warn('Failed to seed VRAM preset default; user can pick manually.', err);
      });
  }, [working]);

  const onAccountComplete = useCallback(
    (network: { allow_external_access: boolean; allowed_hosts: string[] }) => {
      // Seed the working draft's security slice so it's persisted along
      // with the rest of the wizard payload on Finish (step 2).
      setWorking((prev) => {
        if (!prev) {
          // Working hasn't loaded yet — stash the network selection so the
          // seed-from-context effect can merge it in on its first run.
          pendingNetworkRef.current = network;
          return prev;
        }
        return { ...prev, security: { ...network } };
      });
      setStep(1);
    },
    [],
  );

  /** PATCH the full working settings + setup_completed=true, then dashboard. */
  const finish = useCallback(
    async (final: Settings): Promise<void> => {
      setSubmitting(true);
      setError(null);
      try {
        const payload: Settings = { ...final, setup_completed: true };
        await settingsApi.update(payload);
        await refreshSettings();
        // Invalidate the LLM health cache so the banner re-evaluates
        // against the just-saved Ollama URL / API key without waiting
        // for the useLLMHealth refetch interval.
        await queryClient.invalidateQueries({ queryKey: LLM_HEALTH_KEY });
        if (sessionKey) {
          try { sessionStorage.removeItem(sessionKey); } catch { /* ignore */ }
        }
        navigate('/', { replace: true });
      } catch (err) {
        setError(getApiErrorMessage(err) || 'Failed to save settings. Please try again.');
      } finally {
        setSubmitting(false);
      }
    },
    [refreshSettings, navigate, sessionKey, queryClient],
  );

  const continueStep = useCallback(async () => {
    if (isFinalStep(step)) {
      if (!working) return;
      await finish(working);
      return;
    }
    // Advancing past step 1 hands the embedding slice over to the user —
    // any further chat-provider edits (e.g. on a Back-and-edit pass) won't
    // clobber whatever they pick on step 2.
    if (step === 1) {
      setEmbeddingFollowsChat(false);
    }
    setStep((s) => Math.min(2, s + 1) as WizardStep);
  }, [step, working, finish]);

  const backStep = useCallback(() => {
    // Step 0 (Account) is one-shot — once submitted, the cookie is set and
    // we can't undo it. Step 1 is the floor for Back navigation.
    setStep((s) => (s > 1 ? ((s - 1) as WizardStep) : s));
  }, []);

  return {
    step,
    working,
    setWorking,
    submitting,
    error,
    onAccountComplete,
    continueStep,
    backStep,
  };
}
