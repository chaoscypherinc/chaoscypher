// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for LogPane.tsx — scrollable log display with seven format branches.
 *
 * Strategy: render LogPane with crafted `lines` strings that exercise each
 * private format branch (structlog, nginx JSON, nginx error, valkey, valkey-
 * startup, AOF check, fallback level highlight, plain fallback). No mocks are
 * needed — MUI + palette + SERVICE_COLORS all work fine under jsdom.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { ServiceTab } from '../../hooks/useLogViewer';
import { LogPane } from '../LogPane';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRef(): React.RefObject<HTMLDivElement | null> {
  return { current: null };
}

function renderLogPane(
  lines: string[],
  opts: {
    loading?: boolean;
    activeTab?: ServiceTab;
    onScroll?: () => void;
  } = {},
) {
  const { loading = false, activeTab = 'all', onScroll = vi.fn() } = opts;
  return render(
    <LogPane
      lines={lines}
      loading={loading}
      activeTab={activeTab}
      logPaneRef={makeRef()}
      onScroll={onScroll}
    />,
  );
}

// ---------------------------------------------------------------------------
// Loading + empty states
// ---------------------------------------------------------------------------

describe('LogPane — loading and empty states', () => {
  it('shows CircularProgress (progressbar role) when loading=true, no log lines', () => {
    renderLogPane(['some line'], { loading: true });
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(screen.queryByText('some line')).not.toBeInTheDocument();
  });

  it('shows "No logs available" when loading=false and lines=[]', () => {
    renderLogPane([]);
    expect(screen.getByText('No logs available')).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Branch 1: structlog format
// ---------------------------------------------------------------------------

describe('LogPane — branch 1: structlog format', () => {
  it('renders timestamp, event, and key=value pairs', () => {
    const line =
      '2026-05-25T08:12:27.123Z [INFO    ] some_event [logger.name] key=value foo="bar baz"';
    renderLogPane([line]);

    expect(screen.getByText('2026-05-25T08:12:27.123Z')).toBeInTheDocument();
    expect(screen.getByText('some_event')).toBeInTheDocument();
    // logger name rendered inside brackets
    expect(screen.getByText('[logger.name]')).toBeInTheDocument();
    // key and foo are span keys, values are rendered separately
    expect(screen.getByText('key')).toBeInTheDocument();
    expect(screen.getByText('value')).toBeInTheDocument();
    expect(screen.getByText('foo')).toBeInTheDocument();
    expect(screen.getByText('"bar baz"')).toBeInTheDocument();
  });

  it('renders structlog WARN level', () => {
    const line =
      '2026-05-25T08:12:27.000Z [WARN    ] slow_query [db.pool] duration=2500';
    renderLogPane([line]);
    expect(screen.getByText('slow_query')).toBeInTheDocument();
    expect(screen.getByText('duration')).toBeInTheDocument();
    expect(screen.getByText('2500')).toBeInTheDocument();
  });

  it('renders structlog ERROR level', () => {
    const line =
      '2026-05-25T08:12:27.000Z [ERROR   ] connection_failed [db.pool] retries=3';
    renderLogPane([line]);
    expect(screen.getByText('connection_failed')).toBeInTheDocument();
  });

  it('renders structlog DEBUG level without logger name', () => {
    const line =
      '2026-05-25T08:12:27.000Z [DEBUG   ] tick [] count=1';
    renderLogPane([line]);
    expect(screen.getByText('tick')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Branch 2: nginx JSON access log
// ---------------------------------------------------------------------------

describe('LogPane — branch 2: nginx JSON access log', () => {
  it('renders method, path, and status for a valid http_request JSON log', () => {
    const log = {
      event: 'http_request',
      status: 200,
      method: 'GET',
      path: '/api/v1/x',
      duration_ms: 12,
      bytes: 345,
      timestamp: '2026-05-25T08:12:27Z',
    };
    renderLogPane([JSON.stringify(log)]);

    expect(screen.getByText(/GET/)).toBeInTheDocument();
    expect(screen.getByText('/api/v1/x')).toBeInTheDocument();
    expect(screen.getByText(/200/)).toBeInTheDocument();
    expect(screen.getByText(/12ms/)).toBeInTheDocument();
    expect(screen.getByText(/345B/)).toBeInTheDocument();
  });

  it('renders 5xx status (server error color path)', () => {
    const log = {
      event: 'http_request',
      status: 503,
      method: 'POST',
      path: '/api/v1/extract',
      duration_ms: null,
      bytes: null,
      timestamp: '2026-05-25T08:12:27Z',
    };
    renderLogPane([JSON.stringify(log)]);
    expect(screen.getByText(/POST/)).toBeInTheDocument();
    expect(screen.getByText('/api/v1/extract')).toBeInTheDocument();
    expect(screen.getByText(/503/)).toBeInTheDocument();
  });

  it('falls through to plain rendering for malformed JSON starting with { and "event":"http_request"', () => {
    // Starts with `{` and contains `"event":"http_request"` but is not valid JSON
    const malformed = '{"event":"http_request" BROKEN';
    const { container } = renderLogPane([malformed]);
    // Falls through to final span fallback — line text is in the DOM somewhere
    expect(container.textContent).toContain('http_request');
  });
});

// ---------------------------------------------------------------------------
// Branch 3: nginx error log
// ---------------------------------------------------------------------------

describe('LogPane — branch 3: nginx error log', () => {
  it('renders timestamp, level bracket, pid, and message', () => {
    const line = '2026/05/25 08:12:27 [error] 123#456: something failed';
    renderLogPane([line]);

    expect(screen.getByText('2026/05/25 08:12:27')).toBeInTheDocument();
    expect(screen.getByText('[error]')).toBeInTheDocument();
    expect(screen.getByText(/123#456:/)).toBeInTheDocument();
    expect(screen.getByText('something failed')).toBeInTheDocument();
  });

  it('renders nginx warn level', () => {
    const line = '2026/05/25 09:00:00 [warn] 7#8: upstream timeout';
    renderLogPane([line]);
    expect(screen.getByText('[warn]')).toBeInTheDocument();
    expect(screen.getByText('upstream timeout')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Branch 4: valkey log (all four markers)
// ---------------------------------------------------------------------------

describe('LogPane — branch 4: valkey log markers', () => {
  it('renders * (INFO) marker', () => {
    const line = '[valkey] 12:M 25 May 2026 08:12:27.123 * Server started';
    renderLogPane([line]);
    expect(screen.getByText('*')).toBeInTheDocument();
    expect(screen.getByText('Server started')).toBeInTheDocument();
  });

  it('renders # (WARN) marker', () => {
    const line = '[valkey] 12:M 25 May 2026 08:12:27.456 # Config warning';
    renderLogPane([line]);
    expect(screen.getByText('#')).toBeInTheDocument();
    expect(screen.getByText('Config warning')).toBeInTheDocument();
  });

  it('renders - (ERROR) marker', () => {
    const line = '[valkey] 12:M 25 May 2026 08:12:27.789 - Fatal error occurred';
    renderLogPane([line]);
    expect(screen.getByText('-')).toBeInTheDocument();
    expect(screen.getByText('Fatal error occurred')).toBeInTheDocument();
  });

  it('renders . (DEBUG) marker', () => {
    const line = '[valkey] 12:M 25 May 2026 08:12:27.001 . Background save complete';
    renderLogPane([line]);
    expect(screen.getByText('.')).toBeInTheDocument();
    expect(screen.getByText('Background save complete')).toBeInTheDocument();
  });

  it('renders valkey without [valkey] prefix (bare PID:ROLE format)', () => {
    const line = '99:S 25 May 2026 10:00:00.000 * Connecting to MASTER';
    renderLogPane([line]);
    expect(screen.getByText('*')).toBeInTheDocument();
    expect(screen.getByText('Connecting to MASTER')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Branch 5: valkey-startup
// ---------------------------------------------------------------------------

describe('LogPane — branch 5: valkey-startup', () => {
  it('renders the [valkey-startup] badge and message text', () => {
    const line = '[valkey-startup] booting';
    renderLogPane([line]);
    expect(screen.getByText('[valkey-startup]')).toBeInTheDocument();
    expect(screen.getByText('booting')).toBeInTheDocument();
  });

  it('renders valkey-startup with longer message', () => {
    const line = '[valkey-startup] initializing persistence layer';
    renderLogPane([line]);
    expect(screen.getByText('[valkey-startup]')).toBeInTheDocument();
    expect(screen.getByText('initializing persistence layer')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Branch 6: AOF check output
// ---------------------------------------------------------------------------

describe('LogPane — branch 6: AOF check output', () => {
  it('renders a line starting with [offset', () => {
    const line = '[offset 1234] checking AOF';
    renderLogPane([line]);
    expect(screen.getByText(line)).toBeInTheDocument();
  });

  it('renders a line starting with [info]', () => {
    // Note: when activeTab='all', [info] is parsed as a service-badge prefix
    // (service name "info"), then the remaining "AOF rewrite complete" hits the
    // AOF branch. Confirm both parts are in the DOM.
    const line = '[info] AOF rewrite complete';
    renderLogPane([line], { activeTab: 'all' });
    expect(screen.getByText('info')).toBeInTheDocument();
    expect(screen.getByText('AOF rewrite complete')).toBeInTheDocument();
  });

  it('renders a line starting with [info] with specific tab (no badge parsing)', () => {
    // With activeTab='cortex' (not 'all'), the serviceMatch is skipped;
    // the whole line feeds into renderStructlogLine which hits the AOF branch.
    const line = '[info] AOF rewrite complete';
    renderLogPane([line], { activeTab: 'cortex' });
    expect(screen.getByText(line)).toBeInTheDocument();
  });

  it('renders a line containing AOF anywhere', () => {
    const line = 'Loading AOF file from disk';
    renderLogPane([line]);
    expect(screen.getByText(line)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Branch 7: fallback level highlight + plain fallback
// ---------------------------------------------------------------------------

describe('LogPane — branch 7: fallback level highlight and plain fallback', () => {
  it('renders a plain line containing ERROR with the level keyword highlighted', () => {
    const line = 'systemd: service entered ERROR state';
    const { container } = renderLogPane([line]);
    // Level keyword is split into its own span
    expect(screen.getByText('ERROR')).toBeInTheDocument();
    // RTL normalizes leading/trailing whitespace on individual spans, so use
    // container.textContent to confirm the full line is present and split.
    expect(container.textContent).toContain('systemd: service entered');
    expect(container.textContent).toContain('state');
  });

  it('renders a plain line containing WARN', () => {
    const line = 'disk space WARN threshold reached';
    renderLogPane([line]);
    expect(screen.getByText('WARN')).toBeInTheDocument();
  });

  it('renders a plain line containing info (case-insensitive match)', () => {
    const line = 'some info message here';
    renderLogPane([line]);
    expect(screen.getByText('info')).toBeInTheDocument();
  });

  it('renders a completely plain line with no level keyword as a single grey span', () => {
    const line = 'just a plain log line with nothing special';
    renderLogPane([line]);
    expect(screen.getByText(line)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Service badge rendering (renderLogLine)
// ---------------------------------------------------------------------------

describe('LogPane — service badge rendering', () => {
  it('renders "cortex" badge when activeTab="all" and line starts with [cortex]', () => {
    const line = '[cortex] 2026-05-25T08:12:27.000Z [INFO    ] startup [] ';
    renderLogPane([line], { activeTab: 'all' });
    expect(screen.getByText('cortex')).toBeInTheDocument();
    // Event from the structlog content after stripping the prefix
    expect(screen.getByText('startup')).toBeInTheDocument();
  });

  it('renders "neuron" badge for neuron-prefixed lines in all tab', () => {
    const line = '[neuron] just a plain neuron message';
    renderLogPane([line], { activeTab: 'all' });
    expect(screen.getByText('neuron')).toBeInTheDocument();
  });

  it('does NOT render a service badge when activeTab is the service itself (not "all")', () => {
    // When activeTab='cortex', the match is skipped — no badge parsed
    const line = '[cortex] some message';
    renderLogPane([line], { activeTab: 'cortex' });
    // The raw line is rendered as content, badge is not extracted
    expect(screen.queryByText('cortex')).not.toBeInTheDocument();
  });

  it('renders multiple lines each with their own badge', () => {
    const lines = [
      '[cortex] 2026-05-25T08:00:00.000Z [INFO    ] req_start [] ',
      '[nginx] 2026/05/25 08:00:00 [error] 1#2: upstream',
      '[valkey] 12:M 25 May 2026 08:00:00.000 * ready',
    ];
    renderLogPane(lines, { activeTab: 'all' });
    expect(screen.getByText('cortex')).toBeInTheDocument();
    expect(screen.getByText('nginx')).toBeInTheDocument();
    expect(screen.getByText('valkey')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// onScroll callback
// ---------------------------------------------------------------------------

describe('LogPane — onScroll callback', () => {
  it('calls onScroll when the pane is scrolled', () => {
    const onScroll = vi.fn();
    const { container } = render(
      <LogPane
        lines={['line one', 'line two']}
        loading={false}
        activeTab="all"
        logPaneRef={makeRef()}
        onScroll={onScroll}
      />,
    );
    // The outermost Box element is the scroll container
    const scrollBox = container.firstChild as HTMLElement;
    fireEvent.scroll(scrollBox);
    expect(onScroll).toHaveBeenCalledTimes(1);
  });
});
