// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Command registry for the omnibar command mode.
 * Defines all available commands with fuzzy matching support.
 */
import type { OmnibarCommand } from '../types';

interface CommandDeps {
  navigate: (path: string) => void;
  openUploadDialog: () => void;
  notify: (message: string, severity?: string) => void;
  rebuildIndexes: () => Promise<unknown>;
}

/** Build the full command registry with injected dependencies. */
export function buildCommandRegistry(deps: CommandDeps): OmnibarCommand[] {
  const { navigate, openUploadDialog, notify, rebuildIndexes } = deps;

  return [
    // ── Navigation ──
    {
      id: 'nav-dashboard',
      label: 'Go to Dashboard',
      description: 'Open the main dashboard',
      keywords: ['home', 'overview'],
      icon: '🏠',
      category: 'navigation',
      action: () => navigate('/'),
    },
    {
      id: 'nav-graph',
      label: 'Go to Graph',
      description: 'Open the knowledge graph canvas',
      keywords: ['canvas', 'visualization', 'network'],
      icon: '🕸️',
      category: 'navigation',
      action: () => navigate('/graph'),
    },
    {
      id: 'nav-sources',
      label: 'Go to Sources',
      description: 'Browse uploaded source documents',
      keywords: ['files', 'documents', 'uploads'],
      icon: '📁',
      category: 'navigation',
      action: () => navigate('/sources'),
    },
    {
      id: 'nav-chat',
      label: 'Go to Chat',
      description: 'Open the AI chat interface',
      keywords: ['conversation', 'ai', 'ask'],
      icon: '💬',
      category: 'navigation',
      action: () => navigate('/chat'),
    },
    {
      id: 'nav-nodes',
      label: 'Go to Nodes',
      description: 'Browse all knowledge graph entities',
      keywords: ['entities', 'items'],
      icon: '🔵',
      category: 'navigation',
      action: () => navigate('/nodes'),
    },
    {
      id: 'nav-edges',
      label: 'Go to Edges',
      description: 'Browse all knowledge graph relationships',
      keywords: ['links', 'relationships', 'connections'],
      icon: '🔗',
      category: 'navigation',
      action: () => navigate('/edges'),
    },
    {
      id: 'nav-templates',
      label: 'Go to Templates',
      description: 'Manage entity and relationship templates',
      keywords: ['types', 'schemas'],
      icon: '📋',
      category: 'navigation',
      action: () => navigate('/templates'),
    },
    {
      id: 'nav-automations',
      label: 'Go to Automations',
      description: 'Manage workflows and automations',
      keywords: ['workflows', 'pipelines'],
      icon: '⚡',
      category: 'navigation',
      action: () => navigate('/automations'),
    },
    {
      id: 'nav-settings',
      label: 'Go to Settings',
      description: 'Open application settings',
      keywords: ['config', 'preferences', 'configuration'],
      icon: '⚙️',
      category: 'navigation',
      action: () => navigate('/settings'),
    },
    {
      id: 'nav-queues',
      label: 'Go to Queue Monitor',
      description: 'View background job queue status',
      keywords: ['jobs', 'tasks', 'workers', 'queue'],
      icon: '📊',
      category: 'navigation',
      action: () => navigate('/queues'),
    },
    {
      id: 'nav-lexicon',
      label: 'Go to Lexicon',
      description: 'Browse the knowledge lexicon',
      keywords: ['dictionary', 'glossary'],
      icon: '📖',
      category: 'navigation',
      action: () => navigate('/lexicon'),
    },
    // ── Settings sub-pages ──
    {
      id: 'nav-settings-models',
      label: 'LLM / Model Settings',
      description: 'Configure AI providers, models, and parameters',
      keywords: ['llm', 'provider', 'ollama', 'openai', 'anthropic', 'gemini', 'temperature'],
      icon: '🤖',
      category: 'navigation',
      action: () => navigate('/settings?tab=models'),
    },
    {
      id: 'nav-settings-search',
      label: 'Search Settings',
      description: 'Configure search, embeddings, and RAG parameters',
      keywords: ['rag', 'embeddings', 'vectors', 'fts', 'full-text', 'index'],
      icon: '🔍',
      category: 'navigation',
      action: () => navigate('/settings?tab=search'),
    },
    {
      id: 'nav-settings-access',
      label: 'Access Settings',
      description: 'Manage authentication, API keys, and TLS',
      keywords: ['auth', 'security', 'api key', 'tls', 'ssl', 'users', 'password'],
      icon: '🔐',
      category: 'navigation',
      action: () => navigate('/settings?tab=general&section=account'),
    },
    // ── Actions ──
    {
      id: 'action-new-chat',
      label: 'New Chat',
      description: 'Start a new AI conversation',
      keywords: ['create', 'conversation', 'ask'],
      icon: '💬',
      category: 'action',
      action: () => navigate('/chat'),
    },
    {
      id: 'action-import-source',
      label: 'Import Source',
      description: 'Upload a new source document',
      keywords: ['upload', 'add', 'file', 'document'],
      icon: '📥',
      category: 'action',
      action: () => openUploadDialog(),
    },
    {
      id: 'action-rebuild-index',
      label: 'Rebuild Search Index',
      description: 'Rebuild full-text and vector search indexes',
      keywords: ['reindex', 'search', 'vectors', 'embeddings'],
      icon: '🔄',
      category: 'action',
      action: () => {
        rebuildIndexes()
          .then(() => notify('Search index rebuild started', 'success'))
          .catch(() => notify('Failed to start index rebuild', 'error'));
      },
    },
  ];
}

/** Fuzzy-match a query against a command. Returns a score (0 = no match). */
export function matchCommand(command: OmnibarCommand, query: string): number {
  if (!query) return 1;

  const lowerQuery = query.toLowerCase();
  const labelLower = command.label.toLowerCase();
  const descLower = command.description.toLowerCase();

  if (labelLower.startsWith(lowerQuery)) return 100;
  if (labelLower.includes(lowerQuery)) return 80;

  for (const kw of command.keywords) {
    if (kw.toLowerCase().startsWith(lowerQuery)) return 60;
  }

  if (descLower.includes(lowerQuery)) return 40;

  return 0;
}
