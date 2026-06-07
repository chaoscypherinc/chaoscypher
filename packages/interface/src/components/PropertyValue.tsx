// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Type-aware property value renderer.
 *
 * Renders property values with smart formatting based on detected type:
 * - URL: External link with icon
 * - Email: mailto link
 * - Date: Relative time with absolute tooltip
 * - Boolean: Colored chip
 * - Number: Right-aligned, formatted
 * - Array: Chips with "+N more"
 * - Object: Collapsible JSON
 * - Long text: Truncated with expand
 * - Text: Plain display
 */
import React, { useState } from 'react';
import {
  Box,
  Chip,
  IconButton,
  Link,
  Tooltip,
  Typography,
  Collapse,
  Paper,
} from '@mui/material';
import ExternalLinkIcon from '@mui/icons-material/OpenInNew';
import EmailIcon from '@mui/icons-material/Email';
import ExpandIcon from '@mui/icons-material/ExpandMore';
import CollapseIcon from '@mui/icons-material/ExpandLess';
import TrueIcon from '@mui/icons-material/Check';
import FalseIcon from '@mui/icons-material/Close';
import { detectValueType } from '../utils/typeDetection';
import {
  formatNumber,
  formatRelativeDate,
  formatAbsoluteDate,
  truncateUrl,
  truncateText,
} from '../utils/formatters';

interface PropertyValueProps {
  value: unknown;
  /** Property key (for potential future use in context-aware rendering) */
  propertyKey?: string;
  /** Maximum items to show for arrays before "+N more" */
  maxArrayItems?: number;
  /** Maximum length for long text before truncation */
  maxTextLength?: number;
}

function PropertyValue({
  value,
  propertyKey: _propertyKey,
  maxArrayItems = 3,
  maxTextLength = 100,
}: PropertyValueProps) {
  const [expanded, setExpanded] = useState(false);
  const valueType = detectValueType(value);

  // URL
  if (valueType === 'url') {
    const url = value as string;
    return (
      <Link
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 0.5,
          maxWidth: '100%',
          wordBreak: 'break-all',
        }}
      >
        <Typography
          variant="body2"
          component="span"
          sx={{ overflow: 'hidden', textOverflow: 'ellipsis' }}
        >
          {truncateUrl(url)}
        </Typography>
        <ExternalLinkIcon fontSize="small" sx={{ flexShrink: 0 }} />
      </Link>
    );
  }

  // Email
  if (valueType === 'email') {
    const email = value as string;
    return (
      <Link
        href={`mailto:${email}`}
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 0.5,
        }}
      >
        <EmailIcon fontSize="small" />
        <Typography variant="body2" component="span">
          {email}
        </Typography>
      </Link>
    );
  }

  // Date
  if (valueType === 'date') {
    const date = new Date(value as string);
    return (
      <Tooltip title={formatAbsoluteDate(date)} arrow>
        <Typography variant="body2" component="span" sx={{ cursor: 'help' }}>
          {formatRelativeDate(date)}
        </Typography>
      </Tooltip>
    );
  }

  // Boolean
  if (valueType === 'boolean') {
    const boolValue = value as boolean;
    return (
      <Chip
        icon={boolValue ? <TrueIcon /> : <FalseIcon />}
        label={boolValue ? 'True' : 'False'}
        size="small"
        color={boolValue ? 'success' : 'default'}
        variant="outlined"
        sx={{ height: 24 }}
      />
    );
  }

  // Number
  if (valueType === 'number') {
    return (
      <Typography
        variant="body2"
        component="span"
        sx={{ fontFamily: 'monospace', textAlign: 'right' }}
      >
        {formatNumber(value as number)}
      </Typography>
    );
  }

  // Array
  if (valueType === 'array') {
    const arr = value as unknown[];
    const showItems = arr.slice(0, maxArrayItems);
    const remaining = arr.length - maxArrayItems;

    return (
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, alignItems: 'center' }}>
        {showItems.map((item, index) => (
          <Chip
            key={index}
            label={typeof item === 'object' ? JSON.stringify(item) : String(item)}
            size="small"
            variant="outlined"
            sx={{ maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis' }}
          />
        ))}
        {remaining > 0 && (
          <Tooltip
            title={
              <Box>
                {arr.slice(maxArrayItems).map((item, i) => (
                  <Typography key={i} variant="body2">
                    {typeof item === 'object' ? JSON.stringify(item) : String(item)}
                  </Typography>
                ))}
              </Box>
            }
            arrow
          >
            <Chip
              label={`+${remaining} more`}
              size="small"
              color="primary"
              variant="outlined"
              sx={{ cursor: 'help' }}
            />
          </Tooltip>
        )}
      </Box>
    );
  }

  // Object
  if (valueType === 'object') {
    const json = JSON.stringify(value, null, 2);
    const isLong = json.length > 100;

    return (
      <Box>
        {isLong ? (
          <>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography variant="body2" sx={{
                color: "text.secondary"
              }}>
                {Object.keys(value as object).length} keys
              </Typography>
              <IconButton aria-label={expanded ? "Collapse" : "Expand"} size="small" onClick={() => setExpanded(!expanded)}>
                {expanded ? <CollapseIcon /> : <ExpandIcon />}
              </IconButton>
            </Box>
            <Collapse in={expanded}>
              <Paper
                variant="outlined"
                sx={{
                  mt: 1,
                  p: 1,
                  bgcolor: 'background.default',
                  maxHeight: 300,
                  overflow: 'auto',
                }}
              >
                <Typography
                  variant="body2"
                  component="pre"
                  sx={{ fontFamily: 'monospace', m: 0, whiteSpace: 'pre-wrap' }}
                >
                  {json}
                </Typography>
              </Paper>
            </Collapse>
          </>
        ) : (
          <Typography
            variant="body2"
            component="pre"
            sx={{ fontFamily: 'monospace', m: 0, whiteSpace: 'pre-wrap' }}
          >
            {json}
          </Typography>
        )}
      </Box>
    );
  }

  // Long text
  if (valueType === 'longText') {
    const text = value as string;
    const truncated = truncateText(text, maxTextLength);
    const isTruncated = text.length > maxTextLength;

    return (
      <Box>
        {isTruncated && !expanded ? (
          <>
            <Typography variant="body2">{truncated}</Typography>
            <Link
              component="button"
              variant="body2"
              onClick={() => setExpanded(true)}
              sx={{ mt: 0.5 }}
            >
              Show more
            </Link>
          </>
        ) : (
          <>
            <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
              {text}
            </Typography>
            {isTruncated && (
              <Link
                component="button"
                variant="body2"
                onClick={() => setExpanded(false)}
                sx={{ mt: 0.5 }}
              >
                Show less
              </Link>
            )}
          </>
        )}
      </Box>
    );
  }

  // Default: text
  return (
    <Typography variant="body2" component="span">
      {value === null || value === undefined ? '-' : String(value)}
    </Typography>
  );
}

export default React.memo(PropertyValue);
