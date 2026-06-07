// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SetupPage — first-run wizard.
 *
 * Renders the existing Settings-page tab components inside a stepper so
 * the wizard and Settings page share the same JSX. There is exactly one
 * place each piece of configuration UI lives — change it once, both
 * surfaces pick it up.
 *
 * Steps:
 *   0. Account     — `AccountStep` (creates credential + flips auth context)
 *   1. LLM         — `LLMProviderTab` rendered with `hideAdvancedToggle` so
 *                    new users aren't faced with multi-instance / VRAM /
 *                    thinking-mode controls before they've even picked a
 *                    model. Also includes the tool-approval Select at the
 *                    bottom — small enough to fold in here rather than make
 *                    its own step.
 *   2. Embeddings  — `EmbeddingProviderConfig` (provider, model, dimensions, key)
 *
 * The wizard is gated server-side by `settings.setup_completed`; AuthGuard
 * keeps an authenticated user on /setup until that flag flips true at
 * Finish.
 */

import { useState } from 'react';
import {
  Box,
  Typography,
  Paper,
  Stepper,
  Step,
  StepLabel,
  Button,
  Alert,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
} from '@mui/material';
import { useAuth } from './../contexts/useAuth';
import { ghostButtonSx } from './../theme/ghostStyles';
import { ChaosCypherPalette } from './../theme/palette';
import { settingsApi } from './../services/api/settings';
import type { LLMHealthResponse } from './../types';
import AccountStep from './SetupPage/AccountStep';
import LLMProviderTab from './settings/LLMProviderTab';
import EmbeddingProviderConfig from './settings/components/EmbeddingProviderConfig';
import { useSetupWizard, type WizardStep } from './SetupPage/useSetupWizard';
import { embeddingStepHasMinimumInput } from './SetupPage/wizardHelpers';

const STEP_LABELS = ['Account', 'LLM Provider', 'Embeddings'];

export default function SetupPage() {
  const { isAuthenticated } = useAuth();

  // Authenticated users who land on /setup (e.g. they bailed on the wizard
  // last session and AuthGuard routed them back) start at step 1 — the
  // credential already exists.
  const [initialStep] = useState<WizardStep>(() => (isAuthenticated ? 1 : 0));

  const wizard = useSetupWizard({ initialStep });
  const { step, working, setWorking, submitting, error, onAccountComplete, continueStep, backStep } =
    wizard;

  const renderStepBody = () => {
    if (step === 0) return <AccountStep onComplete={onAccountComplete} />;
    if (!working) {
      return (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress />
        </Box>
      );
    }
    if (step === 1) {
      return (
        <LLMProviderTab
          settings={working}
          setSettings={setWorking}
          hideAdvancedToggle
        />
      );
    }
    if (step === 2) return <EmbeddingProviderConfig settings={working} setSettings={setWorking} />;
    return null;
  };

  // The LLM step is NEVER blocked at the wizard — users may click
  // Continue with an empty or unverified provider. The action-gating
  // model handles the consequences server-side: import + chat refuse
  // until the LLM is verified, and a persistent banner reminds the
  // user. On click we surface a soft warning + Skip button to make
  // the trade-off explicit at the wizard moment, then advance.
  const continueDisabled =
    submitting ||
    !working ||
    (step === 2 && !embeddingStepHasMinimumInput(working));

  const continueLabel = step === 2 ? 'Finish' : 'Continue';

  const [verifyWarning, setVerifyWarning] = useState<LLMHealthResponse | null>(null);

  const handleContinueClick = async () => {
    if (step === 1 && working) {
      try {
        const health = await settingsApi.getLLMHealth();
        if (!health.verified) {
          setVerifyWarning(health);
          return;
        }
      } catch {
        // Health check failure shouldn't block the wizard — the
        // server-side action gates still enforce the real contract.
      }
    }
    await continueStep();
  };

  const handleSkipAnyway = async () => {
    setVerifyWarning(null);
    await continueStep();
  };

  return (
    <Box
      sx={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'flex-start',
        minHeight: '100vh',
        bgcolor: 'background.default',
        py: { xs: 3, md: 6 },
        px: { xs: 2, md: 3 },
      }}
    >
      <Paper sx={{ maxWidth: 900, width: '100%', p: { xs: 2, md: 4 } }} elevation={3}>
        <Box sx={{ textAlign: 'center', mb: 3 }}>
          <Box
            component="img"
            src="/logo.png"
            alt="Chaos Cypher"
            sx={{ width: 80, height: 80, mb: 2 }}
          />
          <Typography variant="h4" gutterBottom>
            Welcome to Chaos Cypher
          </Typography>
          <Typography sx={{ color: 'text.secondary', maxWidth: 640, mx: 'auto' }}>
            {step === 0
              ? 'Create an admin account to get started.'
              : 'Chaos Cypher turns your documents into a queryable knowledge graph powered by local or hosted LLMs. The next two steps pick the chat model that will extract entities and relationships, then the embedding model that powers semantic search.'}
          </Typography>
        </Box>

        <Stepper activeStep={step} alternativeLabel sx={{ mb: 4 }}>
          {STEP_LABELS.map((label) => (
            <Step key={label}>
              <StepLabel>{label}</StepLabel>
            </Step>
          ))}
        </Stepper>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {renderStepBody()}

        {step > 0 && (
          <Box
            sx={{
              display: 'flex',
              justifyContent: 'space-between',
              gap: 2,
              mt: 4,
            }}
          >
            <Button variant="text" onClick={backStep} disabled={submitting || step === 1}>
              Back
            </Button>
            <Button
              variant="outlined"
              onClick={handleContinueClick}
              disabled={continueDisabled}
              sx={ghostButtonSx(ChaosCypherPalette.primary)}
            >
              {submitting ? <CircularProgress size={20} color="inherit" /> : continueLabel}
            </Button>
          </Box>
        )}
      </Paper>
      <Dialog
        open={verifyWarning !== null}
        onClose={() => setVerifyWarning(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>LLM not verified</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Chaos Cypher requires an LLM to extract knowledge graphs.{' '}
            {verifyWarning && !verifyWarning.configured
              ? `You haven't entered the credentials for ${verifyWarning.provider} yet.`
              : `Your ${verifyWarning?.provider} connection hasn't been tested successfully.`}
            {' '}You can continue setup, but importing sources and chat will be blocked
            until the LLM is configured and working. You can fix this now or anytime
            later from Settings.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setVerifyWarning(null)} variant="outlined">
            Fix it now
          </Button>
          <Button onClick={handleSkipAnyway} variant="contained" color="warning">
            Skip anyway
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
