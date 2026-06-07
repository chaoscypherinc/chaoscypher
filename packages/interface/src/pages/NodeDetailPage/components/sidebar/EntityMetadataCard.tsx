// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Chip, Typography } from '@mui/material';
import type { Node, Template } from '../../../../types';
import TemplateIcon from '../../../../components/TemplateIcon';
import MetadataCard from '../../../../components/detail/MetadataCard';
import MetadataRow from '../../../../components/detail/MetadataRow';

interface EntityMetadataCardProps {
  entity: Node;
  template: Template | null;
}

function provenanceString(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  return String(value);
}

/**
 * Collapsible metadata card for an entity. Collapsed it shows just the
 * template and source-document name; expanded it reveals the full provenance
 * (IDs, source document, extraction timestamps) and audit timestamps so the
 * Details tab can stay focused on clean extracted data.
 */
export default function EntityMetadataCard({ entity, template }: EntityMetadataCardProps) {
  const props = entity.properties ?? {};
  const docName = provenanceString(props.source_document_name);
  const docId = provenanceString(props.source_document_id);
  const sourceType = provenanceString(props.source_type);
  const ingestedAt = provenanceString(props.ingested_at);
  const extractedAt = provenanceString(props.extracted_at);
  const subtype = typeof props.entity_subtype === 'string' ? props.entity_subtype : null;

  const templateName = template?.name || entity.template_id;

  const summary = (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
        <TemplateIcon
          template={template}
          fallbackTemplateId={entity.template_id}
          size={16}
          containerSize={16}
        />
        <Typography variant="body2" sx={{ color: 'text.primary' }} noWrap>
          {templateName}
        </Typography>
      </Box>
      {docName && (
        <Typography
          variant="caption"
          sx={{ color: 'text.secondary', display: 'block', mt: 0.5 }}
          noWrap
          title={docName}
        >
          {docName}
        </Typography>
      )}
    </Box>
  );

  return (
    <MetadataCard collapsible summary={summary}>
      <MetadataRow label="ID">
        <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>
          {entity.id}
        </Typography>
      </MetadataRow>
      <MetadataRow label="Template">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mt: 0.5 }}>
          <TemplateIcon
            template={template}
            fallbackTemplateId={entity.template_id}
            size={16}
            containerSize={16}
          />
          <Typography variant="body2" sx={{ color: 'text.primary' }}>
            {templateName}
          </Typography>
        </Box>
      </MetadataRow>
      {subtype && (
        <MetadataRow label="Subtype">
          <Chip
            label={subtype}
            size="small"
            variant="outlined"
            color="primary"
            title="entity_subtype — original type before domain canonicalization"
          />
        </MetadataRow>
      )}
      {docName && (
        <MetadataRow label="Source Document">
          <Typography variant="body2">{docName}</Typography>
        </MetadataRow>
      )}
      {docId && (
        <MetadataRow label="Source Document ID">
          <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>
            {docId}
          </Typography>
        </MetadataRow>
      )}
      {sourceType && (
        <MetadataRow label="Source Type">
          <Typography variant="body2">{sourceType}</Typography>
        </MetadataRow>
      )}
      {ingestedAt && (
        <MetadataRow label="Ingested">
          <Typography variant="body2">{formatProvenanceDate(ingestedAt)}</Typography>
        </MetadataRow>
      )}
      {extractedAt && (
        <MetadataRow label="Extracted">
          <Typography variant="body2">{formatProvenanceDate(extractedAt)}</Typography>
        </MetadataRow>
      )}
      <MetadataRow label="Created">
        <Typography variant="body2">{new Date(entity.created_at).toLocaleString()}</Typography>
      </MetadataRow>
      {entity.updated_at && (
        <MetadataRow label="Updated">
          <Typography variant="body2">{new Date(entity.updated_at).toLocaleString()}</Typography>
        </MetadataRow>
      )}
      {entity.tags && entity.tags.length > 0 && (
        <MetadataRow label="Tags">
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.5 }}>
            {entity.tags.map((tag, index) => (
              <Chip key={index} label={tag} size="small" variant="outlined" />
            ))}
          </Box>
        </MetadataRow>
      )}
    </MetadataCard>
  );
}

/** Render an ISO timestamp as a localized string, falling back to the raw value. */
function formatProvenanceDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
