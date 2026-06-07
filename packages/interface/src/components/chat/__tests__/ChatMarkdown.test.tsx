// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router';
import type { ChunkCitationMap, EntityReferenceMap } from '../../../types';

// ---------------------------------------------------------------------------
// Stub child components so we can assert on their rendered markers without
// fighting MUI Tooltip / useNavigate complexity.
// ---------------------------------------------------------------------------
vi.mock('../EntityReference', () => ({
  default: ({ entity }: { entity: { id: string; label: string; type: string } }) => (
    <span data-testid="entity-ref" data-entity-id={entity.id} data-entity-type={entity.type}>
      {entity.label}
    </span>
  ),
}));

vi.mock('../ChunkCitation', () => ({
  default: ({ citation }: { citation: { chunk_id: string; label: string; sentence_refs: string } }) => (
    <span
      data-testid="chunk-citation"
      data-chunk-id={citation.chunk_id}
      data-sentence-refs={citation.sentence_refs}
    >
      {citation.label}
    </span>
  ),
}));

// Import component AFTER mocks are installed
import ChatMarkdown from '../ChatMarkdown';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Render ChatMarkdown inside a MemoryRouter (child stubs that may use
 * useNavigate need it; keeps renders stable).
 */
function renderMarkdown(
  content: string,
  referencedEntities?: EntityReferenceMap,
  chunkCitations?: ChunkCitationMap,
) {
  return render(
    <MemoryRouter>
      <ChatMarkdown
        content={content}
        referencedEntities={referencedEntities}
        chunkCitations={chunkCitations}
      />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Valid token ID helpers
// The entity ID pattern is ([a-zA-Z_]*[a-f0-9-]+), so IDs must end in hex
// chars or hyphens. Use UUID-style hex strings to guarantee pattern matching.
// ---------------------------------------------------------------------------
// Entity IDs (hex-style, matching [a-zA-Z_]*[a-f0-9-]+)
const ENTITY_ID_1 = 'abc-123';    // hex digits + hyphen → valid
const ENTITY_ID_2 = 'def-456';    // hex digits + hyphen → valid
const ENTITY_EDGE_ID = 'edge-def-456'; // edge id (hex)

// Chunk IDs used in [[cite:CHUNK_ID:Sn]] (hex UUID style)
const CHUNK_ID_A = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890';
const CHUNK_ID_B = 'b1c2d3e4-f5a6-7890-abcd-ef0987654321';
const CHUNK_ID_BQ = 'c1d2e3f4-a5b6-7890-abcd-ef1234509876';
const CHUNK_ID_PUNCT = 'd1e2f3a4-b5c6-7890-abcd-ef0123456789';
const CHUNK_ID_MULTI = 'e1f2a3b4-c5d6-7890-abcd-ef9876543210';

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ChatMarkdown', () => {
  // -------------------------------------------------------------------------
  // Plain markdown rendering
  // -------------------------------------------------------------------------
  describe('plain markdown', () => {
    it('renders a simple paragraph', () => {
      renderMarkdown('Hello world');
      expect(screen.getByText('Hello world')).toBeInTheDocument();
    });

    it('renders a heading', () => {
      renderMarkdown('# My Heading');
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('My Heading');
    });

    it('renders h2 and h3 headings', () => {
      renderMarkdown('## Two\n\n### Three');
      expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Two');
      expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent('Three');
    });

    it('renders bold text via strong element', () => {
      const { container } = renderMarkdown('Hello **bold** world');
      const strong = container.querySelector('strong');
      expect(strong).toBeInTheDocument();
      expect(strong?.textContent).toBe('bold');
    });

    it('renders italic text via em element', () => {
      const { container } = renderMarkdown('Hello _italic_ world');
      const em = container.querySelector('em');
      expect(em).toBeInTheDocument();
      expect(em?.textContent).toBe('italic');
    });

    it('renders an unordered list', () => {
      renderMarkdown('- Alpha\n- Beta\n- Gamma');
      expect(screen.getByText('Alpha')).toBeInTheDocument();
      expect(screen.getByText('Beta')).toBeInTheDocument();
      expect(screen.getByText('Gamma')).toBeInTheDocument();
    });

    it('renders an ordered list', () => {
      renderMarkdown('1. First\n2. Second');
      expect(screen.getByText('First')).toBeInTheDocument();
      expect(screen.getByText('Second')).toBeInTheDocument();
    });

    it('renders inline code', () => {
      const { container } = renderMarkdown('Use `console.log()` to debug');
      const code = container.querySelector('code');
      expect(code).toBeInTheDocument();
      expect(code?.textContent).toBe('console.log()');
    });

    it('renders fenced code block as pre/code', () => {
      const { container } = renderMarkdown('```\nconst x = 1;\n```');
      const pre = container.querySelector('pre');
      expect(pre).toBeInTheDocument();
      expect(pre?.textContent).toContain('const x = 1;');
    });

    it('renders a blockquote', () => {
      const { container } = renderMarkdown('> A quote');
      const bq = container.querySelector('blockquote');
      expect(bq).toBeInTheDocument();
      expect(bq?.textContent).toContain('A quote');
    });

    it('renders a markdown link with href and text', () => {
      const { container } = renderMarkdown('[Click here](https://example.com)');
      const anchor = container.querySelector('a');
      expect(anchor).toBeInTheDocument();
      expect(anchor?.getAttribute('href')).toBe('https://example.com');
      expect(anchor?.textContent).toBe('Click here');
    });

    it('renders without crashing on horizontal rule', () => {
      const { container } = renderMarkdown('Before\n\n---\n\nAfter');
      expect(container.textContent).toContain('Before');
      expect(container.textContent).toContain('After');
    });
  });

  // -------------------------------------------------------------------------
  // Empty and whitespace content
  // -------------------------------------------------------------------------
  describe('edge cases', () => {
    it('renders without crashing on empty string', () => {
      const { container } = renderMarkdown('');
      expect(container).toBeTruthy();
    });

    it('renders whitespace-only content without crashing', () => {
      const { container } = renderMarkdown('   \n  ');
      expect(container).toBeTruthy();
    });

    it('renders content with no entity or citation tokens normally', () => {
      renderMarkdown('Just plain text with no tokens.', {}, {});
      expect(screen.getByText('Just plain text with no tokens.')).toBeInTheDocument();
    });

    it('does not throw on content with no tokens', () => {
      expect(() => renderMarkdown('No tokens here at all.')).not.toThrow();
    });
  });

  // -------------------------------------------------------------------------
  // Entity reference tokens
  // -------------------------------------------------------------------------
  describe('entity reference tokens', () => {
    it('replaces [[node:ID|Label]] with EntityReference component', () => {
      renderMarkdown(`See [[node:${ENTITY_ID_1}|My Node]] here.`);
      const ref = screen.getByTestId('entity-ref');
      expect(ref).toBeInTheDocument();
      expect(ref).toHaveAttribute('data-entity-id', ENTITY_ID_1);
      expect(ref).toHaveAttribute('data-entity-type', 'node');
      expect(ref).toHaveTextContent('My Node');
    });

    it('replaces [[edge:ID|Label]] with EntityReference as edge type', () => {
      renderMarkdown(`Edge: [[edge:${ENTITY_EDGE_ID}|My Edge]] done.`);
      const ref = screen.getByTestId('entity-ref');
      expect(ref).toHaveAttribute('data-entity-type', 'edge');
      expect(ref).toHaveAttribute('data-entity-id', ENTITY_EDGE_ID);
      expect(ref).toHaveTextContent('My Edge');
    });

    it('uses enriched entity data from the referencedEntities map when available', () => {
      const referencedEntities: EntityReferenceMap = {
        [ENTITY_ID_1]: {
          id: ENTITY_ID_1,
          type: 'node',
          label: 'Enriched Label',
          description: 'A detailed description',
        },
      };
      renderMarkdown(`See [[node:${ENTITY_ID_1}|Fallback Label]] here.`, referencedEntities);
      const ref = screen.getByTestId('entity-ref');
      expect(ref).toHaveAttribute('data-entity-id', ENTITY_ID_1);
      // When enriched data is in the map, the entity label comes from the map
      expect(ref).toHaveTextContent('Enriched Label');
    });

    it('falls back to parsed label when entity is not in an empty map', () => {
      renderMarkdown(`Unknown [[node:${ENTITY_ID_1}|Parsed Label]] entity.`, {});
      const ref = screen.getByTestId('entity-ref');
      expect(ref).toHaveAttribute('data-entity-id', ENTITY_ID_1);
      expect(ref).toHaveTextContent('Parsed Label');
    });

    it('falls back to parsed label when no maps are provided at all', () => {
      renderMarkdown(`No maps [[node:${ENTITY_ID_1}|No Map Label]] here.`);
      const ref = screen.getByTestId('entity-ref');
      expect(ref).toHaveTextContent('No Map Label');
    });

    it('renders multiple entity references in one paragraph', () => {
      renderMarkdown(`[[node:${ENTITY_ID_1}|NodeA]] and [[node:${ENTITY_ID_2}|NodeB]]`);
      const refs = screen.getAllByTestId('entity-ref');
      expect(refs).toHaveLength(2);
      expect(refs[0]).toHaveTextContent('NodeA');
      expect(refs[1]).toHaveTextContent('NodeB');
    });

    it('preserves surrounding text around entity references', () => {
      renderMarkdown(`Before [[node:${ENTITY_ID_1}|X]] after`);
      expect(screen.getByText(/Before/)).toBeInTheDocument();
      expect(screen.getByText(/after/)).toBeInTheDocument();
    });

    it('handles entity token inside strong (bold) markdown', () => {
      // The entity token preprocessed before markdown sees it, so bold inside works
      // Bold around the placeholder text passes through
      renderMarkdown(`**Some [[node:${ENTITY_ID_1}|Bold Entity]] text**`);
      // The entity ref should render inside the strong
      expect(screen.getByTestId('entity-ref')).toBeInTheDocument();
    });

    it('handles entity reference with underscore separator syntax', () => {
      // Pattern supports [_:\-./\s] as separator; underscore is one of them
      // ID after underscore must match [a-zA-Z_]*[a-f0-9-]+
      renderMarkdown(`Ref [[node_${ENTITY_ID_1}|Label Here]] end.`);
      const ref = screen.getByTestId('entity-ref');
      expect(ref).toBeInTheDocument();
      expect(ref).toHaveTextContent('Label Here');
    });
  });

  // -------------------------------------------------------------------------
  // Chunk citation tokens
  // -------------------------------------------------------------------------
  describe('chunk citation tokens', () => {
    it('replaces [[cite:CHUNK_ID:S1|label]] with ChunkCitation (colon separator)', () => {
      renderMarkdown(`Check [[cite:${CHUNK_ID_A}:S1|Source File]] this.`);
      const cit = screen.getByTestId('chunk-citation');
      expect(cit).toBeInTheDocument();
      expect(cit).toHaveAttribute('data-chunk-id', CHUNK_ID_A);
      expect(cit).toHaveAttribute('data-sentence-refs', 'S1');
    });

    it('replaces [[cite:CHUNK_ID#Sn]] (hash separator) with ChunkCitation', () => {
      renderMarkdown(`Here [[cite:${CHUNK_ID_B}#S2]] done.`);
      const cit = screen.getByTestId('chunk-citation');
      expect(cit).toBeInTheDocument();
      expect(cit).toHaveAttribute('data-chunk-id', CHUNK_ID_B);
      expect(cit).toHaveAttribute('data-sentence-refs', 'S2');
    });

    it('uses enriched citation data from the chunkCitations map', () => {
      const citKey = `${CHUNK_ID_A}:S1`;
      const chunkCitations: ChunkCitationMap = {
        [citKey]: {
          chunk_id: CHUNK_ID_A,
          sentence_refs: 'S1',
          label: 'Enriched Source',
          source_id: 'src-1',
        },
      };
      renderMarkdown(`See [[cite:${CHUNK_ID_A}:S1|fallback]] here.`, {}, chunkCitations);
      const cit = screen.getByTestId('chunk-citation');
      // Label should come from the enriched map entry
      expect(cit).toHaveTextContent('Enriched Source');
    });

    it('falls back to parsed label when citation not in map', () => {
      renderMarkdown(`See [[cite:${CHUNK_ID_A}:S3|Fallback Label]] end.`, {}, {});
      const cit = screen.getByTestId('chunk-citation');
      expect(cit).toHaveTextContent('Fallback Label');
    });

    it('falls back to sentenceRefs as label when no label in token and no map entry', () => {
      renderMarkdown(`[[cite:${CHUNK_ID_A}#S5]]`, {}, {});
      const cit = screen.getByTestId('chunk-citation');
      expect(cit).toHaveAttribute('data-sentence-refs', 'S5');
      // label should fall back to sentenceRefs 'S5'
      expect(cit).toHaveTextContent('S5');
    });

    it('handles citation with multiple sentence refs (comma-separated)', () => {
      renderMarkdown(`[[cite:${CHUNK_ID_MULTI}:S1,S2,S3|Multi Source]]`, {}, {});
      const cit = screen.getByTestId('chunk-citation');
      expect(cit).toHaveAttribute('data-sentence-refs', 'S1,S2,S3');
    });

    it('renders multiple citations in the same content', () => {
      renderMarkdown(
        `Cite A [[cite:${CHUNK_ID_A}:S1|SrcA]] and cite B [[cite:${CHUNK_ID_B}:S2|SrcB]].`,
        {},
        {},
      );
      const cits = screen.getAllByTestId('chunk-citation');
      expect(cits).toHaveLength(2);
      expect(cits[0]).toHaveTextContent('SrcA');
      expect(cits[1]).toHaveTextContent('SrcB');
    });

    it('renders citation with sentence_text as a citation-blockquote', () => {
      const citKey = `${CHUNK_ID_BQ}:S1`;
      const chunkCitations: ChunkCitationMap = {
        [citKey]: {
          chunk_id: CHUNK_ID_BQ,
          sentence_refs: 'S1',
          label: 'BQ Source',
          sentence_text: 'This is the quoted sentence.',
        },
      };
      const { container } = renderMarkdown(
        `Ref [[cite:${CHUNK_ID_BQ}:S1|BQ Source]] end.`,
        {},
        chunkCitations,
      );
      // Should produce a blockquote.citation-blockquote wrapping the sentence
      const blockquotes = container.querySelectorAll('blockquote.citation-blockquote');
      expect(blockquotes.length).toBeGreaterThan(0);
      expect(blockquotes[0].textContent).toContain('This is the quoted sentence.');
    });

    it('renders a ChunkCitation chip inside the blockquote for sentence_text citations', () => {
      const citKey = `${CHUNK_ID_BQ}:S1`;
      const chunkCitations: ChunkCitationMap = {
        [citKey]: {
          chunk_id: CHUNK_ID_BQ,
          sentence_refs: 'S1',
          label: 'BQ Source',
          sentence_text: 'Quoted sentence here.',
        },
      };
      renderMarkdown(`[[cite:${CHUNK_ID_BQ}:S1|BQ Source]]`, {}, chunkCitations);
      // The ChunkCitation chip is rendered inside the blockquote
      expect(screen.getByTestId('chunk-citation')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Mixed entity + citation tokens
  // -------------------------------------------------------------------------
  describe('mixed entity and citation tokens', () => {
    it('renders both entity refs and chunk citations in the same content', () => {
      renderMarkdown(
        `Entity [[node:${ENTITY_ID_1}|MyNode]] and citation [[cite:${CHUNK_ID_A}:S1|MySrc]].`,
        {},
        {},
      );
      expect(screen.getByTestId('entity-ref')).toBeInTheDocument();
      expect(screen.getByTestId('chunk-citation')).toBeInTheDocument();
    });

    it('preserves surrounding text alongside mixed tokens', () => {
      renderMarkdown(
        `Start [[node:${ENTITY_ID_1}|Node]] middle [[cite:${CHUNK_ID_A}:S1|Cite]] end.`,
        {},
        {},
      );
      expect(screen.getByText(/Start/)).toBeInTheDocument();
      expect(screen.getByText(/end/)).toBeInTheDocument();
    });

    it('does not confuse entity and citation counter ordering', () => {
      renderMarkdown(
        `A [[node:${ENTITY_ID_1}|First]] B [[cite:${CHUNK_ID_A}:S1|Second]] C [[node:${ENTITY_ID_2}|Third]]`,
        {},
        {},
      );
      const refs = screen.getAllByTestId('entity-ref');
      const cits = screen.getAllByTestId('chunk-citation');
      expect(refs).toHaveLength(2);
      expect(cits).toHaveLength(1);
      expect(refs[0]).toHaveTextContent('First');
      expect(refs[1]).toHaveTextContent('Third');
      expect(cits[0]).toHaveTextContent('Second');
    });
  });

  // -------------------------------------------------------------------------
  // Malformed / unknown token syntax
  // -------------------------------------------------------------------------
  describe('malformed token handling', () => {
    it('renders unrecognised double-bracket syntax as plain text', () => {
      // [[foo:bar]] does not match either pattern — rendered literally
      const { container } = renderMarkdown('Hello [[foo:bar|baz]] world');
      expect(container.textContent).toContain('foo');
    });

    it('renders incomplete citation (missing close brackets) as plain text', () => {
      const { container } = renderMarkdown('Incomplete [[cite:abc:S1');
      expect(container.textContent).toContain('Incomplete');
    });

    it('renders content that looks like an entity but has wrong chars in ID', () => {
      // 'xyz-qrs' has 'q', 'r', 's' after the hyphen which don't match [a-f0-9]
      // so the full token won't be replaced — rendered as-is
      const { container } = renderMarkdown('Test [[node:xyz-qrs|Bad]] end');
      // Should be rendered without an entity ref component
      // (qrs are not hex: q, r, s are not in a-f)
      // Since it won't match, it appears as literal text
      expect(container).toBeTruthy();
    });
  });

  // -------------------------------------------------------------------------
  // useMemo branches: with vs without references
  // -------------------------------------------------------------------------
  describe('useMemo optimisation branches', () => {
    it('takes the fast-path (no preprocessing) when content has no reference tokens', () => {
      // When no tokens are present, processedContent === content and components === undefined.
      renderMarkdown('## Plain heading\n\nParagraph text here.');
      expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('Plain heading');
      expect(screen.getByText('Paragraph text here.')).toBeInTheDocument();
    });

    it('takes the reference-processing path when an entity token exists', () => {
      renderMarkdown(`Has [[node:${ENTITY_ID_1}|X]] token`);
      expect(screen.getByTestId('entity-ref')).toBeInTheDocument();
    });

    it('takes the reference-processing path when a citation token exists', () => {
      renderMarkdown(`Has [[cite:${CHUNK_ID_A}:S1|Y]] token`);
      expect(screen.getByTestId('chunk-citation')).toBeInTheDocument();
    });

    it('re-processes when content prop changes (entity present then absent)', () => {
      const { rerender } = renderMarkdown(`See [[node:${ENTITY_ID_1}|X]] here.`);
      expect(screen.getByTestId('entity-ref')).toBeInTheDocument();

      // Re-render with plain content — no entity ref should appear
      rerender(
        <MemoryRouter>
          <ChatMarkdown content="Just plain text." />
        </MemoryRouter>,
      );
      expect(screen.queryByTestId('entity-ref')).not.toBeInTheDocument();
      expect(screen.getByText('Just plain text.')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Custom renderers: p, strong, em, li, blockquote (renderChildren branches)
  // -------------------------------------------------------------------------
  describe('custom component renderers', () => {
    it('renders paragraph with entity reference inline (array children branch)', () => {
      // A paragraph containing text + entity forces the array-children branch
      renderMarkdown(`Text [[node:${ENTITY_ID_1}|Para Node]] more text.`);
      expect(screen.getByTestId('entity-ref')).toBeInTheDocument();
    });

    it('renders strong with entity reference inside', () => {
      renderMarkdown(`**[[node:${ENTITY_ID_1}|Strong Node]]**`);
      expect(screen.getByTestId('entity-ref')).toBeInTheDocument();
    });

    it('renders em with entity reference inside', () => {
      renderMarkdown(`_[[node:${ENTITY_ID_1}|Em Node]]_`);
      expect(screen.getByTestId('entity-ref')).toBeInTheDocument();
    });

    it('renders list item with entity reference inside', () => {
      renderMarkdown(`- [[node:${ENTITY_ID_1}|List Node]]`);
      expect(screen.getByTestId('entity-ref')).toBeInTheDocument();
    });

    it('renders blockquote with entity reference inside', () => {
      renderMarkdown(`> [[node:${ENTITY_ID_1}|Blockquote Node]]`);
      expect(screen.getByTestId('entity-ref')).toBeInTheDocument();
    });

    it('string-only paragraph children pass through without extra span wrappers', () => {
      // A paragraph with only a plain string — parts.length === 1 && typeof parts[0] === 'string'
      // so renderChildren returns the string directly without wrapping in <>
      const { container } = renderMarkdown('Simple plain paragraph.');
      const p = container.querySelector('p');
      expect(p).toBeInTheDocument();
      expect(p?.textContent).toBe('Simple plain paragraph.');
      // No extra <span> wrapper inside the <p>
      expect(p?.querySelector('span')).toBeNull();
    });

    it('renders a list item with citation inside', () => {
      renderMarkdown(`- Item with [[cite:${CHUNK_ID_A}:S1|Citation Src]]`);
      expect(screen.getByTestId('chunk-citation')).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Trailing punctuation after blockquote citation is stripped
  // -------------------------------------------------------------------------
  describe('blockquote citation punctuation stripping', () => {
    it('strips trailing period after a blockquote citation so it does not appear standalone', () => {
      const citKey = `${CHUNK_ID_PUNCT}:S1`;
      const chunkCitations: ChunkCitationMap = {
        [citKey]: {
          chunk_id: CHUNK_ID_PUNCT,
          sentence_refs: 'S1',
          label: 'Punct Source',
          sentence_text: 'Some quoted sentence.',
        },
      };
      const { container } = renderMarkdown(
        `[[cite:${CHUNK_ID_PUNCT}:S1|Punct Source]].`,
        {},
        chunkCitations,
      );
      const bq = container.querySelector('blockquote.citation-blockquote');
      expect(bq).toBeInTheDocument();
      // The stray period should not appear as a standalone sibling after blockquote
      const bqSibling = bq?.nextSibling;
      if (bqSibling) {
        expect(bqSibling.textContent?.trim()).not.toBe('.');
      }
    });

    it('renders the blockquote citation chip within the blockquote', () => {
      const citKey = `${CHUNK_ID_PUNCT}:S1`;
      const chunkCitations: ChunkCitationMap = {
        [citKey]: {
          chunk_id: CHUNK_ID_PUNCT,
          sentence_refs: 'S1',
          label: 'Chip Source',
          sentence_text: 'A quoted sentence for chip test.',
        },
      };
      const { container } = renderMarkdown(
        `[[cite:${CHUNK_ID_PUNCT}:S1|Chip Source]]`,
        {},
        chunkCitations,
      );
      const bq = container.querySelector('blockquote.citation-blockquote');
      expect(bq).toBeInTheDocument();
      // The ChunkCitation chip should be inside the blockquote
      const chip = bq?.querySelector('[data-testid="chunk-citation"]');
      expect(chip).toBeInTheDocument();
    });
  });
});
