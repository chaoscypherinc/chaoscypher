// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';
import type { ChunkCitationMap, ChunkCitationSummary, EntityReferenceMap, EntityReferenceSummary } from '../../types';
import EntityReference from './EntityReference';
import ChunkCitation from './ChunkCitation';

// Pattern to match entity references: [[node:ID|Label]] or [[edge:ID|Label]]
// Accepts common separators: colon, underscore, hyphen, dot, slash, space
const ENTITY_REFERENCE_PATTERN = /\[\[(node|edge)[_:\-./\s]([a-zA-Z_]*[a-f0-9-]+)\|([^\]]+)\]\]/gi;

// Pattern to match chunk citations: [[cite:CHUNK_ID:Sn|label]] or [[cite:CHUNK_ID#Sn]]
const CHUNK_CITATION_PATTERN = /\[\[cite:([a-f0-9-]+)[:#](S\d+(?:[,;]\s*S\d+)*)(?:\|([^\]]+))?\]\]/gi;

interface ChatMarkdownProps {
  /** Markdown content to render */
  content: string;
  /** Pre-fetched entity reference data from message metadata */
  referencedEntities?: EntityReferenceMap;
  /** Pre-fetched chunk citation data from message metadata */
  chunkCitations?: ChunkCitationMap;
}

interface PlaceholderData {
  kind: 'entity' | 'citation';
  // Entity fields
  type?: string;
  id?: string;
  label?: string;
  // Citation fields
  chunkId?: string;
  sentenceRefs?: string;
}

/**
 * Pre-process content to replace entity references and chunk citations
 * with placeholder tokens that won't be mangled by markdown parsing.
 */
function preprocessContent(content: string): { processed: string; placeholders: Map<string, PlaceholderData> } {
  const placeholders = new Map<string, PlaceholderData>();
  let counter = 0;

  ENTITY_REFERENCE_PATTERN.lastIndex = 0;
  CHUNK_CITATION_PATTERN.lastIndex = 0;

  // Replace entity references
  let processed = content.replace(ENTITY_REFERENCE_PATTERN, (_match, entityType, entityId, label) => {
    const placeholder = `%%ENTITY_${counter}%%`;
    placeholders.set(placeholder, {
      kind: 'entity',
      type: entityType.toLowerCase(),
      id: entityId,
      label: label,
    });
    counter++;
    return placeholder;
  });

  // Replace chunk citations
  processed = processed.replace(CHUNK_CITATION_PATTERN, (_match, chunkId, sentenceRefs, label) => {
    const placeholder = `%%CITE_${counter}%%`;
    placeholders.set(placeholder, {
      kind: 'citation',
      chunkId: chunkId,
      sentenceRefs: sentenceRefs,
      label: label,
    });
    counter++;
    return placeholder;
  });

  return { processed, placeholders };
}

/**
 * Render text with entity references and chunk citations inline.
 * Splits text on placeholders and renders chips inline.
 */
function renderTextWithPlaceholders(
  text: string,
  placeholders: Map<string, PlaceholderData>,
  referencedEntities?: EntityReferenceMap,
  chunkCitations?: ChunkCitationMap,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const placeholderPattern = /%%(?:ENTITY|CITE)_\d+%%/g;

  let lastIndex = 0;
  let match;
  let keyIndex = 0;

  placeholderPattern.lastIndex = 0;

  while ((match = placeholderPattern.exec(text)) !== null) {
    // Add text before placeholder
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }

    const placeholder = match[0];
    const data = placeholders.get(placeholder);
    let renderedBlockquoteCitation = false;

    if (data?.kind === 'entity' && data.id) {
      const fullEntityData: EntityReferenceSummary = referencedEntities?.[data.id] || {
        id: data.id,
        type: (data.type || 'node') as 'node' | 'edge',
        label: data.label || '',
      };

      parts.push(
        <EntityReference
          key={`entity-${keyIndex++}`}
          entity={fullEntityData}
        />
      );
    } else if (data?.kind === 'citation' && data.chunkId) {
      const citationKey = `${data.chunkId}:${data.sentenceRefs || ''}`;
      const enriched = chunkCitations?.[citationKey];
      // Prefer: enriched label > parsed label > sentence refs > generic fallback
      const resolvedLabel = enriched?.label || data.label || data.sentenceRefs || 'source';
      const citationData: ChunkCitationSummary = enriched
        ? { ...enriched, label: resolvedLabel }
        : {
            chunk_id: data.chunkId,
            sentence_refs: data.sentenceRefs || '',
            label: resolvedLabel,
          };

      if (citationData.sentence_text) {
        parts.push(
          <blockquote
            key={`cite-bq-${keyIndex}`}
            className="citation-blockquote"
            style={{ margin: '0.5em 0' }}
          >
            <p style={{ display: 'block', margin: '0 0 0.25em 0' }}>
              {citationData.sentence_text}
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <ChunkCitation
                key={`cite-chip-${keyIndex}`}
                citation={citationData}
              />
            </div>
          </blockquote>
        );
        keyIndex++;
        renderedBlockquoteCitation = true;
      } else {
        parts.push(
          <ChunkCitation
            key={`cite-${keyIndex++}`}
            citation={citationData}
          />
        );
      }
    }

    lastIndex = match.index + placeholder.length;

    // Safety net: skip orphaned trailing punctuation after blockquote citations.
    // The backend already moves punctuation before the citation marker, but if
    // any slips through, drop it rather than rendering it on its own line.
    if (renderedBlockquoteCitation) {
      const trailing = text.slice(lastIndex);
      const punct = trailing.match(/^[.;,!?]+/);
      if (punct) {
        lastIndex += punct[0].length;
      }
    }
  }

  // Add remaining text
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts;
}

/**
 * Create custom ReactMarkdown components that handle placeholders
 */
function createComponents(
  placeholders: Map<string, PlaceholderData>,
  referencedEntities?: EntityReferenceMap,
  chunkCitations?: ChunkCitationMap,
): Components {
  const renderChildren = (children: React.ReactNode): React.ReactNode => {
    if (typeof children === 'string') {
      const parts = renderTextWithPlaceholders(children, placeholders, referencedEntities, chunkCitations);
      return parts.length === 1 && typeof parts[0] === 'string' ? parts[0] : <>{parts}</>;
    }
    if (Array.isArray(children)) {
      return children.map((child, i) => {
        if (typeof child === 'string') {
          const parts = renderTextWithPlaceholders(child, placeholders, referencedEntities, chunkCitations);
          return parts.length === 1 && typeof parts[0] === 'string'
            ? parts[0]
            : <span key={i} style={{ display: 'inline' }}>{parts}</span>;
        }
        return child;
      });
    }
    return children;
  };

  return {
    p: ({ children, ...props }) => (
      <p {...props} style={{ display: 'block', margin: '0 0 0.5em 0' }}>
        {renderChildren(children)}
      </p>
    ),
    blockquote: ({ children, ...props }) => (
      <blockquote {...props}>
        {renderChildren(children)}
      </blockquote>
    ),
    li: ({ children, ...props }) => (
      <li {...props}>
        {renderChildren(children)}
      </li>
    ),
    strong: ({ children, ...props }) => (
      <strong {...props}>
        {renderChildren(children)}
      </strong>
    ),
    em: ({ children, ...props }) => (
      <em {...props}>
        {renderChildren(children)}
      </em>
    ),
  };
}

/**
 * Check if content contains entity references or chunk citations
 */
function hasInlineReferences(content: string): boolean {
  ENTITY_REFERENCE_PATTERN.lastIndex = 0;
  CHUNK_CITATION_PATTERN.lastIndex = 0;
  return ENTITY_REFERENCE_PATTERN.test(content) || CHUNK_CITATION_PATTERN.test(content);
}

/**
 * Enhanced markdown renderer with inline entity references and chunk citations.
 *
 * Parses content for [[node:id|label]], [[edge:id|label]], and [[cite:id:Sn|label]]
 * patterns and renders them as interactive components inline with text.
 */
export default function ChatMarkdown({
  content,
  referencedEntities,
  chunkCitations,
}: ChatMarkdownProps) {
  // Check if content has inline references
  const containsReferences = useMemo(() => hasInlineReferences(content), [content]);

  // Preprocess and create components if we have references
  const { processedContent, components } = useMemo(() => {
    if (!containsReferences) {
      return { processedContent: content, components: undefined };
    }

    const { processed, placeholders } = preprocessContent(content);
    const comps = createComponents(placeholders, referencedEntities, chunkCitations);

    return { processedContent: processed, components: comps };
  }, [content, referencedEntities, chunkCitations, containsReferences]);

  return (
    <ReactMarkdown components={components} rehypePlugins={[rehypeSanitize]}>
      {processedContent}
    </ReactMarkdown>
  );
}
