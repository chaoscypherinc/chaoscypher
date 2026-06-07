# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain exceptions for Chaos Cypher Engine.

These are framework-agnostic exceptions that represent business logic errors.
The API layer converts these to appropriate HTTP responses.

Usage in services:
    from chaoscypher_core.exceptions import NotFoundError, ValidationError

    def get_workflow(self, workflow_id: str):
        workflow = self.repository.get(workflow_id)
        if not workflow:
            raise NotFoundError("Workflow", workflow_id)
        return workflow

The API layer will automatically convert NotFoundError to HTTP 404.
"""


class ChaosCypherException(Exception):
    """Base exception for all ChaosCypher domain errors.

    All domain exceptions should inherit from this class.
    This allows the API layer to distinguish between domain errors
    and unexpected system errors.
    """

    def __init__(self, message: str, code: str = "INTERNAL_ERROR", details: dict | None = None):
        """Initialize the ChaosCypherError.

        Args:
            message: Human-readable error message
            code: Machine-readable error code (for API responses)
            details: Additional context (dict with any relevant data).

        """
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)


class NotFoundError(ChaosCypherException):
    """Resource not found.

    Maps to HTTP 404.

    Example:
        raise NotFoundError("Workflow", workflow_id)
        # → HTTP 404: {"error": "NOT_FOUND", "message": "Workflow not found: abc123"}

    """

    def __init__(self, resource_type: str, identifier: str):
        """Initialize the instance.

        Args:
            resource_type: Type of resource that was not found.
            identifier: Unique identifier of the resource.

        """
        super().__init__(
            message=f"{resource_type} not found: {identifier}",
            code="NOT_FOUND",
            details={"resource_type": resource_type, "identifier": identifier},
        )
        self.resource_type = resource_type
        self.identifier = identifier


class ValidationError(ChaosCypherException):
    """Invalid input or business rule violation.

    Maps to HTTP 400.

    Example:
        raise ValidationError("Workflow name is required", field="name")
        # → HTTP 400: {"error": "VALIDATION_ERROR", "message": "Workflow name is required"}

    """

    def __init__(self, message: str, field: str | None = None, details: dict | None = None):
        """Initialize the instance.

        Args:
            message: Human-readable validation error message.
            field: Name of the field that failed validation.
            details: Additional context about the validation error.

        """
        error_details = details or {}
        if field:
            error_details["field"] = field

        super().__init__(message=message, code="VALIDATION_ERROR", details=error_details)
        self.field = field


class TriggerValidationError(ValidationError):
    """Trigger configuration failed validation (filter bounds, etc.).

    Subclass of ValidationError so existing HTTP 400 mapping continues
    to apply. Raised from TriggerService when the trigger payload fails
    static validation at save time.
    """


class SchemaValidationError(ValidationError):
    """Workflow output failed JSON-Schema validation.

    Maps to HTTP 400.
    """

    def __init__(
        self,
        message: str,
        path: list[str] | None = None,
        details: dict | None = None,
    ):
        """Initialize the instance.

        Args:
            message: Human-readable error message.
            path: JSON-Schema path to the failing field (list of str).
            details: Additional context.

        """
        error_details = details or {}
        if path is not None:
            error_details["path"] = path
        super().__init__(message=message, details=error_details)
        self.path = path or []


class DataIntegrityError(ChaosCypherException):
    """Persisted data failed schema validation, indicating drift or corruption.

    Distinct from ``ValidationError`` (which guards user-supplied input at the
    API boundary). ``DataIntegrityError`` fires when *already-persisted* data
    no longer matches the expected schema — for example, a ``ChunkExtractionTask``
    JSON column whose entity/relationship dicts drifted from the canonical
    Pydantic shape between writer and reader.

    Maps to HTTP 500 (Internal Server Error). Surfacing this to the API layer
    is intentional: data drift is a server-side bug, not a client error.

    Example:
        raise DataIntegrityError(
            "Chunk task chunk_42 raw_entities[3] missing required field 'name'",
            details={"chunk_task_id": "task_42", "field": "raw_entities[3]"},
        )

    """

    def __init__(self, message: str, details: dict | None = None):
        """Initialize the instance.

        Args:
            message: Human-readable description of the integrity violation.
            details: Additional context (e.g., chunk_task_id, field path).

        """
        super().__init__(message=message, code="DATA_INTEGRITY_ERROR", details=details or {})


class SchemaIntegrityError(ChaosCypherException):
    """Live DB schema drifted from the SQLModel metadata at startup.

    Raised by the startup schema-drift gate in
    :func:`chaoscypher_core.database.engine.init_database` when
    ``settings.database.strict_schema_drift`` is true and Alembic's
    ``produce_migrations`` reports any diff between the freshly-migrated
    DB and the live ``SQLModel.metadata``. Refusing to boot in this mode
    keeps a release that shipped without CI from silently landing in a
    state where the next feature query crashes on a missing column.

    Maps to HTTP 500 (Internal Server Error). This is a deployment
    integrity issue, not a client error.

    Example:
        raise SchemaIntegrityError(
            "Schema drift detected after migrations",
            details={"diffs": [...summary entries...]},
        )

    """

    def __init__(self, message: str, details: dict | None = None):
        """Initialize the instance.

        Args:
            message: Human-readable description of the drift.
            details: Additional context (e.g., the list of diffs).

        """
        super().__init__(message=message, code="SCHEMA_INTEGRITY_ERROR", details=details or {})


class InvalidStateError(ChaosCypherException):
    """Operation attempted on an entity in an incompatible state.

    Maps to HTTP 409 Conflict.

    Example:
        raise InvalidStateError("Source src_123 is already committed; reset before re-extracting.")
        # → HTTP 409: {"error": "INVALID_STATE", "message": "Source src_123 is already committed..."}

    """

    def __init__(self, message: str, details: dict | None = None):
        """Initialize the instance.

        Args:
            message: Human-readable description of the invalid state.
            details: Additional context about the state conflict.

        """
        super().__init__(message=message, code="INVALID_STATE", details=details or {})


class LLMNotVerifiedError(ChaosCypherException):
    """User-initiated action refused because the LLM provider has not been verified.

    Maps to HTTP 409. Raised by the import and chat-send endpoints when
    the currently-selected chat provider has no successful verify on
    record — the frontend listens for code ``LLM_NOT_VERIFIED`` to show
    a "Configure your LLM" prompt with a Settings deeplink.
    """

    def __init__(self, provider: str):
        """Initialize the instance.

        Args:
            provider: The chat provider that lacks a successful verify.

        """
        super().__init__(
            message=(
                f"LLM provider {provider!r} has not been verified. Open Settings → LLM "
                f"and click Test to verify your connection, then retry."
            ),
            code="LLM_NOT_VERIFIED",
            details={"provider": provider},
        )
        self.provider = provider


class ExtractionModelMissingError(ChaosCypherException):
    """User-initiated action refused because configured models aren't pulled.

    Maps to HTTP 409. Raised by the import and chat-send endpoints when
    one or more of the configured Ollama models (chat / extraction /
    vision) is not present on any reachable Ollama instance. The
    frontend listens for code ``EXTRACTION_MODEL_MISSING`` to surface a
    "Pull these models" prompt with the missing list and a Settings
    deeplink.
    """

    def __init__(self, provider: str, missing_models: tuple[str, ...] | list[str]):
        """Initialize the instance.

        Args:
            provider: The chat provider with missing models (currently
                only ``"ollama"`` — cloud providers don't trigger this).
            missing_models: The configured model names not present on
                any reachable instance.
        """
        models = tuple(missing_models)
        super().__init__(
            message=(
                f"Configured {provider} model(s) not pulled: "
                f"{', '.join(models) or '<none>'}. Open Settings → LLM "
                f"and click Pull next to each missing model, then retry."
            ),
            code="EXTRACTION_MODEL_MISSING",
            details={"provider": provider, "missing_models": list(models)},
        )
        self.provider = provider
        self.missing_models = models


class ConflictError(ChaosCypherException):
    """Resource conflict (e.g., duplicate unique constraint).

    Maps to HTTP 409.

    Example:
        raise ConflictError(f"Workflow with name '{name}' already exists")
        # → HTTP 409: {"error": "CONFLICT", "message": "Workflow with name 'my-workflow' already exists"}

    """

    def __init__(self, message: str, details: dict | None = None):
        """Initialize the instance.

        Args:
            message: Human-readable conflict error message.
            details: Additional context about the conflict.

        """
        super().__init__(message=message, code="CONFLICT", details=details or {})


class WorkflowBusyError(ConflictError):
    """Workflow has allow_parallel_execution=False and already has an active run.

    Maps to HTTP 409.
    """

    def __init__(self, workflow_id: str, active_execution_id: str):
        """Initialize the instance.

        Args:
            workflow_id: ID of the workflow that rejected the run.
            active_execution_id: ID of the already-active execution.

        """
        super().__init__(
            message=(
                f"Workflow {workflow_id} does not allow parallel execution; "
                f"execution {active_execution_id} is already active."
            ),
            details={
                "workflow_id": workflow_id,
                "active_execution_id": active_execution_id,
            },
        )
        self.workflow_id = workflow_id
        self.active_execution_id = active_execution_id


class PropertyValidationError(ChaosCypherException):
    """Raised when property validation fails.

    Maps to HTTP 400.

    Example:
        raise PropertyValidationError("age", "Must be a positive integer")
        # → HTTP 400: {"error": "VALIDATION_ERROR", "message": "Property 'age': Must be ..."}

    """

    def __init__(self, property_name: str, message: str):
        """Initialize the instance.

        Args:
            property_name: Name of the property that failed validation.
            message: Description of the validation error.

        """
        self.property_name = property_name
        super().__init__(
            message=f"Property '{property_name}': {message}",
            code="VALIDATION_ERROR",
            details={"property_name": property_name},
        )


class AuthorizationError(ChaosCypherException):
    """Permission denied (user lacks required permissions).

    Maps to HTTP 403.

    Example:
        raise AuthorizationError("Admin role required to delete workflows")
        # → HTTP 403: {"error": "PERMISSION_DENIED", "message": "Admin role required..."}

    """

    def __init__(
        self, message: str, required_permission: str | None = None, details: dict | None = None
    ):
        """Initialize the instance.

        Args:
            message: Human-readable permission error message.
            required_permission: Name of the permission that was required.
            details: Additional context about the permission error.

        """
        error_details = details or {}
        if required_permission:
            error_details["required_permission"] = required_permission

        super().__init__(message=message, code="PERMISSION_DENIED", details=error_details)
        self.required_permission = required_permission


class AuthenticationError(ChaosCypherException):
    """Authentication failed (invalid credentials, expired token, etc.).

    Maps to HTTP 401.

    Example:
        raise AuthenticationError("Invalid API key")
        # → HTTP 401: {"error": "AUTHENTICATION_ERROR", "message": "Invalid API key"}

    """

    def __init__(self, message: str = "Authentication required", details: dict | None = None):
        """Initialize the instance.

        Args:
            message: Human-readable authentication error message.
            details: Additional context about the authentication error.

        """
        super().__init__(message=message, code="AUTHENTICATION_ERROR", details=details or {})


class InsufficientStorageError(ChaosCypherException):
    """Disk space too low for the requested write operation.

    Maps to HTTP 507 (Insufficient Storage).

    Example:
        raise InsufficientStorageError(50, 100)
        # → HTTP 507: {"error": "INSUFFICIENT_STORAGE", "message": "Insufficient disk space: 50MB available, 100MB required"}

    """

    def __init__(self, message: str, details: dict | None = None):
        """Initialize the instance.

        Args:
            message: Human-readable storage error message.
            details: Additional context (e.g., free_mb, required_mb).

        """
        super().__init__(message=message, code="INSUFFICIENT_STORAGE", details=details or {})


class SourceFanoutLimitExceededError(ChaosCypherException):
    """Source fan-out would exceed the per-source LLM/vision task ceiling.

    Raised before any chunk-extraction or vision-page task is enqueued, so
    the source fails with zero LLM spend. Classified *permanent* by the queue
    (no retry) — re-running a too-large document never succeeds; the operator
    must split the document or raise the ceiling. Recorded on the worker via
    ``fail_extraction`` / ``fail_indexing``.

    Maps to HTTP 413 (Payload Too Large) if it ever surfaces through an HTTP
    path.

    Example:
        raise SourceFanoutLimitExceededError(
            "Document too large for full extraction: 10001 chunk-groups "
            "exceeds the per-source ceiling of 10000."
        )

    """

    def __init__(self, message: str, details: dict | None = None):
        """Initialize the instance.

        Args:
            message: Human-readable, operator-facing message naming the
                actual counts and the setting to raise.
            details: Additional context (stage, item_count, max_items).

        """
        super().__init__(
            message=message, code="SOURCE_FANOUT_LIMIT_EXCEEDED", details=details or {}
        )


class OperationError(ChaosCypherException):
    """Operation failed due to business logic constraints.

    Maps to HTTP 422 (Unprocessable Entity).

    Example:
        raise OperationError("Cannot delete workflow with active executions")
        # → HTTP 422: {"error": "OPERATION_ERROR", "message": "Cannot delete..."}

    """

    def __init__(self, message: str, operation: str | None = None, details: dict | None = None):
        """Initialize the instance.

        Args:
            message: Human-readable operation error message.
            operation: Name of the operation that failed.
            details: Additional context about the operation error.

        """
        error_details = details or {}
        if operation:
            error_details["operation"] = operation

        super().__init__(message=message, code="OPERATION_ERROR", details=error_details)
        self.operation = operation


class WorkflowExecutionError(OperationError):
    """A workflow run reached the error handler and returned a fatal error.

    Distinct from continue_on_error soft failures (which do not raise).
    Maps to HTTP 422.
    """

    def __init__(
        self,
        message: str,
        workflow_id: str,
        execution_id: str,
        failed_step_id: str | None = None,
        details: dict | None = None,
    ):
        """Initialize the instance.

        Args:
            message: Human-readable error message.
            workflow_id: ID of the workflow whose run failed.
            execution_id: ID of the failing execution record.
            failed_step_id: ID of the step that failed, if known.
            details: Additional context.

        """
        error_details = details or {}
        error_details["workflow_id"] = workflow_id
        error_details["execution_id"] = execution_id
        if failed_step_id is not None:
            error_details["failed_step_id"] = failed_step_id
        super().__init__(message=message, operation="execute_workflow", details=error_details)
        self.workflow_id = workflow_id
        self.execution_id = execution_id
        self.failed_step_id = failed_step_id


class WorkflowRecursionError(OperationError):
    """Nested workflow recursion exceeded the configured bound or formed a cycle.

    Maps to HTTP 422.
    """

    def __init__(
        self,
        message: str,
        workflow_id: str,
        depth: int,
        lineage: frozenset[str],
    ):
        """Initialize the instance.

        Args:
            message: Human-readable error message.
            workflow_id: ID of the workflow that would have been recursed.
            depth: Current recursion depth when the error was raised.
            lineage: Frozenset of workflow IDs already in the call chain.

        """
        super().__init__(
            message=message,
            operation="execute_workflow",
            details={
                "workflow_id": workflow_id,
                "depth": depth,
                "lineage": sorted(lineage),
            },
        )
        self.workflow_id = workflow_id
        self.depth = depth
        self.lineage = lineage


class ExternalServiceError(ChaosCypherException):
    """External service failure (LLM provider down, Valkey unavailable, etc.).

    Maps to HTTP 503 (Service Unavailable).

    Example:
        raise ExternalServiceError("Ollama", "Connection refused")
        # → HTTP 503: {"error": "EXTERNAL_SERVICE_ERROR", "message": "Ollama service unavailable: Connection refused"}

    """

    def __init__(self, service_name: str, reason: str | None = None, details: dict | None = None):
        """Initialize the instance.

        Args:
            service_name: Name of the external service that failed.
            reason: Description of why the service is unavailable.
            details: Additional context about the service error.

        """
        message = f"{service_name} service unavailable"
        if reason:
            message += f": {reason}"

        error_details = details or {}
        error_details["service"] = service_name
        if reason:
            error_details["reason"] = reason

        super().__init__(message=message, code="EXTERNAL_SERVICE_ERROR", details=error_details)
        self.service_name = service_name
        self.reason = reason


# =============================================================================
# LLM Provider Errors
# =============================================================================


class LLMError(ChaosCypherException):
    """Base exception for LLM provider errors.

    All LLM-specific exceptions inherit from this class for easy catching
    of any LLM-related error.

    Attributes:
        provider: The LLM provider that raised the error (e.g., 'gemini', 'openai')
        model: The model that was being used
        is_retryable: Whether the error might succeed on retry
        suggested_action: User-facing suggestion for how to resolve

    """

    def __init__(
        self,
        message: str,
        code: str = "LLM_ERROR",
        provider: str | None = None,
        model: str | None = None,
        is_retryable: bool = False,
        suggested_action: str | None = None,
        details: dict | None = None,
    ):
        """Initialize the instance.

        Args:
            message: Human-readable error message.
            code: Machine-readable error code.
            provider: LLM provider name (gemini, openai, anthropic, ollama).
            model: Model identifier that was being used.
            is_retryable: Whether retrying might succeed.
            suggested_action: User-facing suggestion for resolution.
            details: Additional context about the error.

        """
        error_details = details or {}
        if provider:
            error_details["provider"] = provider
        if model:
            error_details["model"] = model
        error_details["is_retryable"] = is_retryable
        if suggested_action:
            error_details["suggested_action"] = suggested_action

        super().__init__(message=message, code=code, details=error_details)
        self.provider = provider
        self.model = model
        self.is_retryable = is_retryable
        self.suggested_action = suggested_action


class LLMAuthenticationError(LLMError):
    """LLM API authentication failed (invalid/expired API key).

    Maps to HTTP 401.

    Example:
        raise LLMAuthenticationError("gemini", "Invalid API key")
        # → User sees: "Gemini authentication failed. Check your API key in Settings."

    """

    def __init__(
        self,
        provider: str,
        reason: str = "Invalid or expired API key",
        model: str | None = None,
        details: dict | None = None,
    ):
        """Initialize the instance.

        Args:
            provider: LLM provider name.
            reason: Description of why authentication failed.
            model: Model that was being accessed.
            details: Additional context.

        """
        message = f"{provider.title()} authentication failed: {reason}"
        super().__init__(
            message=message,
            code="LLM_AUTHENTICATION_ERROR",
            provider=provider,
            model=model,
            is_retryable=False,
            suggested_action=f"Check your {provider.title()} API key in Settings → LLM Provider.",
            details=details,
        )


class LLMRateLimitError(LLMError):
    """LLM API rate limit or quota exceeded.

    Maps to HTTP 429.

    Example:
        raise LLMRateLimitError("gemini", retry_after=60)
        # → User sees: "Rate limit exceeded. Try again in 60 seconds or switch to a different model."

    """

    def __init__(
        self,
        provider: str,
        model: str | None = None,
        retry_after: int | None = None,
        quota_exceeded: bool = False,
        details: dict | None = None,
    ):
        """Initialize the instance.

        Args:
            provider: LLM provider name.
            model: Model that was rate limited.
            retry_after: Seconds until rate limit resets.
            quota_exceeded: True if this is a quota issue (not just rate limiting).
            details: Additional context.

        """
        if quota_exceeded:
            message = f"{provider.title()} API quota exceeded"
            suggested = f"Your {provider.title()} quota is exhausted. Consider upgrading your plan or switching to a different model."
        elif retry_after:
            message = f"{provider.title()} rate limit exceeded. Retry in {retry_after}s"
            suggested = f"Wait {retry_after} seconds or try a less resource-intensive model."
        else:
            message = f"{provider.title()} rate limit exceeded"
            suggested = "Wait a moment and try again, or switch to a different model."

        error_details = details or {}
        if retry_after:
            error_details["retry_after"] = retry_after
        error_details["quota_exceeded"] = quota_exceeded

        super().__init__(
            message=message,
            code="LLM_RATE_LIMIT_ERROR",
            provider=provider,
            model=model,
            is_retryable=not quota_exceeded,
            suggested_action=suggested,
            details=error_details,
        )
        self.retry_after = retry_after
        self.quota_exceeded = quota_exceeded


class LLMModelError(LLMError):
    """LLM model not found or not available.

    Maps to HTTP 404 or 400.

    Example:
        raise LLMModelError("gemini", "gemini-3-ultra", "Model not found")
        # → User sees: "Model 'gemini-3-ultra' not found. Select a different model."

    """

    def __init__(
        self,
        provider: str,
        model: str,
        reason: str = "Model not found or not available",
        details: dict | None = None,
    ):
        """Initialize the instance.

        Args:
            provider: LLM provider name.
            model: Model that was not found.
            reason: Description of why model is unavailable.
            details: Additional context.

        """
        message = f"{provider.title()} model '{model}' error: {reason}"
        super().__init__(
            message=message,
            code="LLM_MODEL_ERROR",
            provider=provider,
            model=model,
            is_retryable=False,
            suggested_action="Select a different model in Settings → LLM Provider.",
            details=details,
        )


class LLMContentFilterError(LLMError):
    """LLM blocked response due to safety/content filters.

    Maps to HTTP 400.

    Example:
        raise LLMContentFilterError("gemini", "SAFETY", "Blocked due to safety settings")

    """

    def __init__(
        self,
        provider: str,
        filter_type: str = "SAFETY",
        reason: str = "Response blocked by content filter",
        model: str | None = None,
        details: dict | None = None,
    ):
        """Initialize the instance.

        Args:
            provider: LLM provider name.
            filter_type: Type of filter that triggered (SAFETY, RECITATION, etc.).
            reason: Description of why content was blocked.
            model: Model that was being used.
            details: Additional context.

        """
        message = f"{provider.title()} blocked response: {reason}"
        error_details = details or {}
        error_details["filter_type"] = filter_type

        super().__init__(
            message=message,
            code="LLM_CONTENT_FILTER_ERROR",
            provider=provider,
            model=model,
            is_retryable=False,
            suggested_action="Try rephrasing your request or adjusting safety settings.",
            details=error_details,
        )
        self.filter_type = filter_type


class LLMContextLengthError(LLMError):
    """LLM request exceeded context window size.

    Maps to HTTP 400.

    Example:
        raise LLMContextLengthError("gemini", "gemini-2.5-flash", 50000, 32000)

    """

    def __init__(
        self,
        provider: str,
        model: str,
        requested_tokens: int | None = None,
        max_tokens: int | None = None,
        details: dict | None = None,
    ):
        """Initialize the instance.

        Args:
            provider: LLM provider name.
            model: Model that was being used.
            requested_tokens: Number of tokens in the request.
            max_tokens: Maximum tokens allowed by the model.
            details: Additional context.

        """
        if requested_tokens and max_tokens:
            message = f"{provider.title()} context length exceeded: {requested_tokens:,} tokens requested, max is {max_tokens:,}"
        else:
            message = f"{provider.title()} context length exceeded for model '{model}'"

        error_details = details or {}
        if requested_tokens:
            error_details["requested_tokens"] = requested_tokens
        if max_tokens:
            error_details["max_tokens"] = max_tokens

        super().__init__(
            message=message,
            code="LLM_CONTEXT_LENGTH_ERROR",
            provider=provider,
            model=model,
            is_retryable=False,
            suggested_action="Reduce your input size or select a model with a larger context window.",
            details=error_details,
        )
        self.requested_tokens = requested_tokens
        self.max_tokens = max_tokens


class LLMServiceError(LLMError):
    """LLM provider service error (server error, timeout, etc.).

    Maps to HTTP 503.

    Example:
        raise LLMServiceError("gemini", "Service temporarily unavailable")

    """

    def __init__(
        self,
        provider: str,
        reason: str = "Service temporarily unavailable",
        model: str | None = None,
        is_timeout: bool = False,
        details: dict | None = None,
    ):
        """Initialize the instance.

        Args:
            provider: LLM provider name.
            reason: Description of the service error.
            model: Model that was being used.
            is_timeout: True if this was a timeout error.
            details: Additional context.

        """
        message = f"{provider.title()} service error: {reason}"
        error_details = details or {}
        error_details["is_timeout"] = is_timeout

        super().__init__(
            message=message,
            code="LLM_SERVICE_ERROR",
            provider=provider,
            model=model,
            is_retryable=True,
            suggested_action="The service may be experiencing issues. Try again in a moment.",
            details=error_details,
        )
        self.is_timeout = is_timeout


class ModelCapabilityError(LLMError):
    """Raised when a model doesn't support a required capability.

    This is a non-retryable error — the user must change their model selection.

    Example:
        raise ModelCapabilityError(
            "Model doesn't support tool calling",
            model="phi3",
            provider="ollama",
            capability="tool_calling",
        )

    """

    def __init__(
        self,
        message: str,
        model: str | None = None,
        provider: str | None = None,
        capability: str | None = None,
        details: dict | None = None,
    ):
        """Initialize the instance.

        Args:
            message: Human-readable error message.
            model: Model name that caused the error.
            provider: Provider name.
            capability: The capability not supported (e.g., "tool_calling", "thinking").
            details: Additional context.

        """
        self.capability = capability
        error_details = details or {}
        if capability:
            error_details["capability"] = capability

        super().__init__(
            message=message,
            code="MODEL_CAPABILITY_ERROR",
            provider=provider,
            model=model,
            is_retryable=False,
            suggested_action="Select a model that supports this capability.",
            details=error_details,
        )


class MaxBytesExceeded(ChaosCypherException):
    """Raised when a streaming download exceeds the configured byte cap.

    Raised by :meth:`WebScraper._fetch_with_redirect_validation_capped` when
    the accumulated response body exceeds *max_bytes*.  The caller converts
    this into an error-dict response rather than propagating the exception.

    Example:
        raise MaxBytesExceeded("Content exceeded max_bytes=5242880")

    """

    def __init__(self, message: str, details: dict | None = None):
        """Initialize the instance.

        Args:
            message: Human-readable description of the cap that was exceeded.
            details: Additional context (e.g., max_bytes, url).

        """
        super().__init__(message=message, code="MAX_BYTES_EXCEEDED", details=details or {})


class QueueFullError(ChaosCypherException):
    """Queue has reached its maximum pending task depth.

    Maps to HTTP 429 (Too Many Requests). Raised when a caller tries to
    enqueue a task but the pending sorted set already contains
    ``max_pending_queue_depth`` entries. This provides backpressure so
    that Valkey memory cannot grow without bound.

    Example:
        raise QueueFullError("llm", current_depth=10000, max_depth=10000)
        # → HTTP 429: {"error": "QUEUE_FULL", "message": "Queue 'llm' is full ..."}

    """

    def __init__(
        self,
        queue: str,
        current_depth: int,
        max_depth: int,
        details: dict | None = None,
    ):
        """Initialize the instance.

        Args:
            queue: Name of the queue that is full.
            current_depth: Current number of pending tasks.
            max_depth: Configured maximum pending depth.
            details: Additional context about the error.

        """
        message = (
            f"Queue '{queue}' is full ({current_depth}/{max_depth} pending tasks). Try again later."
        )
        error_details = details or {}
        error_details["queue"] = queue
        error_details["current_depth"] = current_depth
        error_details["max_depth"] = max_depth

        super().__init__(message=message, code="QUEUE_FULL", details=error_details)
        self.queue = queue
        self.current_depth = current_depth
        self.max_depth = max_depth


class ToolCallingNotSupportedError(ModelCapabilityError):
    """Raised when a model doesn't support tool/function calling.

    Specifically for extraction and workflow operations that require
    structured output via tool calling.

    Example:
        raise ToolCallingNotSupportedError("phi3", provider="ollama")

    """

    def __init__(self, model: str, provider: str = "ollama"):
        """Initialize tool calling error with helpful message.

        Args:
            model: Model name that doesn't support tool calling.
            provider: Provider name.

        """
        message = (
            f"The model '{model}' does not support tool calling, which is required for "
            f"entity extraction and structured output. Please select a different model "
            f"that supports tool/function calling.\n\n"
            f"Check your LLM provider documentation for models that support tool calling. "
            f"You can change models in Settings > LLM Configuration."
        )
        super().__init__(message, model, provider, capability="tool_calling")


class LLMSpendCapExceededError(LLMError):
    """LLM spend cap reached — refuses further extraction calls.

    Raised by :class:`chaoscypher_core.services.llm.spend.LLMSpendTracker`
    BEFORE the next LLM call when the configured ``max_tokens_per_source``
    or ``max_tokens_per_day`` is reached. ``is_retryable=False`` so the
    queue does NOT retry — the source is marked failed permanently and
    the operator must adjust caps or wait for the daily window to roll
    over.

    Example:
        raise LLMSpendCapExceededError(
            scope="source",
            cap_tokens=100_000,
            consumed_tokens=100_500,
            source_id="src_abc",
        )

    """

    def __init__(
        self,
        scope: str,
        cap_tokens: int,
        consumed_tokens: int,
        source_id: str | None = None,
    ):
        """Initialize the error with the scope and observed consumption.

        Args:
            scope: Either ``"source"`` or ``"day"`` — which cap fired.
            cap_tokens: Configured cap from settings.
            consumed_tokens: Tokens already consumed when the next call
                would have been made.
            source_id: Source identifier (when scope=="source").
        """
        if scope == "source":
            message = (
                f"Per-source LLM spend cap reached: {consumed_tokens:,} tokens "
                f"consumed against a {cap_tokens:,}-token budget for source "
                f"{source_id!r}. Source marked failed; raise "
                "settings.llm.max_tokens_per_source to allow more."
            )
        else:
            message = (
                f"Daily LLM spend cap reached: {consumed_tokens:,} tokens "
                f"consumed against a {cap_tokens:,}-token UTC-day budget. "
                "Subsequent extractions will be refused until the day rolls "
                "over; raise settings.llm.max_tokens_per_day to allow more."
            )
        super().__init__(
            message=message,
            code="LLM_SPEND_CAP_EXCEEDED",
            is_retryable=False,
            details={
                "scope": scope,
                "cap_tokens": cap_tokens,
                "consumed_tokens": consumed_tokens,
                "source_id": source_id,
            },
        )
        self.scope = scope
        self.cap_tokens = cap_tokens
        self.consumed_tokens = consumed_tokens
        self.source_id = source_id


class EncryptedPDFError(ValidationError):
    """PDF is password-protected and cannot be read without decryption.

    Maps to HTTP 422 (Unprocessable Entity). Raised by the PDF loader
    when ``PdfReader.is_encrypted`` is True before page iteration begins.

    Example:
        raise EncryptedPDFError("report.pdf")
        # → HTTP 422: {"error": "ENCRYPTED_PDF", "message": "PDF 'report.pdf' is encrypted ..."}

    """

    def __init__(self, filename: str):
        """Initialize the error with a clear, actionable message.

        Args:
            filename: Name (or path) of the encrypted PDF file.

        """
        super().__init__(
            message=(
                f"PDF '{filename}' is encrypted; password-protected PDFs are not supported. "
                "Decrypt the file before uploading."
            ),
            details={"filename": filename},
        )
        self.filename = filename
        # Override the generic VALIDATION_ERROR code for precise HTTP mapping.
        self.code = "ENCRYPTED_PDF"


class LoaderFileTooLargeError(ValidationError):
    """Source file exceeds the per-loader max_disk_bytes cap.

    Maps to HTTP 400 (via ValidationError). Raised by the per-loader
    size guard BEFORE invoking the heavyweight parser (pypdf,
    python-docx, full-text read), so a malicious or accidental
    multi-GB upload cannot OOM the worker. The cap lives at
    ``LoaderSettings.max_disk_bytes`` and defaults to 500 MiB.

    Example:
        raise LoaderFileTooLargeError("video.pdf", actual_bytes=2_000_000_000, max_bytes=524288000)
        # → HTTP 400: {"error": "LOADER_FILE_TOO_LARGE", "message": "File 'video.pdf' is 2.0 GB (1907 MiB) ..."}

    """

    def __init__(self, filename: str, actual_bytes: int, max_bytes: int):
        """Initialize the error with a clear, actionable message.

        Args:
            filename: Name (or path) of the oversized file.
            actual_bytes: Actual size of the file in bytes.
            max_bytes: Configured cap from settings.loader.max_disk_bytes.

        """
        actual_mib = actual_bytes / (1024 * 1024)
        max_mib = max_bytes / (1024 * 1024)
        super().__init__(
            message=(
                f"File '{filename}' is {actual_mib:.1f} MiB which exceeds the "
                f"configured loader cap of {max_mib:.1f} MiB. Reduce the file size "
                f"or raise the cap via settings.loader.max_disk_bytes."
            ),
            details={
                "filename": filename,
                "actual_bytes": actual_bytes,
                "max_bytes": max_bytes,
            },
        )
        self.filename = filename
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes
        # Override the generic VALIDATION_ERROR code for precise HTTP mapping.
        self.code = "LOADER_FILE_TOO_LARGE"


class RecoveryTimeoutError(ChaosCypherException):
    """A reconcile_database pass exceeded its configured timeout.

    Raised by the neuron worker's startup one-shot reconcile when
    ``asyncio.timeout(reconcile_timeout_seconds)`` fires before the pass
    completes.  Startup fails loud so operators are not left with a silently
    stalled worker.

    Example:
        raise RecoveryTimeoutError(
            "startup reconcile of mydb exceeded 30s",
        )

    """

    def __init__(self, message: str, details: dict | None = None):
        """Initialize the instance.

        Args:
            message: Human-readable description of the timeout.
            details: Additional context (e.g., database_name, timeout_seconds).

        """
        super().__init__(message=message, code="RECOVERY_TIMEOUT", details=details or {})


class ConfigError(ChaosCypherException):
    r"""Raised when a settings YAML file contains unrecognized top-level keys.

    Prevents typo'd configuration keys from silently falling back to
    defaults.  The error message includes the unknown key name and, when
    possible, a Levenshtein-based suggestion for the closest valid key.

    Example:
        raise ConfigError(
            "Unrecognized top-level setting(s) in /data/settings.yaml:\n"
            "  - embedding_settings (did you mean 'embedding'?)"
        )

    """

    def __init__(self, message: str):
        """Initialize the instance.

        Args:
            message: Human-readable description of which keys are unknown
                and any close-match suggestions.

        """
        super().__init__(message=message, code="CONFIG_ERROR")
