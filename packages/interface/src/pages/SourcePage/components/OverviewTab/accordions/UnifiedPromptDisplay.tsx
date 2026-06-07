// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Fragment, useMemo, useState, type ReactNode } from 'react';
import { Box, IconButton, Typography } from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import type { ExtractionTaskStats } from '../../../../../types';

interface PromptSegment {
  text: string;
  type: 'generic' | 'domain';
}

interface UnifiedPromptDisplayProps {
  stats: ExtractionTaskStats;
}

// Splits on the backend sentinel placeholders (ai_entities.py
// PROMPT_CHUNK_TEXT_PLACEHOLDER / PROMPT_PASS1_ENTITIES_PLACEHOLDER) — the
// "[[ ... ]]" runs that mark where the chunk's text / pass-1 entities are
// injected at runtime. Capturing group keeps the placeholders in the split
// output (odd indices) so they can be highlighted.
const PLACEHOLDER_SPLIT = /(\[\[ .*? \]\])/g;

/**
 * Split a prompt into generic vs. domain-specific segments by locating the
 * known domain parts (entity/relationship templates, guidance, examples)
 * inside it. Used to tint the domain-specific portions so operators can see
 * what their domain plugin contributed vs. the built-in scaffolding.
 */
function splitDomainSegments(text: string, domainParts: string[]): PromptSegment[] {
  const result: PromptSegment[] = [];
  let remaining = text;
  for (const domainPart of domainParts) {
    const idx = remaining.indexOf(domainPart);
    if (idx !== -1) {
      if (idx > 0) result.push({ text: remaining.substring(0, idx), type: 'generic' });
      result.push({ text: domainPart, type: 'domain' });
      remaining = remaining.substring(idx + domainPart.length);
    }
  }
  if (remaining) result.push({ text: remaining, type: 'generic' });
  return result;
}

/**
 * Render a run of prompt text, wrapping any ``[[ … ]]`` placeholder in a
 * highlighted chip so it reads as "runtime-injected content" rather than
 * literal prompt text.
 */
function renderWithPlaceholders(text: string): ReactNode[] {
  // `split` with a capturing group yields plain text at even indices and the
  // captured placeholders at odd indices.
  return text.split(PLACEHOLDER_SPLIT).map((part, idx) => {
    if (idx % 2 === 1) {
      return (
        <Box
          key={idx}
          component="span"
          data-testid="prompt-placeholder"
          sx={{
            display: 'inline',
            mx: 0.25,
            px: 0.5,
            py: 0.1,
            borderRadius: 0.5,
            bgcolor: 'rgba(255, 193, 7, 0.16)',
            border: '1px dashed',
            borderColor: 'warning.main',
            color: 'warning.main',
            fontWeight: 600,
            fontSize: '0.72rem',
            whiteSpace: 'normal',
          }}
        >
          {part}
        </Box>
      );
    }
    return <Fragment key={idx}>{part}</Fragment>;
  });
}

/** Domain-tinted, placeholder-highlighted body of one prompt. */
function PromptBody({ text, domainParts }: { text: string; domainParts: string[] }) {
  const segments = useMemo(() => splitDomainSegments(text, domainParts), [text, domainParts]);
  return (
    <Box sx={{ fontFamily: 'monospace', fontSize: '0.85rem', lineHeight: 1.6 }}>
      {segments.map((segment, idx) => (
        <Box
          key={`segment-${idx}-${segment.type}`}
          component="span"
          sx={{
            whiteSpace: 'pre-wrap',
            display: 'inline',
            color: segment.type === 'domain' ? 'info.main' : 'text.primary',
          }}
        >
          {renderWithPlaceholders(segment.text)}
        </Box>
      ))}
    </Box>
  );
}

interface PromptCollapsibleProps {
  label: string;
  text: string;
  domainParts: string[];
  open: boolean;
  onToggle: () => void;
}

/** A collapsible labelled prompt block with copy-to-clipboard. */
function PromptCollapsible({ label, text, domainParts, open, onToggle }: PromptCollapsibleProps) {
  return (
    <Box sx={{ mb: 2, borderBottom: '1px solid rgba(255, 255, 255, 0.08)' }}>
      <Box
        onClick={onToggle}
        sx={{
          cursor: 'pointer',
          p: 2,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.03)' },
          transition: 'background 0.15s',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {open ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
          <Typography variant="subtitle2">{label}</Typography>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <IconButton
            aria-label={`Copy ${label}`}
            size="small"
            onClick={(e) => {
              e.stopPropagation();
              void navigator.clipboard.writeText(text);
            }}
            sx={{ opacity: 0.4, '&:hover': { opacity: 1 }, color: 'text.secondary' }}
          >
            <ContentCopyIcon sx={{ fontSize: 14 }} />
          </IconButton>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            Click to {open ? 'collapse' : 'expand'}
          </Typography>
        </Box>
      </Box>
      {open && (
        <Box
          sx={{
            mx: 2,
            mb: 2,
            p: 2,
            maxHeight: 600,
            overflow: 'auto',
            background: 'rgba(0, 0, 0, 0.3)',
            border: '1px solid rgba(255, 255, 255, 0.06)',
            borderRadius: 1.5,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <PromptBody text={text} domainParts={domainParts} />
        </Box>
      )}
    </Box>
  );
}

/**
 * Renders the AI prompts for a source as the operator-facing *templates*:
 * the system prompt, then the two-pass extraction prompts (Pass 1 entities,
 * Pass 2 relationships). Per-chunk content (the chunk's sentences, and the
 * pass-1 entity list for relationships) is shown as a highlighted placeholder
 * rather than one chunk's baked-in text, and domain-specific portions are
 * tinted to distinguish them from the built-in scaffolding.
 */
export function UnifiedPromptDisplay({ stats }: UnifiedPromptDisplayProps) {
  const [openEntity, setOpenEntity] = useState(false);
  const [openRelationship, setOpenRelationship] = useState(false);

  const domainParts = useMemo(
    () =>
      [
        stats.entity_templates,
        stats.relationship_templates,
        stats.domain_guidance,
        stats.domain_examples,
      ].filter(Boolean) as string[],
    [stats],
  );

  const entityPrompt = stats.user_instructions ?? '';
  const relationshipPrompt = stats.relationship_instructions ?? '';
  const hasRelationship = Boolean(relationshipPrompt);

  return (
    <Box>
      {/* Legend */}
      <Box sx={{ display: 'flex', gap: 2, mb: 2, alignItems: 'center', flexWrap: 'wrap' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box
            sx={{
              width: 16,
              height: 16,
              borderRadius: 0.5,
              border: '1px solid',
              borderColor: 'info.main',
              borderLeft: '3px solid',
              borderLeftColor: 'info.main',
            }}
          />
          <Typography variant="caption" sx={{ color: 'info.main' }}>
            Domain-specific
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Box
            sx={{
              width: 16,
              height: 16,
              borderRadius: 0.5,
              border: '1px dashed',
              borderColor: 'warning.main',
              bgcolor: 'rgba(255, 193, 7, 0.16)',
            }}
          />
          <Typography variant="caption" sx={{ color: 'warning.main' }}>
            Inserted per chunk at runtime
          </Typography>
        </Box>
      </Box>

      {/* System Prompt */}
      {stats.system_prompt && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="subtitle2" sx={{ color: 'text.secondary', mb: 1 }}>
            System Prompt
          </Typography>
          <Box
            sx={{
              position: 'relative',
              background: 'rgba(0, 0, 0, 0.3)',
              border: '1px solid rgba(255, 255, 255, 0.06)',
              borderRadius: 1.5,
              p: 2,
              maxHeight: 400,
              overflow: 'auto',
            }}
          >
            <IconButton
              aria-label="Copy System Prompt"
              size="small"
              onClick={() => void navigator.clipboard.writeText(stats.system_prompt ?? '')}
              sx={{
                position: 'absolute',
                top: 8,
                right: 8,
                opacity: 0.4,
                '&:hover': { opacity: 1 },
                color: 'text.secondary',
              }}
            >
              <ContentCopyIcon sx={{ fontSize: 14 }} />
            </IconButton>
            <Typography
              sx={{ fontFamily: 'monospace', fontSize: '0.85rem', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}
            >
              {stats.system_prompt}
            </Typography>
          </Box>
        </Box>
      )}

      {/* Pass 1: entity extraction prompt */}
      <PromptCollapsible
        label="Entity extraction prompt (Pass 1)"
        text={entityPrompt}
        domainParts={domainParts}
        open={openEntity}
        onToggle={() => setOpenEntity((prev) => !prev)}
      />

      {/* Pass 2: relationship extraction prompt (absent on legacy sources) */}
      {hasRelationship && (
        <PromptCollapsible
          label="Relationship extraction prompt (Pass 2)"
          text={relationshipPrompt}
          domainParts={domainParts}
          open={openRelationship}
          onToggle={() => setOpenRelationship((prev) => !prev)}
        />
      )}

      <Typography variant="body2" sx={{ color: 'text.secondary', mt: 2 }}>
        Extraction runs two passes per chunk group. The highlighted placeholders mark where the
        chunk&apos;s sentences (and, for relationships, the entities found in pass 1) are inserted
        at runtime.
      </Typography>
    </Box>
  );
}
