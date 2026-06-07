# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat Executor - AI Response Generation.

Executes chat message processing and AI response generation.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.ports.llm import TaskType
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.ports.storage_chats import ChatStorageProtocol
    from chaoscypher_core.ports.types import MessageDict

logger = structlog.get_logger(__name__)


class ChatExecutor:
    """Executes chat message processing and AI response generation.

    Handles the complete chat workflow:
    - Save user messages
    - Load chat history
    - Generate AI responses via LLM
    - Save assistant responses
    """

    def __init__(
        self,
        chat_storage: ChatStorageProtocol,
        llm_service: Any,  # LLM service interface (backend-specific)
        settings: Settings,
    ):
        """Initialize chat processor.

        Args:
            chat_storage: Storage protocol for chat data access
            llm_service: LLM service for AI operations
            settings: Application settings

        """
        self.chat_storage = chat_storage
        self.llm_service = llm_service
        self.settings = settings

    async def process_user_message(
        self, chat_id: str, user_message: str, user_id: int | None = None
    ) -> MessageDict | None:
        """Process user message and generate AI response.

        This method:
        1. Saves the user message to the database
        2. Loads chat history
        3. Builds messages array for LLM (system + history + new user message)
        4. Calls LLM via queue (chat_completion_operation)
        5. Waits for result
        6. Saves assistant response to database
        7. Updates chat status

        Args:
            chat_id: Chat ID
            user_message: User's message content
            user_id: Optional user ID for access control

        Returns:
            Assistant message dict or None if processing failed

        """
        try:
            # 1. Save user message to database
            user_message_id = generate_id()

            user_msg_dict = {
                "id": user_message_id,
                "chat_id": chat_id,
                "role": "user",
                "content": user_message,
                "extra_metadata": None,
                "timestamp": datetime.now(UTC),
            }

            saved_user_msg = self.chat_storage.create_message(user_msg_dict)

            if not saved_user_msg:
                logger.exception("chat_user_message_save_failed", chat_id=chat_id)
                return None

            logger.info("chat_user_message_saved", chat_id=chat_id, message_id=user_message_id)

            # 2. Update chat status to 'processing'
            self.chat_storage.update_chat(
                chat_id=chat_id, updates={"status": "processing", "updated_at": datetime.now(UTC)}
            )

            # 3. Load chat history (all messages)
            messages = self.chat_storage.get_messages(chat_id=chat_id)

            # 4. Build messages array for LLM
            llm_messages = self._build_llm_messages(messages)

            logger.info("chat_llm_messages_built", chat_id=chat_id, message_count=len(llm_messages))

            # 5. Queue LLM chat completion operation
            # Using the LLMQueueService queue system
            # TaskType import removed - engine uses direct LLM calls

            # Get priority and timeout from settings
            priority = self.settings.priorities.interactive
            timeout = self.settings.timeouts.llm_chat_wait

            task_id = await self.llm_service.queue_operation(
                task_type=TaskType.CHAT,
                operation_name="chat_completion",
                messages=llm_messages,
                priority=priority,  # High priority for interactive chat
                metadata={"chat_id": chat_id, "user_id": user_id, "type": "chat_response"},
                stream=False,  # Non-streaming for simplicity
                enable_thinking=self.settings.llm.thinking_for_chat,
            )

            logger.info("chat_llm_task_queued", task_id=task_id, chat_id=chat_id)

            # 6. Wait for result
            result = await self.llm_service.wait_for_result(task_id, timeout=timeout)

            if not result or not result.get("content"):
                msg = "Empty response from LLM"
                raise LLMError(msg)

            assistant_content = result.get("content", "")
            usage = result.get("usage", {})

            logger.info(
                "chat_llm_response_received",
                chat_id=chat_id,
                content_length=len(assistant_content),
                total_tokens=usage.get("total_tokens", 0),
            )

            # 7. Save assistant message to database
            assistant_message_id = generate_id()

            assistant_msg_dict = {
                "id": assistant_message_id,
                "chat_id": chat_id,
                "role": "assistant",
                "content": assistant_content,
                "extra_metadata": {
                    "usage": usage,
                    "provider": result.get("provider"),
                    "task_id": task_id,
                },
                "timestamp": datetime.now(UTC),
            }

            saved_assistant_msg = self.chat_storage.create_message(assistant_msg_dict)

            if not saved_assistant_msg:
                logger.exception("chat_assistant_message_save_failed", chat_id=chat_id)
                # Mark chat as error
                self.chat_storage.update_chat(
                    chat_id=chat_id, updates={"status": "error", "updated_at": datetime.now(UTC)}
                )
                return None

            # 8. Update chat status to 'active'
            self.chat_storage.update_chat(
                chat_id=chat_id, updates={"status": "active", "updated_at": datetime.now(UTC)}
            )

            logger.info(
                "chat_assistant_message_saved", chat_id=chat_id, message_id=assistant_message_id
            )

            # 9. Return assistant message as dict
            return saved_assistant_msg

        except Exception as e:
            logger.exception(
                "chat_process_message_failed",
                chat_id=chat_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )

            # Update chat status to 'error'
            try:
                self.chat_storage.update_chat(
                    chat_id=chat_id, updates={"status": "error", "updated_at": datetime.now(UTC)}
                )
            except Exception as status_error:
                logger.exception(
                    "chat_status_update_failed",
                    chat_id=chat_id,
                    error_type=type(status_error).__name__,
                    error_message=str(status_error),
                )

            return None

    def _build_llm_messages(self, messages: list[MessageDict]) -> list[dict[str, str]]:
        """Build messages array for LLM from chat history.

        Args:
            messages: List of message dictionaries

        Returns:
            List of message dicts with 'role' and 'content'

        """
        # System message for ChaosCypher assistant
        system_message = {
            "role": "system",
            "content": (
                "You are ChaosCypher, an AI assistant specializing in knowledge graph construction and analysis. "
                "You help users build, explore, and understand complex knowledge graphs. "
                "You can answer questions, provide insights, and assist with graph-related tasks. "
                "Be helpful, accurate, and concise in your responses."
            ),
        }

        # Build messages array: [system, user, assistant, user, assistant, ...]
        llm_messages = [system_message]
        llm_messages.extend(
            {"role": msg.get("role") or "", "content": msg.get("content") or ""} for msg in messages
        )

        return llm_messages
