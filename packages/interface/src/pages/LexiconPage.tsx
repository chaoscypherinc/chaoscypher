// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Alert,
  Snackbar,
  ToggleButton,
  ToggleButtonGroup,
  Button,
} from '@mui/material';
import { LoadingState } from '../components/LoadingState';
import ViewModule from '@mui/icons-material/ViewModule';
import ViewList from '@mui/icons-material/ViewList';
import CloudOffOutlined from '@mui/icons-material/CloudOffOutlined';
import { useLexiconAuth } from '../hooks/useLexiconAuth';
import { isApiError } from '../services/api/client';
import {
  LexiconAuthStatus,
  DeviceAuthDialog,
  PackageFilters,
  PackageCard,
  PackageTable,
} from '../components/lexicon';
import {
  usePopularPackages,
  useSearchPackages,
  useImportPackage,
} from '../services/api/useLexicon';
import type {
  LexiconPackageInfo,
  ViewMode,
  SortOption,
} from '../types/lexicon';
import GhostPagination from '../components/GhostPagination';
import { ghostErrorAlertSx } from '../theme/ghostStyles';
import { getApiErrorMessage } from '../utils/errors';

const ITEMS_PER_PAGE = 50;
const POPULAR_PACKAGES_LIMIT = 10;

/** A 503 from any lexicon endpoint means the optional registry service isn't reachable. */
function is503(err: unknown): boolean {
  return isApiError(err) && err.status === 503;
}

export default function LexiconPage() {
  // Auth state — the device-auth login/poll flow stays imperative (timer
  // chain that doesn't fit a query); only the data reads below are queries.
  const {
    authStatus,
    loading: authLoading,
    deviceCode,
    startDeviceAuth,
    logout,
    cancelAuth,
  } = useLexiconAuth();

  // Search state
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [sortBy, setSortBy] = useState<SortOption>('downloads');
  const [page, setPage] = useState(1);
  const [viewMode, setViewMode] = useState<ViewMode>('cards');

  // Import + feedback state
  const [importingId, setImportingId] = useState<string | null>(null);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error';
  }>({ open: false, message: '', severity: 'success' });

  const importPackage = useImportPackage();

  // Debounce the search query (300ms) and reset to page 1 when it changes,
  // matching the old consolidated debounce/immediate effect. Page and sort
  // feed the query directly (no debounce) so paging/sorting fire immediately.
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(query);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  // Popular packages (shown when there's no active search query). The Retry
  // button on the unavailable panel re-runs this via refetch.
  const popular = usePopularPackages({ limit: POPULAR_PACKAGES_LIMIT });
  const popularPackages = popular.data ?? null;

  // Package search. Disabled (resolves to no results) while the query is empty.
  const search = useSearchPackages({
    query: debouncedQuery,
    page,
    sortBy,
    limit: ITEMS_PER_PAGE,
  });
  const trimmedQuery = debouncedQuery.trim();
  const searchResults = trimmedQuery ? search.data ?? null : null;

  // Lexicon registry unreachable when any read returns 503. A subsequent 200
  // from either query flips it back (TanStack replaces error state on success).
  const serviceUnavailable = is503(popular.error) || is503(search.error);

  // Surface a non-503 search failure inline, mirroring the old `error` slot.
  const error =
    trimmedQuery && search.isError && !is503(search.error)
      ? getApiErrorMessage(search.error) || 'Failed to search packages'
      : null;

  const loading = !!trimmedQuery && search.isLoading;
  const loadingPopular = popular.isLoading || popular.isFetching;

  // Helper to create package key for tracking import state
  const getPackageKey = (ownerUsername: string, repoName: string) => `${ownerUsername}/${repoName}`;

  // Import package
  const handleImport = async (ownerUsername: string, repoName: string) => {
    const pkgKey = getPackageKey(ownerUsername, repoName);
    setImportingId(pkgKey);

    try {
      const result = await importPackage.mutateAsync({ ownerUsername, repoName });
      setSnackbar({
        open: true,
        message: result.message || `Import of ${pkgKey} queued. Check Queue Monitor for status.`,
        severity: 'success',
      });
    } catch (err) {
      setSnackbar({
        open: true,
        message: getApiErrorMessage(err) || 'Failed to import package',
        severity: 'error',
      });
    } finally {
      setImportingId(null);
    }
  };

  const handleViewModeChange = (_: React.MouseEvent<HTMLElement>, newMode: ViewMode | null) => {
    if (newMode) setViewMode(newMode);
  };

  const totalPages = searchResults ? Math.ceil(searchResults.total / ITEMS_PER_PAGE) : 0;

  // Lexicon registry isn't reachable. Replace filters/search/login/results
  // with a single panel — there's nothing useful the user can do here
  // until the operator deploys/restores the lexicon service. Keep the
  // page header so the user knows where they landed and can navigate
  // away via the sidebar.
  if (serviceUnavailable) {
    return (
      <Box>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
          <Typography variant="h4" component="h1">
            Lexicon
          </Typography>
        </Box>
        <Box
          data-testid="lexicon-unavailable"
          sx={{
            textAlign: 'center',
            py: 10,
            px: 4,
            maxWidth: 560,
            mx: 'auto',
            border: '1px solid rgba(255, 255, 255, 0.08)',
            borderRadius: 1,
            bgcolor: 'rgba(255, 255, 255, 0.02)',
          }}
        >
          <CloudOffOutlined
            sx={{ fontSize: 56, color: 'text.disabled', mb: 2 }}
            aria-hidden="true"
          />
          <Typography variant="h6" gutterBottom>
            Lexicon service unavailable
          </Typography>
          <Typography variant="body2" sx={{ color: 'text.secondary', mb: 3 }}>
            The Lexicon package registry isn&apos;t reachable from this
            Chaos Cypher instance. Browsing, searching, and signing in
            are disabled until the service is back up. Everything else
            in Chaos Cypher continues to work normally — only this page
            depends on the registry.
          </Typography>
          <Button
            variant="outlined"
            onClick={() => {
              void popular.refetch();
            }}
            disabled={loadingPopular}
          >
            {loadingPopular ? 'Retrying…' : 'Retry'}
          </Button>
        </Box>
      </Box>
    );
  }

  return (
    <Box>
      {/* Header */}
      <Box
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 2,
          justifyContent: 'space-between',
          alignItems: { xs: 'flex-start', sm: 'center' },
          mb: 3,
        }}
      >
        <Typography variant="h4" component="h1">
          Lexicon
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <ToggleButtonGroup
            value={viewMode}
            exclusive
            onChange={handleViewModeChange}
            size="small"
            sx={{
              '& .MuiToggleButton-root': {
                borderColor: 'rgba(255, 255, 255, 0.08)',
                color: 'text.disabled',
                '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' },
                '&.Mui-selected': {
                  color: 'primary.main',
                  bgcolor: 'rgba(0, 229, 255, 0.08)',
                  borderColor: 'rgba(0, 229, 255, 0.25)',
                  '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.12)' },
                },
              },
            }}
          >
            <ToggleButton value="cards">
              <ViewModule />
            </ToggleButton>
            <ToggleButton value="table">
              <ViewList />
            </ToggleButton>
          </ToggleButtonGroup>
          <LexiconAuthStatus
            authStatus={authStatus}
            loading={authLoading}
            onLogin={startDeviceAuth}
            onLogout={logout}
          />
        </Box>
      </Box>
      {/* Description */}
      <Typography
        variant="body1"
        sx={{
          color: "text.secondary",
          marginBottom: "16px"
        }}>
        Browse and import knowledge packages from the Chaos Cypher Lexicon package registry.
      </Typography>
      {/* Filters */}
      <PackageFilters
        query={query}
        sortBy={sortBy}
        onQueryChange={setQuery}
        onSortChange={setSortBy}
      />
      {/* Error display */}
      {error && (
        <Alert
          severity="error"
          sx={{ mb: 2, ...ghostErrorAlertSx }}
        >
          {error}
        </Alert>
      )}
      {/* Loading state */}
      {loading && (
        <LoadingState message="Searching packages..." minHeight="200px" />
      )}
      {/* Popular packages (shown when no search query) */}
      {!loading && !searchResults && query.trim() === '' && (
        <>
          {loadingPopular ? (
            <LoadingState message="Loading popular packages..." minHeight="200px" />
          ) : popularPackages?.packages?.length ? (
            <>
              <Typography variant="h6" gutterBottom sx={{ mt: 2 }}>
                Popular Packages
              </Typography>
              <Typography
                variant="body2"
                sx={{
                  color: "text.secondary",
                  mb: 2
                }}>
                Top {popularPackages.packages.length} most downloaded packages
              </Typography>
              {viewMode === 'cards' ? (
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
                  {popularPackages.packages.map((pkg: LexiconPackageInfo) => (
                    <Box key={pkg.id} sx={{ flex: '1 1 calc(25% - 12px)', minWidth: 250 }}>
                      <PackageCard
                        package_info={pkg}
                        importing={importingId === getPackageKey(pkg.owner_username, pkg.name)}
                        onImport={() => handleImport(pkg.owner_username, pkg.name)}
                      />
                    </Box>
                  ))}
                </Box>
              ) : (
                <PackageTable
                  packages={popularPackages.packages}
                  importingId={importingId}
                  onImport={handleImport}
                />
              )}
            </>
          ) : (
            <Box sx={{ textAlign: 'center', py: 8 }}>
              <Typography variant="h6" gutterBottom sx={{
                color: "text.secondary"
              }}>
                Search the Lexicon
              </Typography>
              <Typography variant="body2" sx={{
                color: "text.secondary"
              }}>
                Enter a search term above to find knowledge packages
              </Typography>
            </Box>
          )}
        </>
      )}
      {/* No results */}
      {!loading && searchResults && searchResults.packages.length === 0 && (
        <Box sx={{ textAlign: 'center', py: 8 }}>
          <Typography variant="h6" gutterBottom sx={{
            color: "text.secondary"
          }}>
            No packages found
          </Typography>
          <Typography variant="body2" sx={{
            color: "text.secondary"
          }}>
            Try a different search term
          </Typography>
        </Box>
      )}
      {/* Results */}
      {!loading && searchResults && searchResults.packages.length > 0 && (
        <>
          <Typography
            variant="body2"
            sx={{
              color: "text.secondary",
              mb: 2
            }}>
            Showing {searchResults.packages.length} of {searchResults.total} packages
          </Typography>

          {viewMode === 'cards' ? (
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
              {searchResults.packages.map((pkg: LexiconPackageInfo) => (
                <Box key={pkg.id} sx={{ flex: '1 1 calc(25% - 12px)', minWidth: 250 }}>
                  <PackageCard
                    package_info={pkg}
                    importing={importingId === getPackageKey(pkg.owner_username, pkg.name)}
                    onImport={() => handleImport(pkg.owner_username, pkg.name)}
                  />
                </Box>
              ))}
            </Box>
          ) : (
            <PackageTable
              packages={searchResults.packages}
              importingId={importingId}
              onImport={handleImport}
            />
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}>
              <GhostPagination
                page={page}
                totalPages={totalPages}
                total={searchResults.total}
                pageSize={ITEMS_PER_PAGE}
                onPageChange={setPage}
              />
            </Box>
          )}
        </>
      )}
      {/* Device Auth Dialog */}
      <DeviceAuthDialog
        open={!!deviceCode}
        deviceCode={deviceCode}
        onClose={cancelAuth}
      />
      {/* Snackbar for feedback */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
      >
        <Alert
          onClose={() => setSnackbar({ ...snackbar, open: false })}
          severity={snackbar.severity}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}
