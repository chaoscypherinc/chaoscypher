// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { lazy, Suspense, useCallback, useContext, useEffect, useState, useMemo } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation, Outlet } from 'react-router';
import { ThemeProvider, createTheme, CssBaseline, alpha } from '@mui/material';
import { QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { ChaosCypherPalette, ChaosCypherBackground, ChaosCypherNeutrals } from './theme/palette';
import { getComponentOverrides } from './theme/componentOverrides';
import Layout from './components/Layout';
import { ConnectionErrorScreen } from './components/ConnectionErrorScreen';
import { ErrorBoundary } from './components/ErrorBoundary';
import { LoadingState } from './components/LoadingState';
import { DashboardProvider } from './contexts/DashboardContext';
import { NotificationProvider } from './contexts/NotificationContext';
import { SettingsProvider } from './contexts/SettingsContext';
import { SettingsContext } from './contexts/settingsContextValue';
import { AuthProvider } from './contexts/AuthContext';
import { PublicSettingsProvider } from './contexts/PublicSettingsContext';
import { useAuth } from './contexts/useAuth';
import { settingsApi } from './services/api/settings';
import { fetchPendingUpgrades } from './services/api/upgrade';
import { queryClient } from './services/queryClient';
import type { Settings } from './types';
import { logger } from './utils/logger';

// ---------------------------------------------------------------------------
// Lazy-loaded page components (code-split at the route level)
// ---------------------------------------------------------------------------
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const NodesPage = lazy(() => import('./pages/NodesPage'));
const NodeDetailPage = lazy(() => import('./pages/NodeDetailPage'));
const EdgesPage = lazy(() => import('./pages/EdgesPage'));
const EdgeDetailPage = lazy(() => import('./pages/EdgeDetailPage'));
const SourcesPage = lazy(() => import('./pages/Sources'));
const SourcePage = lazy(() => import('./pages/SourcePage'));
const TemplatesPage = lazy(() => import('./pages/TemplatesPage'));
const TemplateDetailPage = lazy(() => import('./pages/TemplateDetailPage'));
const LexiconPage = lazy(() => import('./pages/LexiconPage'));
const WorkflowSystemPage = lazy(() => import('./pages/WorkflowSystemPage'));
const WorkflowBuilderPage = lazy(() => import('./pages/WorkflowBuilderPage/WorkflowBuilderPage'));
const WorkflowExecutionHistoryPage = lazy(() => import('./pages/WorkflowExecutionHistoryPage'));
const GraphCanvasPage = lazy(() => import('./pages/GraphCanvasPage/GraphCanvasPage'));
const ChatPage = lazy(() => import('./pages/ChatPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const QueueMonitorPage = lazy(() => import('./pages/QueueMonitorPage'));
const SetupPage = lazy(() => import('./pages/SetupPage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const MaintenancePage = lazy(() =>
  import('./pages/MaintenancePage').then((m) => ({ default: m.MaintenancePage }))
);

// ---------------------------------------------------------------------------
// Auth guard: redirects to /setup or /login when needed
// ---------------------------------------------------------------------------

function AuthGuard() {
  const { needsSetup, isAuthenticated, loading } = useAuth();
  // Read the raw context — `useSettings()` throws when settings is null and
  // settings is null on /login and /setup until the user authenticates.
  const settingsCtx = useContext(SettingsContext);
  const settings = settingsCtx?.settings ?? null;
  const location = useLocation();

  // AuthGuard only renders *inside* the Router — and the Router only
  // mounts once `AppContent.loading === false`, which means by the
  // time we get here the initial boot fullPage loader (rendered by
  // AppContent) has already painted. Subsequent transient gate flips
  // (login → settings load, in-flight settings refresh) should not
  // unmount the layout and flash a full-viewport splash over the
  // user's in-app navigation. We use the non-fullPage `LoadingState`
  // variant for both gates so the layout stays put while the gate
  // resolves.
  if (loading) {
    return <LoadingState message="Checking authentication..." />;
  }

  // /maintenance is the one route that runs *without* a loaded settings
  // payload, by design: the page is reached precisely because the upgrade
  // gate is 503'ing /api/v1/settings, and AppContent deliberately skips the
  // settings fetch on this path. Short-circuit here so that:
  //   1. The "still loading settings" gate below doesn't trap the user on
  //      a permanent LoadingState, and
  //   2. The setup_completed routing below doesn't read `setupComplete`
  //      from a null `settings` (defaulting to false → bouncing the user
  //      to /setup, where the settings gate then traps them).
  // Auth is already verified above (the `loading` early-return covers the
  // unresolved-auth case), and the !isAuthenticated branch further down
  // still runs for unauthenticated visitors hitting /maintenance directly.
  if (isAuthenticated && location.pathname === '/maintenance') {
    return <Outlet />;
  }

  // While authenticated but settings haven't loaded yet, we can't decide
  // setup_completed routing — wait. (AppContent already shows a loader at
  // boot for this, but not for state transitions like login → settings load.)
  if (isAuthenticated && !settings) {
    return <LoadingState message="Loading..." />;
  }

  const isPublicPage =
    location.pathname === '/setup' || location.pathname === '/login';
  const onSetup = location.pathname === '/setup';
  const setupComplete = settings?.setup_completed ?? false;

  // /setup is reachable when:
  //   - First-run setup is required (no account yet), OR
  //   - User is authenticated but the wizard isn't completed yet.
  const canAccessSetup = needsSetup || (isAuthenticated && !setupComplete);

  if (onSetup && !canAccessSetup) {
    return <Navigate to={isAuthenticated ? '/' : '/login'} replace />;
  }

  // Authenticated user with an incomplete wizard who tried to go elsewhere —
  // route them back to the wizard until they finish.
  if (!onSetup && canAccessSetup && isAuthenticated) {
    return <Navigate to="/setup" replace />;
  }

  // First-run setup required and they're not on /setup — send them there.
  if (!onSetup && needsSetup) {
    return <Navigate to="/setup" replace />;
  }

  // Unauthenticated user on a non-public page — send to /login with a `next`
  // so we can bounce them back after they sign in.
  if (!isAuthenticated && !isPublicPage) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }

  // Authenticated user on /login — send to dashboard
  if (isAuthenticated && location.pathname === '/login') {
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
}

// ---------------------------------------------------------------------------
// Layout wrapper for main app routes
// ---------------------------------------------------------------------------

function LayoutWrapper() {
  // DashboardProvider runs the single shared /system/dashboard polling
  // loop that MiniSystemStatus, the system pause banner, and other
  // live-status consumers read from. Mounted here (inside the layout)
  // so it only polls while a logged-in user is on a real app page —
  // the login/setup screens stay quiet.
  return (
    <DashboardProvider>
      <Layout>
        <ErrorBoundary>
          <Suspense fallback={<LoadingState message="Loading page..." fullPage />}>
            <Outlet />
          </Suspense>
        </ErrorBoundary>
      </Layout>
    </DashboardProvider>
  );
}

// ---------------------------------------------------------------------------
// Inner app (with settings + routing)
// ---------------------------------------------------------------------------

function AppContent() {
  // `/api/v1/settings` is auth-gated, so we can only fetch it once the user
  // has a session. Bootstrap order matters: on a fresh install the auth
  // status returns `setup_needed: true`, and we must let the Router mount
  // (with no settings) so AuthGuard can redirect to /setup or /login.
  const { isAuthenticated, loading: authLoading } = useAuth();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await settingsApi.get();
      setSettings(data);
    } catch (err) {
      logger.error('Failed to load settings:', err);
      setError('Failed to connect to backend. Please ensure the Cortex server is running.');
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Re-fetch settings from the API and update context without a full page reload.
   * Called by consumers (e.g., SettingsPage) after a successful save or reset.
   */
  const refreshSettings = useCallback(async () => {
    try {
      const data = await settingsApi.get();
      setSettings(data);
    } catch (err) {
      logger.error('Failed to refresh settings:', err);
    }
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (!isAuthenticated) {
      // Skip the settings fetch — it would 401 and trap us on Connection
      // Error. The Router will mount and AuthGuard will route the user to
      // /setup or /login. Settings will load below once `isAuthenticated`
      // flips after a successful login.
      setSettings(null);
      setError(null);
      setLoading(false);
      return;
    }
    if (window.location.pathname === '/maintenance') {
      // During a blocked upgrade, /api/v1/settings returns 503 — surfacing
      // that as "Connection Error" short-circuits the Router and hides the
      // maintenance page. Skip the fetch; it will run again after apply.
      setSettings(null);
      setError(null);
      setLoading(false);
      return;
    }
    loadSettings();
  }, [authLoading, isAuthenticated, loadSettings]);

  // Upgrade-state check: if the DB is blocked on a tier-2 migration,
  // bounce to /maintenance before loading anything that assumes the
  // schema is ready. The upgrade endpoint is whitelisted by the Cortex
  // upgrade-gate middleware so this call works even when the rest of
  // /api/* is returning 503.
  useEffect(() => {
    if (authLoading || !isAuthenticated) return;
    if (window.location.pathname === '/maintenance') return;
    let cancelled = false;
    fetchPendingUpgrades()
      .then((body) => {
        if (cancelled) return;
        if (body.ready === false) {
          window.location.replace('/maintenance');
        }
      })
      .catch(() => {
        // Network error or endpoint missing (old backend) — no-op.
      });
    return () => {
      cancelled = true;
    };
  }, [authLoading, isAuthenticated]);

  // Chaos Cypher is dark-first: when settings haven't loaded yet (initial
  // boot, maintenance redirect, connection error) default to dark so the
  // splash/loading/error screens don't flash light against a dark product.
  // Once settings loads, the user's saved `dark_mode` wins.
  const darkMode = settings?.dark_mode ?? true;
  const theme = useMemo(
    () =>
      createTheme({
        palette: {
          mode: darkMode ? 'dark' : 'light',
          primary: {
            main: ChaosCypherPalette.primary,
          },
          secondary: {
            main: ChaosCypherPalette.secondary,
          },
          error: {
            main: ChaosCypherPalette.error,
          },
          warning: {
            main: ChaosCypherPalette.warning,
          },
          info: {
            main: ChaosCypherPalette.info,
          },
          success: {
            main: ChaosCypherPalette.success,
          },
          background: darkMode
            ? ChaosCypherBackground.dark
            : ChaosCypherBackground.light,
          text: {
            primary:   ChaosCypherNeutrals.textPrimary,
            secondary: ChaosCypherNeutrals.textSecondary,
            disabled:  ChaosCypherNeutrals.textTertiary,
          },
          divider: alpha(ChaosCypherNeutrals.borderDivider, 0.4),
        },
        components: getComponentOverrides(),
      }),
    [darkMode]
  );

  if (loading) {
    return (
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <LoadingState message="Loading..." fullPage />
      </ThemeProvider>
    );
  }

  if (error) {
    return (
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <ConnectionErrorScreen error={error} onRetry={loadSettings} />
      </ThemeProvider>
    );
  }

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <NotificationProvider>
        <SettingsProvider settings={settings} refreshSettings={refreshSettings}>
          <Router>
            <Suspense fallback={<LoadingState message="Loading page..." fullPage />}>
              <Routes>
                {/* All routes go through auth guard */}
                <Route element={<AuthGuard />}>
                  {/* Public auth pages (no Layout) */}
                  <Route path="/setup" element={<SetupPage />} />
                  <Route path="/login" element={<LoginPage />} />
                  <Route path="/maintenance" element={<MaintenancePage />} />

                  {/* Main app pages (with Layout) */}
                  <Route element={<LayoutWrapper />}>
                    <Route path="/" element={<DashboardPage />} />
                    <Route path="/nodes" element={<NodesPage />} />
                    <Route path="/nodes/:nodeId" element={<NodeDetailPage />} />
                    <Route path="/edges" element={<EdgesPage />} />
                    <Route path="/edges/:edgeId" element={<EdgeDetailPage />} />
                    <Route path="/sources" element={<SourcesPage />} />
                    <Route path="/sources/:id" element={<SourcePage />} />
                    <Route path="/templates" element={<TemplatesPage />} />
                    <Route path="/templates/:templateId" element={<TemplateDetailPage />} />
                    <Route path="/lexicon" element={<LexiconPage />} />
                    <Route path="/automations" element={<WorkflowSystemPage />} />
                    <Route path="/automations/builder" element={<WorkflowBuilderPage />} />
                    <Route path="/automations/builder/:workflowId" element={<WorkflowBuilderPage />} />
                    <Route path="/automations/:workflowId/history" element={<WorkflowExecutionHistoryPage />} />
                    <Route path="/graph" element={<GraphCanvasPage />} />
                    <Route path="/chat" element={<ChatPage />} />
                    <Route path="/chat/:chatId" element={<ChatPage />} />
                    <Route path="/settings" element={<SettingsPage />} />
                    <Route path="/queues" element={<QueueMonitorPage />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Route>
                </Route>
              </Routes>
            </Suspense>
          </Router>
        </SettingsProvider>
      </NotificationProvider>
    </ThemeProvider>
  );
}

// ---------------------------------------------------------------------------
// Root App (wraps everything with AuthProvider)
// ---------------------------------------------------------------------------

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <PublicSettingsProvider>
        {/* Top-level boundary so a render crash on the public auth/maintenance
            pages (or in AppContent/AuthGuard) shows the reload fallback instead
            of a white screen. The inner LayoutWrapper boundary still gives
            finer-grained recovery for in-app routes. */}
        <ErrorBoundary>
          <AuthProvider>
            <AppContent />
          </AuthProvider>
        </ErrorBoundary>
        {import.meta.env.DEV && <ReactQueryDevtools buttonPosition="bottom-right" />}
      </PublicSettingsProvider>
    </QueryClientProvider>
  );
}

export default App;
