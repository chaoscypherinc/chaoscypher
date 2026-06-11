// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useCallback } from 'react';
import { Box } from '@mui/material';
import ChatHeaderBar from '../components/ChatHeaderBar';
import { ScopePanel, ToolApprovalDialog } from '../components/chat';
import ChatMessageList from './chat/ChatMessageList';
import ChatInput from './chat/ChatInput';
import ContextPanel from './chat/ContextPanel';
import { useChat } from './chat/hooks/useChat';
import { useLLMHealth } from '../hooks/useLLMHealth';
import { ChaosCypherPalette } from '../theme/palette';

/** CSS keyframe animation for the "Apple Intelligence" aura glow behind the thinking card. */
const rainbowGlowStyles = `
  @keyframes rainbow-rotate {
    from {
      filter: hue-rotate(0deg);
    }
    to {
      filter: hue-rotate(360deg);
    }
  }

  .rainbow-glow-wrapper {
    position: relative;
    isolation: isolate;
    overflow: visible;
  }

  .rainbow-glow-wrapper::before {
    content: '';
    position: absolute;
    inset: -4px;
    border-radius: 6px;
    padding: 3px;
    background: conic-gradient(
      from 0deg,
      ${ChaosCypherPalette.primary},
      ${ChaosCypherPalette.accent},
      ${ChaosCypherPalette.purple},
      ${ChaosCypherPalette.secondary},
      ${ChaosCypherPalette.purple},
      ${ChaosCypherPalette.accent},
      ${ChaosCypherPalette.primary}
    );
    -webkit-mask:
      linear-gradient(#fff 0 0) content-box,
      linear-gradient(#fff 0 0);
    -webkit-mask-composite: xor;
    mask:
      linear-gradient(#fff 0 0) content-box,
      linear-gradient(#fff 0 0);
    mask-composite: exclude;
    z-index: -1;
    filter: blur(12px);
    opacity: 0.6;
    animation: rainbow-rotate 3s linear infinite;
    pointer-events: none;
  }
`;

/**
 * Main chat page component that composes the header bar, message list,
 * context/error panel, and input area into the full chat experience.
 *
 * All state management and business logic is delegated to the useChat hook.
 * This component is responsible only for layout and wiring sub-components.
 */
export default function ChatPage() {
  const {
    chats,
    currentChat,
    messages,
    input,
    loading,
    error,
    contextInfo,
    messagesEndRef,
    inputRef,
    setInput,
    clearError,
    handleSend,
    handleStop,
    handleRetry,
    handleRegenerate,
    startEditMessage,
    stopping,
    handleNewChat,
    handleSelectChat,
    handleRenameChat,
    handleDeleteChat,
    handleExportChat,
    handleClearAllChats,
    handleUpdateScope,
    handleClearScope,
    pendingScope,
    setPendingScope,
    pendingApproval,
    decideToolApproval,
    clearPendingApproval,
  } = useChat();
  const { data: llmHealth } = useLLMHealth();
  const missingModels = llmHealth?.missing_models ?? [];
  let chatDisabledReason: string | null = null;
  if (llmHealth && !llmHealth.verified) {
    chatDisabledReason = `Configure and verify your LLM (${llmHealth.provider}) in Settings to chat`;
  } else if (missingModels.length > 0) {
    chatDisabledReason =
      `Configured model${missingModels.length > 1 ? 's' : ''} not pulled: ` +
      `${missingModels.join(', ')}. Open Settings → LLM and pull, then retry.`;
  }

  const [scopePanelOpen, setScopePanelOpen] = useState(false);

  // Effective scope: current chat's scope or pending scope for new chat
  const effectiveScope = currentChat?.source_ids || (pendingScope.length > 0 ? pendingScope : []);

  // Scope panel handlers — for existing chats use API, for new chats use pending state
  const handlePanelUpdateScope = useCallback(async (sourceIds: string[]) => {
    if (currentChat) {
      await handleUpdateScope(sourceIds);
    } else {
      setPendingScope(sourceIds);
    }
  }, [currentChat, handleUpdateScope, setPendingScope]);

  const handlePanelClearScope = useCallback(async () => {
    if (currentChat) {
      await handleClearScope();
    } else {
      setPendingScope([]);
    }
  }, [currentChat, handleClearScope, setPendingScope]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', p: 0 }}>
      {/* Rainbow glow animation CSS */}
      <style>{rainbowGlowStyles}</style>

      {/* Header Bar */}
      <ChatHeaderBar
        chats={chats}
        currentChat={currentChat ? { id: currentChat.id, title: currentChat.title, source_ids: currentChat.source_ids } : null}
        onSelectChat={handleSelectChat}
        onNewChat={handleNewChat}
        onRenameChat={handleRenameChat}
        onDeleteChat={handleDeleteChat}
        onExportChat={handleExportChat}
        onClearAllChats={handleClearAllChats}
        onScopeBadgeClick={() => setScopePanelOpen(true)}
        pendingScopeCount={pendingScope.length}
      />

      {/* Scope Panel (works for both existing and new chats) */}
      <ScopePanel
        open={scopePanelOpen}
        onClose={() => setScopePanelOpen(false)}
        sourceIds={effectiveScope}
        onUpdateScope={handlePanelUpdateScope}
        onClearScope={handlePanelClearScope}
      />

      {/* Error / Context Panel */}
      <ContextPanel
        error={error}
        onClearError={clearError}
        messages={messages}
        onRetry={(message) => setInput(message)}
        onRetryTurn={() => handleRetry()}
      />

      {/* Messages */}
      <ChatMessageList
        messages={messages}
        loading={loading}
        chatStatus={currentChat?.status}
        contextInfo={contextInfo}
        onQuickAction={handleSend}
        onRegenerate={() => handleRegenerate()}
        onEditMessage={startEditMessage}
        messagesEndRef={messagesEndRef}
      />

      {/* Input */}
      <ChatInput
        input={input}
        loading={loading}
        contextInfo={contextInfo}
        inputRef={inputRef}
        onInputChange={setInput}
        onSend={() => handleSend()}
        onStop={() => handleStop()}
        stopping={stopping}
        disabledReason={chatDisabledReason}
      />

      {/* Tool-approval dialog (opens when the backend emits
          tool_approval_required for a mutating tool call). */}
      {pendingApproval && (
        <ToolApprovalDialog
          open={true}
          toolCallId={pendingApproval.tool_call_id}
          toolName={pendingApproval.tool_name}
          arguments={pendingApproval.arguments}
          onDecide={decideToolApproval}
          onClose={clearPendingApproval}
        />
      )}
    </Box>
  );
}
