// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * @module SiteImportOffer
 * The Add Source dialog's answer to a site check: while the address is being
 * checked, a quiet "looking" line; when the check finds more than one page, a
 * choice between the whole site and just this page, with the site's pages in
 * a collapsed, deselectable list; otherwise one line saying why there is no
 * choice (nothing else found, or the address could not be checked).
 */

import { useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Collapse,
  FormControlLabel,
  List,
  ListItem,
  Radio,
  RadioGroup,
  Typography,
} from '@mui/material';
import type { SiteDiscoveryMethod, SitePreview } from '../../types';

const METHOD_LABELS: Record<SiteDiscoveryMethod, string> = {
  llms_txt: 'llms.txt',
  sitemap: 'sitemap',
  crawl: 'crawling',
  none: 'nothing',
};

/** The radio label for the whole-site choice: "71 pages (found by llms.txt)". */
function wholeSiteLabel(preview: SitePreview): string {
  const count = preview.capped ? `at least ${preview.page_count}` : `${preview.page_count}`;
  return `Import the whole site: ${count} pages (found by ${METHOD_LABELS[preview.method]})`;
}

interface SiteImportOfferProps {
  /** The check is running. */
  checking: boolean;
  /** Why the check failed, or null. */
  error: string | null;
  /** The check's answer, once it has one. */
  preview: SitePreview | undefined;
  /**
   * The choice changed: `undefined` is just this page; for the whole site, the
   * pages unticked (empty: every page). The import discovers the pages itself,
   * so a page the quick check never listed is still imported.
   */
  onChoiceChange: (exclude: string[] | undefined) => void;
  /** Most pages one site import takes (`web.site_max_pages`). */
  maxPages: number;
}

export function SiteImportOffer({
  checking,
  error,
  preview,
  onChoiceChange,
  maxPages,
}: SiteImportOfferProps) {
  const [showPages, setShowPages] = useState(false);
  const [wholeSite, setWholeSite] = useState(false);
  const [excluded, setExcluded] = useState<ReadonlySet<string>>(new Set());

  const choose = (nextWhole: boolean, nextExcluded: ReadonlySet<string>) => {
    setWholeSite(nextWhole);
    setExcluded(nextExcluded);
    if (!nextWhole || !preview) onChoiceChange(undefined);
    else onChoiceChange(preview.pages.map((p) => p.url).filter((url) => nextExcluded.has(url)));
  };
  const togglePage = (url: string) => {
    const next = new Set(excluded);
    if (next.has(url)) next.delete(url);
    else next.add(url);
    choose(true, next);
  };

  if (checking) {
    return (
      <Box role="status" sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <CircularProgress size={14} />
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          Looking for more pages at this address…
        </Typography>
      </Box>
    );
  }
  if (error) {
    return (
      <Typography variant="caption" sx={{ color: 'warning.main' }}>
        This address could not be checked for more pages: {error}
      </Typography>
    );
  }
  if (!preview) return null;
  if (preview.page_count <= 1) {
    return (
      <Typography variant="caption" sx={{ color: 'text.secondary' }}>
        No other pages found at this address; this page is imported on its own.
      </Typography>
    );
  }

  const selected = preview.page_count - excluded.size;
  return (
    <Box>
      <RadioGroup
        value={wholeSite ? 'site' : 'page'}
        onChange={(e) => choose(e.target.value === 'site', excluded)}
      >
        <FormControlLabel
          value="site"
          control={<Radio size="small" />}
          label={<Typography variant="body2">{wholeSiteLabel(preview)}</Typography>}
        />
        <FormControlLabel
          value="page"
          control={<Radio size="small" />}
          label={<Typography variant="body2">Just this page</Typography>}
        />
      </RadioGroup>
      {preview.capped && (
        <Typography variant="caption" component="p" sx={{ color: 'text.secondary', ml: 4 }}>
          The quick check stopped early; the import finds the rest, up to {maxPages} pages.
        </Typography>
      )}
      {wholeSite && (
        <Box sx={{ ml: 4 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Button size="small" onClick={() => setShowPages((open) => !open)}>
              {showPages ? 'Hide pages' : 'Show pages'}
            </Button>
            {excluded.size > 0 && (
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                {selected} of {preview.page_count} pages selected
              </Typography>
            )}
          </Box>
          <Collapse in={showPages} unmountOnExit>
            <List
              dense
              aria-label="Pages to import"
              sx={{ maxHeight: 240, overflowY: 'auto', py: 0 }}
            >
              {preview.pages.map((page) => (
                <ListItem key={page.url} disablePadding>
                  <FormControlLabel
                    control={
                      <Checkbox
                        size="small"
                        checked={!excluded.has(page.url)}
                        onChange={() => togglePage(page.url)}
                      />
                    }
                    label={
                      <Typography variant="caption" sx={{ wordBreak: 'break-all' }}>
                        {page.title || page.url}
                      </Typography>
                    }
                  />
                </ListItem>
              ))}
            </List>
          </Collapse>
        </Box>
      )}
    </Box>
  );
}
