// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, CircularProgress, Typography } from '@mui/material';
import type { ServiceTab } from '../hooks/useLogViewer';
import { ChaosCypherPalette } from '../../../theme/palette';
import { SERVICE_COLORS } from './logColors';

/** Log level to highlight color mapping. */
const LEVEL_COLORS: Record<string, string> = {
  INFO: ChaosCypherPalette.success,
  WARN: ChaosCypherPalette.warning,
  WARNING: ChaosCypherPalette.warning,
  ERROR: ChaosCypherPalette.error,
  DEBUG: 'rgba(255,255,255,0.4)',
};

const HTTP_STATUS_COLORS: Record<string, string> = {
  '2': ChaosCypherPalette.success, // 2xx success — mint
  '3': ChaosCypherPalette.primary, // 3xx redirect — cyan
  '4': ChaosCypherPalette.warning, // 4xx client error — gold
  '5': ChaosCypherPalette.error,   // 5xx server error — red
};

const VALKEY_LEVEL_MAP: Record<string, string> = {
  '*': 'INFO',
  '#': 'WARN',
  '-': 'ERROR',
  '.': 'DEBUG',
};

/** Render key=value pairs with syntax highlighting. */
function renderKeyValuePairs(text: string) {
  const parts: React.ReactNode[] = [];
  const kvRegex = /(\w+)=((?:"[^"]*"|'[^']*'|\S+))/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = kvRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(
        <span key={`t${lastIndex}`} style={{ color: 'rgba(255,255,255,0.4)' }}>
          {text.slice(lastIndex, match.index)}
        </span>
      );
    }
    parts.push(
      <span key={`k${match.index}`}>
        <span style={{ color: 'rgba(255,255,255,0.35)' }}>{match[1]}</span>
        <span style={{ color: 'rgba(255,255,255,0.2)' }}>=</span>
        <span style={{ color: 'rgba(124,77,255,0.7)' }}>{match[2]}</span>
      </span>
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push(
      <span key={`t${lastIndex}`} style={{ color: 'rgba(255,255,255,0.4)' }}>
        {text.slice(lastIndex)}
      </span>
    );
  }

  return <>{parts}</>;
}

/** Parse and render a single log line with format-specific syntax highlighting. */
function renderStructlogLine(text: string) {
  // 1. Structlog format: TIMESTAMP [LEVEL] EVENT [LOGGER] key=value ...
  const structMatch = text.match(
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z?)\s+\[(\w+)\s*\]\s+(\S+)\s+(?:\[([^\]]+)\]\s*)?(.*)?$/
  );

  if (structMatch) {
    const [, timestamp, level, event, loggerName, kvPairs] = structMatch;
    const levelColor = LEVEL_COLORS[level.toUpperCase()] || 'rgba(255,255,255,0.5)';

    return (
      <>
        <span style={{ color: 'rgba(255,255,255,0.3)' }}>{timestamp} </span>
        <span style={{ color: levelColor, fontWeight: 600 }}>{level.padEnd(8)}</span>
        <span style={{ color: 'rgba(255,255,255,0.95)', fontWeight: 500 }}>{event} </span>
        {loggerName && (
          <span style={{ color: 'rgba(0,229,255,0.35)' }}>[{loggerName}] </span>
        )}
        {kvPairs && renderKeyValuePairs(kvPairs)}
      </>
    );
  }

  // 2. Nginx JSON access log
  if (text.startsWith('{') && text.includes('"event":"http_request"')) {
    try {
      const log = JSON.parse(text);
      const status = String(log.status || '');
      const statusColor = HTTP_STATUS_COLORS[status[0]] || 'rgba(255,255,255,0.7)';
      const method = log.method || '';
      const path = log.path || '';
      const duration = log.duration_ms != null ? `${log.duration_ms}ms` : '';
      const bytes = log.bytes != null ? `${log.bytes}B` : '';
      const ts = log.timestamp || '';

      return (
        <>
          <span style={{ color: 'rgba(255,255,255,0.3)' }}>{ts} </span>
          <span style={{ color: ChaosCypherPalette.purple, fontWeight: 600 }}>{method.padEnd(5)}</span>
          <span style={{ color: 'rgba(255,255,255,0.9)' }}>{path} </span>
          <span style={{ color: statusColor, fontWeight: 600 }}>{status} </span>
          {duration && <span style={{ color: 'rgba(255,255,255,0.35)' }}>{duration} </span>}
          {bytes && <span style={{ color: 'rgba(255,255,255,0.25)' }}>{bytes}</span>}
        </>
      );
    } catch {
      // Fall through to generic handling
    }
  }

  // 3. Nginx error log: YYYY/MM/DD HH:MM:SS [level] PID#TID: message
  const nginxErrorMatch = text.match(
    /^(\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}:\d{2})\s+\[(\w+)\]\s+(\d+#\d+:\s*)(.*)/
  );
  if (nginxErrorMatch) {
    const [, ts, level, pid, message] = nginxErrorMatch;
    const levelColor = LEVEL_COLORS[level.toUpperCase()] || 'rgba(255,255,255,0.5)';
    return (
      <>
        <span style={{ color: 'rgba(255,255,255,0.3)' }}>{ts} </span>
        <span style={{ color: levelColor, fontWeight: 600 }}>[{level}] </span>
        <span style={{ color: 'rgba(255,255,255,0.3)' }}>{pid}</span>
        <span style={{ color: 'rgba(255,255,255,0.7)' }}>{message}</span>
      </>
    );
  }

  // 4. Valkey log: [valkey] PID:ROLE DD Mon YYYY HH:MM:SS.ms MARKER message
  const valkeyMatch = text.match(
    /^(?:\[valkey\]\s+)?(\d+:\w+)\s+(\d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2}\.\d+)\s+([-*#.])\s+(.*)/
  );
  if (valkeyMatch) {
    const [, pid, ts, marker, message] = valkeyMatch;
    const level = VALKEY_LEVEL_MAP[marker] || 'INFO';
    const levelColor = LEVEL_COLORS[level] || 'rgba(255,255,255,0.5)';
    return (
      <>
        <span style={{ color: 'rgba(255,255,255,0.3)' }}>{ts} </span>
        <span style={{ color: levelColor, fontWeight: 600 }}>{marker} </span>
        <span style={{ color: 'rgba(255,255,255,0.25)' }}>{pid} </span>
        <span style={{ color: 'rgba(255,255,255,0.8)' }}>{message}</span>
      </>
    );
  }

  // 5. Valkey startup lines: [valkey-startup] message
  const valkeyStartupMatch = text.match(/^(\[valkey-startup\])\s+(.*)/);
  if (valkeyStartupMatch) {
    return (
      <>
        <span style={{ color: ChaosCypherPalette.accent, fontWeight: 600 }}>{valkeyStartupMatch[1]} </span>
        <span style={{ color: 'rgba(255,255,255,0.7)' }}>{valkeyStartupMatch[2]}</span>
      </>
    );
  }

  // 6. AOF check output (from valkey-check-aof)
  if (text.startsWith('[offset') || text.startsWith('[info]') || text.includes('AOF')) {
    return <span style={{ color: 'rgba(255,255,255,0.45)' }}>{text}</span>;
  }

  // 7. Fallback: simple level highlight
  const levelMatch = text.match(/\b(INFO|WARN|WARNING|ERROR|DEBUG|info|warning|error|notice)\b/);
  if (levelMatch) {
    const level = levelMatch[1].toUpperCase();
    const levelColor = LEVEL_COLORS[level] || 'rgba(255,255,255,0.5)';
    return (
      <>
        <span style={{ color: 'rgba(255,255,255,0.5)' }}>{text.slice(0, levelMatch.index)}</span>
        <span style={{ color: levelColor, fontWeight: 600 }}>{levelMatch[1]}</span>
        <span style={{ color: 'rgba(255,255,255,0.7)' }}>{text.slice(levelMatch.index! + levelMatch[1].length)}</span>
      </>
    );
  }

  return <span style={{ color: 'rgba(255,255,255,0.6)' }}>{text}</span>;
}

/** Render a full log line with optional service badge prefix. */
function renderLogLine(line: string, index: number, activeTab: ServiceTab) {
  const serviceMatch = activeTab === 'all' ? line.match(/^\[(\w+)\]\s*/) : null;
  const service = serviceMatch ? serviceMatch[1] : null;
  const content = service ? line.slice(serviceMatch![0].length) : line;

  return (
    <div key={index} style={{ lineHeight: '1.8', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
      {service && (
        <span
          style={{
            color: SERVICE_COLORS[service] || 'rgba(255,255,255,0.5)',
            background: `${SERVICE_COLORS[service] || 'rgba(255,255,255,0.5)'}14`,
            padding: '1px 6px',
            borderRadius: '3px',
            fontSize: '9px',
            marginRight: '6px',
          }}
        >
          {service}
        </span>
      )}
      {renderStructlogLine(content)}
    </div>
  );
}

interface LogPaneProps {
  lines: string[];
  loading: boolean;
  activeTab: ServiceTab;
  logPaneRef: React.RefObject<HTMLDivElement | null>;
  onScroll: () => void;
}

/** Scrollable log display pane with loading/empty states and formatted log lines. */
export function LogPane({ lines, loading, activeTab, logPaneRef, onScroll }: LogPaneProps) {
  return (
    <Box
      ref={logPaneRef}
      onScroll={onScroll}
      sx={{
        background: 'rgba(0,0,0,0.3)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderTop: 'none',
        p: 1.5,
        fontFamily: 'monospace',
        fontSize: '11px',
        minHeight: 300,
        maxHeight: 500,
        overflowY: 'auto',
        overflowX: 'hidden',
      }}
    >
      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
          <CircularProgress size={24} />
        </Box>
      ) : lines.length === 0 ? (
        <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.3)' }}>
          No logs available
        </Typography>
      ) : (
        lines.map((line, i) => renderLogLine(line, i, activeTab))
      )}
    </Box>
  );
}
