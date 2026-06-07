# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""OpenAPI Specification Handler.

Processes OpenAPI/Swagger specifications, chunking by operation
with schema references resolved inline using jsonref library.

Phase 5c (2026-05-08) — full spec coverage:
- All eight major sections are emitted: paths, schemas, securitySchemes,
  parameters, responses, requestBodies, tags + externalDocs, callbacks,
  webhooks, examples.
- Schemas recurse via ``_expand_schema`` (oneOf/anyOf/allOf/items/properties/
  enum) capped at ``LoaderSettings.openapi_max_schema_depth`` (default 4).
- ``jsonref`` is hard-required: ``OperationError`` on import failure or
  unresolvable ``$ref`` to prevent silent placeholder text in chunks.
- ``metadata["openapi_sections_emitted"]`` lists which sections produced
  chunks, enabling downstream data-quality tracking.
- Multi-spec archives: all spec files in the archive are discovered via
  ``_find_spec_files`` (not just the first match); each produces its own
  set of documents.

Example:
    from chaoscypher_core.services.sources.loaders.archive.handlers import (
        OpenAPIHandler,
    )

    handler = OpenAPIHandler(settings)
    score = handler.can_handle(extracted_dir)
    if score > 0:
        documents = handler.process(extracted_dir, settings)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.plugins.base import PluginMetadata


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class OpenAPIHandler:
    """Handler for OpenAPI/Swagger specifications.

    Features:
    - Parses OpenAPI 3.x and Swagger 2.x
    - Chunks by operation (one chunk per endpoint)
    - Includes resolved schemas inline using jsonref
    - Handles both JSON and YAML formats
    - Emits all major spec sections (security, parameters, responses, etc.)
    - Recurses nested schemas up to a configurable depth
    - Handles multi-spec archives (one set of docs per spec file)

    Detection Indicators:
    - openapi.json, openapi.yaml at root
    - swagger.json, swagger.yaml at root
    - 'openapi' or 'swagger' key in root object
    """

    metadata: ClassVar[PluginMetadata] = PluginMetadata(
        name="openapi",
        version="1.0.0",
        description="OpenAPI / Swagger API specifications.",
        priority=5,
    )

    # Files to check for OpenAPI specs
    SPEC_FILENAMES: ClassVar[list[str]] = [
        "openapi.json",
        "openapi.yaml",
        "openapi.yml",
        "swagger.json",
        "swagger.yaml",
        "swagger.yml",
        "api.json",
        "api.yaml",
        "api.yml",
    ]

    @property
    def name(self) -> str:
        """Handler identifier."""
        return "openapi"

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize OpenAPI handler.

        Args:
            settings: Engine settings for configuration.
        """
        self.settings = settings

    def can_handle(self, extracted_dir: Path) -> int:
        """Check for OpenAPI/Swagger specification.

        Args:
            extracted_dir: Path to extracted archive contents.

        Returns:
            Specificity score: 95 when a valid OpenAPI/Swagger spec is found,
            0 otherwise.
        """
        spec_path = self._find_spec_file(extracted_dir)

        if not spec_path:
            return 0

        # Verify it's actually an OpenAPI spec
        try:
            spec = self._parse_spec(spec_path)

            if "openapi" in spec:
                logger.debug(
                    "openapi_detection_result",
                    score=95,
                    spec_file=str(spec_path),
                    version=spec.get("openapi"),
                )
                return 95

            if "swagger" in spec:
                logger.debug(
                    "swagger_detection_result",
                    score=95,
                    spec_file=str(spec_path),
                    version=spec.get("swagger"),
                )
                return 95

        except Exception as e:
            logger.debug(
                "openapi_detection_failed",
                spec_file=str(spec_path),
                error=str(e),
            )

        return 0

    def find_root(self, extracted_dir: Path) -> Path:
        """OpenAPI specs live at (or near) the top level — no narrowing needed.

        :meth:`_find_spec_file` already handles nested specs internally by
        walking the tree, so :meth:`process` works correctly when passed
        the outer archive directory. Returning ``extracted_dir`` unchanged
        keeps behaviour stable and fulfils the protocol contract.

        Args:
            extracted_dir: Path to extracted archive contents.

        Returns:
            ``extracted_dir`` unchanged.
        """
        return extracted_dir

    def process(
        self,
        extracted_dir: Path,
        settings: EngineSettings,
    ) -> list[dict[str, Any]]:
        """Process all OpenAPI specifications found in the archive.

        Discovers all spec files (multi-spec support) and emits one set of
        documents per spec. Each spec produces:
        - One chunk for API info/description
        - One chunk per operation (path + method)
        - One chunk per schema in components
        - One chunk per security scheme
        - One chunk per reusable parameter
        - One chunk per reusable response
        - One chunk per reusable request body
        - One chunk for tags + externalDocs
        - One chunk per callback / webhook
        - One chunk per example

        Args:
            extracted_dir: Path to extracted archive.
            settings: Engine settings.

        Returns:
            List of document chunks across all discovered specs.
        """
        logger.info("openapi_processing_started", directory=str(extracted_dir))

        spec_paths = self._find_spec_files(extracted_dir)
        if not spec_paths:
            logger.warning("openapi_spec_not_found", directory=str(extracted_dir))
            # Phase 7 (2026-05-09 audit): synthetic-doc pattern matching
            # Markdown / Sphinx / Generic handlers. Operator sees an
            # actionable warning instead of a generic empty-content failure.
            warning = (
                f"No OpenAPI spec files (openapi.json / openapi.yaml / swagger.json) "
                f"detected in '{extracted_dir.name}'."
            )
            return [
                {
                    "content": "",
                    "metadata": {
                        "loader_warnings": [warning],
                        "doc_type": self.name,
                    },
                }
            ]

        all_documents: list[dict[str, Any]] = []

        for spec_path in spec_paths:
            # Reset per-call warning accumulator.
            self._resolve_refs_warnings: list[str] = []

            try:
                spec = self._parse_spec(spec_path)
                resolved_spec = self._resolve_refs(spec)
                documents = self._chunk_by_operation(resolved_spec, spec_path, extracted_dir)

                # Propagate the encoding used to load the spec into every
                # chunk's metadata so the indexing handler can record it on
                # the source row (Workstream 6, 2026-05-07).
                encoding_used = getattr(self, "_last_spec_encoding", None)
                replacement_chars_count = int(
                    getattr(self, "_last_spec_replacement_chars_count", 0) or 0
                )
                if encoding_used:
                    for doc in documents:
                        meta = doc.setdefault("metadata", {})
                        meta["encoding_used"] = encoding_used
                        meta["replacement_chars_count"] = replacement_chars_count

                # Attach any ref-resolution warnings accumulated during
                # _resolve_refs to the first document so the indexing handler
                # can surface them via loader_warnings.
                ref_warnings = getattr(self, "_resolve_refs_warnings", [])
                if documents and ref_warnings:
                    first_meta = documents[0].setdefault("metadata", {})
                    existing = first_meta.get("loader_warnings") or []
                    first_meta["loader_warnings"] = list(existing) + ref_warnings

                logger.info(
                    "openapi_processing_complete",
                    spec_file=str(spec_path),
                    documents_count=len(documents),
                )

                all_documents.extend(documents)

            except Exception as e:
                logger.error(
                    "openapi_processing_failed",
                    spec_file=str(spec_path),
                    error=str(e),
                    exc_info=True,
                )
                # Emit a synthetic empty-content doc so the user sees a
                # meaningful failure (spec parse error) rather than a generic
                # empty-content error.
                all_documents.append(
                    {
                        "content": "",
                        "metadata": {
                            "loader_files_skipped": 1,
                            "loader_warnings": [f"Spec file could not be processed: {e}"],
                            "doc_type": self.name,
                            "source": str(spec_path.relative_to(extracted_dir)).replace("\\", "/"),
                        },
                    }
                )

        return all_documents

    def _find_spec_file(self, extracted_dir: Path) -> Path | None:
        """Find first OpenAPI spec file in directory.

        Args:
            extracted_dir: Directory to search.

        Returns:
            Path to first spec file found, or None.
        """
        specs = self._find_spec_files(extracted_dir)
        return specs[0] if specs else None

    def _find_spec_files(self, extracted_dir: Path) -> list[Path]:
        """Find all OpenAPI spec files in directory.

        Checks root-level filenames first (in priority order), then walks
        subdirectories. Returns all valid spec files so multi-spec archives
        produce one set of documents per spec.

        Args:
            extracted_dir: Directory to search.

        Returns:
            List of paths to all spec files found (may be empty).
        """
        found: list[Path] = []
        seen: set[Path] = set()

        # Check root directory first (highest priority)
        for filename in self.SPEC_FILENAMES:
            spec_path = extracted_dir / filename
            if spec_path.exists() and spec_path not in seen:
                found.append(spec_path)
                seen.add(spec_path)

        # Check subdirectories
        for filename in self.SPEC_FILENAMES:
            for match in extracted_dir.rglob(filename):
                if match not in seen:
                    found.append(match)
                    seen.add(match)

        return found

    def _parse_spec(self, spec_path: Path) -> dict[str, Any]:
        """Parse OpenAPI spec from JSON or YAML.

        Args:
            spec_path: Path to spec file.

        Returns:
            Parsed spec dictionary.
        """
        from typing import cast

        from chaoscypher_core.utils.encoding import detect_encoding

        encoding_used, content, replacement_chars_count = detect_encoding(spec_path)
        # Stash on instance so chunked-doc metadata can pick it up.
        self._last_spec_encoding = encoding_used
        self._last_spec_replacement_chars_count = replacement_chars_count

        if spec_path.suffix in [".json"]:
            return cast("dict[str, Any]", json.loads(content))

        # YAML parsing
        try:
            import yaml

            return cast("dict[str, Any]", yaml.safe_load(content))
        except ImportError:
            logger.warning("pyyaml_not_installed", fallback="json_only")
            raise OperationError(
                "PyYAML required for YAML spec files",
                operation="archive_load",
            )

    def _resolve_refs(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Resolve $ref references in spec using jsonref.

        Args:
            spec: Raw spec dictionary.

        Returns:
            Spec with resolved references.

        Raises:
            OperationError: If jsonref is not installed or ref resolution fails,
                since unresolved ``$ref`` placeholders corrupt all downstream
                chunk text.
        """
        from typing import cast

        try:
            import jsonref
        except ImportError as exc:
            raise OperationError(
                "jsonref is required for OpenAPI $ref resolution. Install it with: uv add jsonref",
                operation="archive_load",
            ) from exc

        try:
            # Convert to JSON string and back to handle $ref resolution.
            return cast("dict[str, Any]", jsonref.loads(json.dumps(spec)))
        except Exception as exc:
            msg = f"Failed to resolve $ref references in OpenAPI spec: {exc}"
            raise OperationError(msg, operation="archive_load") from exc

    def _chunk_by_operation(  # noqa: C901, PLR0912, PLR0915 - chunker emits a chunk per OpenAPI section (info, paths, components, security, ...); each branch is a distinct shape
        self,
        spec: dict[str, Any],
        spec_path: Path,
        base_dir: Path,
    ) -> list[dict[str, Any]]:
        """Create document chunks from spec operations and all major sections.

        Args:
            spec: Resolved spec dictionary.
            spec_path: Path to spec file.
            base_dir: Base directory for metadata.

        Returns:
            List of document chunks.
        """
        documents: list[dict[str, Any]] = []
        relative_path = spec_path.relative_to(base_dir)
        sections_emitted: list[str] = []

        # Common base metadata for this spec
        base_meta: dict[str, Any] = {
            "source": str(relative_path).replace("\\", "/"),
            "filename": spec_path.name,
            "doc_type": self.name,
        }

        # 1. Info chunk - API overview
        info_content = self._format_api_info(spec)
        if info_content:
            documents.append(
                {
                    "content": info_content,
                    "metadata": {
                        **base_meta,
                        "hierarchy": "info",
                        "title": spec.get("info", {}).get("title", "API Overview"),
                        "chunk_type": "api_info",
                    },
                }
            )
            sections_emitted.append("info")

        # 2. Operation chunks - one per path+method
        paths = spec.get("paths", {})
        path_chunks_added = False
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            for method in ["get", "post", "put", "patch", "delete", "options", "head"]:
                if method not in path_item:
                    continue

                operation = path_item[method]
                operation_content = self._format_operation(path, method, operation, spec)

                if operation_content:
                    operation_id = operation.get("operationId", f"{method}_{path}")
                    summary = operation.get("summary", f"{method.upper()} {path}")

                    documents.append(
                        {
                            "content": operation_content,
                            "metadata": {
                                **base_meta,
                                "hierarchy": f"paths/{path}",
                                "title": summary,
                                "chunk_type": "operation",
                                "method": method.upper(),
                                "path": path,
                                "operation_id": operation_id,
                            },
                        }
                    )
                    path_chunks_added = True

        if path_chunks_added:
            sections_emitted.append("paths")

        # 3. Schemas chunk - all component schemas
        schemas_content = self._format_schemas(spec)
        if schemas_content:
            documents.append(
                {
                    "content": schemas_content,
                    "metadata": {
                        **base_meta,
                        "hierarchy": "schemas",
                        "title": "Data Models",
                        "chunk_type": "schemas",
                    },
                }
            )
            sections_emitted.append("schemas")

        # 4. Security schemes
        security_chunks = self._emit_security_schemes(spec, base_meta)
        if security_chunks:
            documents.extend(security_chunks)
            sections_emitted.append("security_schemes")

        # 5. Reusable parameters
        parameter_chunks = self._emit_reusable_parameters(spec, base_meta)
        if parameter_chunks:
            documents.extend(parameter_chunks)
            sections_emitted.append("parameters")

        # 6. Reusable responses
        response_chunks = self._emit_reusable_responses(spec, base_meta)
        if response_chunks:
            documents.extend(response_chunks)
            sections_emitted.append("responses")

        # 7. Reusable request bodies
        request_body_chunks = self._emit_reusable_request_bodies(spec, base_meta)
        if request_body_chunks:
            documents.extend(request_body_chunks)
            sections_emitted.append("request_bodies")

        # 8. Tags + externalDocs
        tags_chunk = self._emit_tags_and_external_docs(spec, base_meta)
        if tags_chunk:
            documents.append(tags_chunk)
            sections_emitted.append("tags")

        # 9. Callbacks
        callback_chunks = self._emit_callbacks(spec, base_meta)
        if callback_chunks:
            documents.extend(callback_chunks)
            sections_emitted.append("callbacks")

        # 10. Webhooks (OpenAPI 3.1+)
        webhook_chunks = self._emit_webhooks(spec, base_meta)
        if webhook_chunks:
            documents.extend(webhook_chunks)
            sections_emitted.append("webhooks")

        # 11. Examples
        example_chunks = self._emit_examples(spec, base_meta)
        if example_chunks:
            documents.extend(example_chunks)
            sections_emitted.append("examples")

        # Attach section coverage to all documents from this spec
        for doc in documents:
            doc["metadata"]["openapi_sections_emitted"] = sections_emitted

        return documents

    # ---------------------------------------------------------------------------
    # Section emitters (Task 1)
    # ---------------------------------------------------------------------------

    def _emit_security_schemes(
        self, spec: dict[str, Any], base_meta: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Emit one chunk per security scheme in components.securitySchemes.

        Args:
            spec: Resolved spec dictionary.
            base_meta: Shared metadata fields for this spec file.

        Returns:
            List of chunks, one per security scheme.
        """
        schemes = spec.get("components", {}).get("securitySchemes") or {}
        # Swagger 2.x: securityDefinitions
        if not schemes:
            schemes = spec.get("securityDefinitions") or {}

        chunks: list[dict[str, Any]] = []
        for name, scheme in schemes.items():
            if not isinstance(scheme, dict):
                continue
            scheme_type = scheme.get("type", "unknown")
            lines = [f"## Security Scheme: {name}", "", f"**Type:** {scheme_type}"]

            description = scheme.get("description", "")
            if description:
                lines += ["", description]

            # OAuth2 flows
            flows = scheme.get("flows", {})
            if flows:
                lines += ["", "**OAuth2 Flows:**"]
                for flow_name, flow in flows.items():
                    lines.append(
                        f"- {flow_name}: {flow.get('tokenUrl', flow.get('authorizationUrl', ''))}"
                    )

            # API key / http details
            if scheme_type == "apiKey":
                in_loc = scheme.get("in", "")
                param_name = scheme.get("name", "")
                if in_loc or param_name:
                    lines += ["", f"**In:** {in_loc}  **Name:** {param_name}"]
            elif scheme_type == "http":
                scheme_name = scheme.get("scheme", "")
                bearer_format = scheme.get("bearerFormat", "")
                if scheme_name:
                    lines += ["", f"**Scheme:** {scheme_name}"]
                if bearer_format:
                    lines.append(f"**Bearer Format:** {bearer_format}")

            # OpenID Connect
            open_id_url = scheme.get("openIdConnectUrl", "")
            if open_id_url:
                lines += ["", f"**OpenID Connect URL:** {open_id_url}"]

            chunks.append(
                {
                    "content": "\n".join(lines),
                    "metadata": {
                        **base_meta,
                        "hierarchy": f"components/securitySchemes/{name}",
                        "title": f"Security Scheme: {name}",
                        "chunk_type": "security_scheme",
                        "openapi_section": "security_schemes",
                        "name": name,
                    },
                }
            )

        return chunks

    def _emit_reusable_parameters(
        self, spec: dict[str, Any], base_meta: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Emit one chunk per reusable parameter in components.parameters.

        Args:
            spec: Resolved spec dictionary.
            base_meta: Shared metadata fields for this spec file.

        Returns:
            List of chunks, one per reusable parameter.
        """
        parameters = spec.get("components", {}).get("parameters") or {}
        chunks: list[dict[str, Any]] = []

        for name, param in parameters.items():
            if not isinstance(param, dict):
                continue

            location = param.get("in", "")
            required = param.get("required", False)
            description = param.get("description", "")
            schema = param.get("schema", {})
            max_depth = self._get_max_schema_depth()
            param_type = self._expand_schema(schema, depth=0, max_depth=max_depth)

            lines = [
                f"## Reusable Parameter: {name}",
                "",
                f"**In:** {location}  **Type:** {param_type}  **Required:** {required}",
            ]
            if description:
                lines += ["", description]

            chunks.append(
                {
                    "content": "\n".join(lines),
                    "metadata": {
                        **base_meta,
                        "hierarchy": f"components/parameters/{name}",
                        "title": f"Reusable Parameter: {name}",
                        "chunk_type": "reusable_parameter",
                        "openapi_section": "parameters",
                        "name": name,
                    },
                }
            )

        return chunks

    def _emit_reusable_responses(
        self, spec: dict[str, Any], base_meta: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Emit one chunk per reusable response in components.responses.

        Args:
            spec: Resolved spec dictionary.
            base_meta: Shared metadata fields for this spec file.

        Returns:
            List of chunks, one per reusable response.
        """
        responses = spec.get("components", {}).get("responses") or {}
        chunks: list[dict[str, Any]] = []

        for name, response in responses.items():
            if not isinstance(response, dict):
                continue

            description = response.get("description", "")
            lines = [f"## Reusable Response: {name}", ""]
            if description:
                lines.append(description)
                lines.append("")

            content = response.get("content", {})
            if content:
                lines.append("**Content Types:**")
                max_depth = self._get_max_schema_depth()
                for content_type, media in content.items():
                    schema = media.get("schema", {}) if isinstance(media, dict) else {}
                    schema_desc = self._expand_schema(schema, depth=0, max_depth=max_depth)
                    lines.append(f"- {content_type}: {schema_desc}")

            chunks.append(
                {
                    "content": "\n".join(lines),
                    "metadata": {
                        **base_meta,
                        "hierarchy": f"components/responses/{name}",
                        "title": f"Reusable Response: {name}",
                        "chunk_type": "reusable_response",
                        "openapi_section": "responses",
                        "name": name,
                    },
                }
            )

        return chunks

    def _emit_reusable_request_bodies(
        self, spec: dict[str, Any], base_meta: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Emit one chunk per reusable request body in components.requestBodies.

        Args:
            spec: Resolved spec dictionary.
            base_meta: Shared metadata fields for this spec file.

        Returns:
            List of chunks, one per reusable request body.
        """
        request_bodies = spec.get("components", {}).get("requestBodies") or {}
        chunks: list[dict[str, Any]] = []

        for name, request_body in request_bodies.items():
            if not isinstance(request_body, dict):
                continue

            description = request_body.get("description", "")
            required = request_body.get("required", False)
            lines = [
                f"## Reusable Request Body: {name}",
                "",
                f"**Required:** {required}",
            ]
            if description:
                lines += ["", description]

            content = request_body.get("content", {})
            if content:
                lines += ["", "**Content Types:**"]
                max_depth = self._get_max_schema_depth()
                for content_type, media in content.items():
                    schema = media.get("schema", {}) if isinstance(media, dict) else {}
                    schema_desc = self._expand_schema(schema, depth=0, max_depth=max_depth)
                    lines.append(f"- {content_type}: {schema_desc}")

            chunks.append(
                {
                    "content": "\n".join(lines),
                    "metadata": {
                        **base_meta,
                        "hierarchy": f"components/requestBodies/{name}",
                        "title": f"Reusable Request Body: {name}",
                        "chunk_type": "reusable_request_body",
                        "openapi_section": "request_bodies",
                        "name": name,
                    },
                }
            )

        return chunks

    def _emit_tags_and_external_docs(
        self, spec: dict[str, Any], base_meta: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Emit a single chunk for top-level tags and externalDocs.

        Args:
            spec: Resolved spec dictionary.
            base_meta: Shared metadata fields for this spec file.

        Returns:
            A single chunk dict, or None if neither section is present.
        """
        tags = spec.get("tags") or []
        external_docs = spec.get("externalDocs") or {}

        if not tags and not external_docs:
            return None

        lines: list[str] = []

        if tags:
            lines.append("# API Tags")
            lines.append("")
            for tag in tags:
                if not isinstance(tag, dict):
                    continue
                tag_name = tag.get("name", "")
                tag_desc = tag.get("description", "")
                lines.append(f"## Tag: {tag_name}")
                if tag_desc:
                    lines.append(tag_desc)
                tag_ext_docs = tag.get("externalDocs", {})
                if isinstance(tag_ext_docs, dict) and tag_ext_docs.get("url"):
                    lines.append(f"External Docs: {tag_ext_docs['url']}")
                lines.append("")

        if isinstance(external_docs, dict) and external_docs:
            lines.append("# External Documentation")
            lines.append("")
            ext_desc = external_docs.get("description", "")
            ext_url = external_docs.get("url", "")
            if ext_desc:
                lines.append(ext_desc)
            if ext_url:
                lines.append(f"URL: {ext_url}")

        return {
            "content": "\n".join(lines),
            "metadata": {
                **base_meta,
                "hierarchy": "tags",
                "title": "Tags and External Documentation",
                "chunk_type": "tags",
                "openapi_section": "tags",
            },
        }

    def _emit_callbacks(
        self, spec: dict[str, Any], base_meta: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Emit one chunk per reusable callback in components.callbacks.

        Args:
            spec: Resolved spec dictionary.
            base_meta: Shared metadata fields for this spec file.

        Returns:
            List of chunks, one per reusable callback.
        """
        callbacks = spec.get("components", {}).get("callbacks") or {}
        chunks: list[dict[str, Any]] = []

        for name, callback in callbacks.items():
            if not isinstance(callback, dict):
                continue

            lines = [f"## Callback: {name}", ""]

            # A callback maps expression keys to path item objects
            for expression, path_item in callback.items():
                if not isinstance(path_item, dict):
                    continue
                lines.append(f"**Expression:** `{expression}`")
                for method in ["get", "post", "put", "patch", "delete"]:
                    if method in path_item:
                        op = path_item[method]
                        summary = op.get("summary", "") if isinstance(op, dict) else ""
                        lines.append(f"- {method.upper()}: {summary}")
                lines.append("")

            chunks.append(
                {
                    "content": "\n".join(lines),
                    "metadata": {
                        **base_meta,
                        "hierarchy": f"components/callbacks/{name}",
                        "title": f"Callback: {name}",
                        "chunk_type": "callback",
                        "openapi_section": "callbacks",
                        "name": name,
                    },
                }
            )

        return chunks

    def _emit_webhooks(
        self, spec: dict[str, Any], base_meta: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Emit one chunk per webhook (OpenAPI 3.1+ top-level webhooks).

        Args:
            spec: Resolved spec dictionary.
            base_meta: Shared metadata fields for this spec file.

        Returns:
            List of chunks, one per webhook.
        """
        webhooks = spec.get("webhooks") or {}
        chunks: list[dict[str, Any]] = []

        for name, path_item in webhooks.items():
            if not isinstance(path_item, dict):
                continue

            lines = [f"## Webhook: {name}", ""]

            for method in ["get", "post", "put", "patch", "delete"]:
                if method not in path_item:
                    continue
                operation = path_item[method]
                summary = operation.get("summary", "") if isinstance(operation, dict) else ""
                description = (
                    operation.get("description", "") if isinstance(operation, dict) else ""
                )
                lines.append(f"**{method.upper()}**")
                if summary:
                    lines.append(f"Summary: {summary}")
                if description:
                    lines.append(description)
                lines.append("")

            chunks.append(
                {
                    "content": "\n".join(lines),
                    "metadata": {
                        **base_meta,
                        "hierarchy": f"webhooks/{name}",
                        "title": f"Webhook: {name}",
                        "chunk_type": "webhook",
                        "openapi_section": "webhooks",
                        "name": name,
                    },
                }
            )

        return chunks

    def _emit_examples(
        self, spec: dict[str, Any], base_meta: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Emit one chunk per example in components.examples.

        Args:
            spec: Resolved spec dictionary.
            base_meta: Shared metadata fields for this spec file.

        Returns:
            List of chunks, one per example.
        """
        examples = spec.get("components", {}).get("examples") or {}
        chunks: list[dict[str, Any]] = []

        for name, example in examples.items():
            if not isinstance(example, dict):
                continue

            summary = example.get("summary", "")
            description = example.get("description", "")
            value = example.get("value")
            external_value = example.get("externalValue", "")

            lines = [f"## Example: {name}", ""]
            if summary:
                lines.append(f"**Summary:** {summary}")
            if description:
                lines += ["", description]
            if value is not None:
                value_str = json.dumps(value, indent=2) if not isinstance(value, str) else value
                lines += ["", "**Value:**", "```", value_str, "```"]
            if external_value:
                lines += ["", f"**External Value:** {external_value}"]

            chunks.append(
                {
                    "content": "\n".join(lines),
                    "metadata": {
                        **base_meta,
                        "hierarchy": f"components/examples/{name}",
                        "title": f"Example: {name}",
                        "chunk_type": "example",
                        "openapi_section": "examples",
                        "name": name,
                    },
                }
            )

        return chunks

    # ---------------------------------------------------------------------------
    # Recursive schema expansion (Task 2)
    # ---------------------------------------------------------------------------

    def _get_max_schema_depth(self) -> int:
        """Get the configured maximum schema expansion depth.

        Returns:
            Maximum depth from settings, or default of 4.
        """
        if self.settings is not None:
            loader = getattr(self.settings, "loader", None)
            if loader is not None:
                depth = getattr(loader, "openapi_max_schema_depth", None)
                if depth is not None:
                    return int(depth)
        return 4

    def _expand_schema(  # noqa: PLR0911 - schema walker: one return per JSON Schema shape (ref/oneOf/anyOf/allOf/array/object/enum/scalar)
        self,
        schema: Any,
        *,
        depth: int = 0,
        max_depth: int,
    ) -> str:
        """Recursively expand a JSON Schema object into a human-readable string.

        Handles ``oneOf``, ``anyOf``, ``allOf``, array ``items``,
        ``properties``, ``enum``, and scalar types. Recursion is capped at
        ``max_depth`` to avoid infinite expansion on deeply nested or circular
        schemas.

        ``schema`` is typed as ``Any`` so the defensive ``isinstance(schema,
        dict)`` guard below remains live: malformed specs can pass scalars
        (string, list, None) through nested calls, and returning ``"any"``
        is the contract callers rely on.

        Args:
            schema: JSON Schema dict to expand. Non-dicts return ``"any"``.
            depth: Current recursion depth (starts at 0).
            max_depth: Maximum allowed recursion depth.

        Returns:
            Human-readable type description string.
        """
        if not isinstance(schema, dict):
            return "any"

        if depth >= max_depth:
            return f"<schema (depth {max_depth} reached)>"

        # Phase 7 audit-remediation (2026-05-09): align with Phase 5c's
        # 'jsonref hard-required' stance. If a ref reaches the schema walker
        # without being resolved by _resolve_refs, fail loudly instead of
        # silently returning the basename (e.g. "Pet"), which corrupts chunk text.
        if "$ref" in schema and len(schema) == 1:
            msg = (
                f"Unresolved $ref in OpenAPI schema: {schema['$ref']!r}. "
                "Check that all $ref targets exist in the spec or in linked specs."
            )
            raise OperationError(msg, operation="archive_load")

        if "oneOf" in schema:
            parts = [
                self._expand_schema(s, depth=depth + 1, max_depth=max_depth)
                for s in schema["oneOf"]
                if isinstance(s, dict)
            ]
            return "one of: " + ", ".join(parts) if parts else "one of: (empty)"

        if "anyOf" in schema:
            parts = [
                self._expand_schema(s, depth=depth + 1, max_depth=max_depth)
                for s in schema["anyOf"]
                if isinstance(s, dict)
            ]
            return "any of: " + ", ".join(parts) if parts else "any of: (empty)"

        if "allOf" in schema:
            parts = [
                self._expand_schema(s, depth=depth + 1, max_depth=max_depth)
                for s in schema["allOf"]
                if isinstance(s, dict)
            ]
            return "all of: " + ", ".join(parts) if parts else "all of: (empty)"

        schema_type = schema.get("type")

        if schema_type == "array":
            items = schema.get("items", {})
            items_str = self._expand_schema(
                items if isinstance(items, dict) else {},
                depth=depth + 1,
                max_depth=max_depth,
            )
            return f"array of {items_str}"

        if schema_type == "object":
            props = schema.get("properties", {})
            if props and isinstance(props, dict):
                prop_strs = [
                    f"{prop_name}: {self._expand_schema(prop_schema if isinstance(prop_schema, dict) else {}, depth=depth + 1, max_depth=max_depth)}"
                    for prop_name, prop_schema in props.items()
                ]
                return "object {" + ", ".join(prop_strs) + "}"
            return "object"

        if "enum" in schema:
            enum_values = schema["enum"]
            type_prefix = f"{schema_type} " if schema_type else ""
            return f"{type_prefix}(one of: {enum_values})"

        if schema_type:
            fmt = schema.get("format")
            return f"{schema_type}({fmt})" if fmt else str(schema_type)

        return "any"

    # ---------------------------------------------------------------------------
    # Existing formatters (updated to use _expand_schema)
    # ---------------------------------------------------------------------------

    def _format_api_info(self, spec: dict[str, Any]) -> str:
        """Format API info section as text.

        Args:
            spec: Spec dictionary.

        Returns:
            Formatted info text.
        """
        info = spec.get("info", {})
        lines: list[str] = []

        title = info.get("title", "API")
        version = info.get("version", "")

        lines.append(f"# {title}")
        if version:
            lines.append(f"Version: {version}")
        lines.append("")

        if "description" in info:
            lines.append(info["description"])
            lines.append("")

        # Servers
        servers = spec.get("servers", [])
        if servers:
            lines.append("## Servers")
            for server in servers:
                url = server.get("url", "")
                desc = server.get("description", "")
                lines.append(f"- {url}" + (f" ({desc})" if desc else ""))
            lines.append("")

        # Contact
        contact = info.get("contact", {})
        if contact:
            lines.append("## Contact")
            if "name" in contact:
                lines.append(f"Name: {contact['name']}")
            if "email" in contact:
                lines.append(f"Email: {contact['email']}")
            if "url" in contact:
                lines.append(f"URL: {contact['url']}")
            lines.append("")

        return "\n".join(lines)

    def _format_operation(
        self,
        path: str,
        method: str,
        operation: dict[str, Any],
        spec: dict[str, Any],
    ) -> str:
        """Format operation as text.

        Args:
            path: API path.
            method: HTTP method.
            operation: Operation object.
            spec: Full spec for schema resolution.

        Returns:
            Formatted operation text.
        """
        max_depth = self._get_max_schema_depth()
        lines: list[str] = []

        # Header
        summary = operation.get("summary", "")
        lines.append(f"# {method.upper()} {path}")
        if summary:
            lines.append(f"**{summary}**")
        lines.append("")

        # Description
        description = operation.get("description", "")
        if description:
            lines.append(description)
            lines.append("")

        # Parameters
        parameters = operation.get("parameters", [])
        if parameters:
            lines.append("## Parameters")
            for param in parameters:
                name = param.get("name", "")
                location = param.get("in", "")
                required = param.get("required", False)
                param_desc = param.get("description", "")
                schema = param.get("schema", {})
                param_type = self._expand_schema(
                    schema if isinstance(schema, dict) else {},
                    depth=0,
                    max_depth=max_depth,
                )

                req_marker = "*required*" if required else ""
                lines.append(f"- **{name}** ({location}, {param_type}) {req_marker}")
                if param_desc:
                    lines.append(f"  {param_desc}")
            lines.append("")

        # Request Body
        request_body = operation.get("requestBody", {})
        if request_body:
            lines.append("## Request Body")
            rb_desc = request_body.get("description", "")
            if rb_desc:
                lines.append(rb_desc)

            content = request_body.get("content", {})
            for content_type, media in content.items():
                lines.append(f"Content-Type: {content_type}")
                schema = media.get("schema", {})
                if schema:
                    lines.append(f"Schema: {self._format_schema_brief(schema)}")
            lines.append("")

        # Responses
        responses = operation.get("responses", {})
        if responses:
            lines.append("## Responses")
            for status_code, response in responses.items():
                resp_desc = response.get("description", "")
                lines.append(f"- **{status_code}**: {resp_desc}")

                # Response schema
                resp_content = response.get("content", {})
                for content_type, media in resp_content.items():
                    schema = media.get("schema", {})
                    if schema:
                        schema_brief = self._format_schema_brief(schema)
                        lines.append(f"  Schema ({content_type}): {schema_brief}")
            lines.append("")

        # Tags
        tags = operation.get("tags", [])
        if tags:
            lines.append(f"Tags: {', '.join(tags)}")
            lines.append("")

        return "\n".join(lines)

    def _format_schemas(self, spec: dict[str, Any]) -> str:
        """Format component schemas as text.

        Args:
            spec: Spec dictionary.

        Returns:
            Formatted schemas text.
        """
        max_depth = self._get_max_schema_depth()

        # OpenAPI 3.x
        schemas = spec.get("components", {}).get("schemas", {})

        # Swagger 2.x fallback
        if not schemas:
            schemas = spec.get("definitions", {})

        if not schemas:
            return ""

        lines: list[str] = []
        lines.append("# Data Models (Schemas)")
        lines.append("")

        for schema_name, schema in schemas.items():
            lines.append(f"## {schema_name}")

            description = schema.get("description", "")
            if description:
                lines.append(description)

            schema_type = schema.get("type", "object")
            lines.append(f"Type: {schema_type}")

            # Properties
            properties = schema.get("properties", {})
            required = set(schema.get("required", []))

            if properties:
                lines.append("Properties:")
                for prop_name, prop_schema in properties.items():
                    prop_type = self._expand_schema(
                        prop_schema if isinstance(prop_schema, dict) else {},
                        depth=0,
                        max_depth=max_depth,
                    )
                    prop_desc = (
                        prop_schema.get("description", "") if isinstance(prop_schema, dict) else ""
                    )
                    req_marker = "*required*" if prop_name in required else ""

                    lines.append(f"- **{prop_name}** ({prop_type}) {req_marker}")
                    if prop_desc:
                        lines.append(f"  {prop_desc}")

            lines.append("")

        return "\n".join(lines)

    def _format_schema_brief(self, schema: dict[str, Any]) -> str:
        """Format schema as brief description.

        Args:
            schema: Schema object.

        Returns:
            Brief schema description.
        """
        from typing import cast

        if "$ref" in schema:
            ref = schema["$ref"]
            return cast("str", ref.split("/")[-1])

        schema_type = schema.get("type", "object")

        if schema_type == "array":
            items = schema.get("items", {})
            items_type = self._format_schema_brief(items)
            return f"array of {items_type}"

        if schema_type == "object":
            props = list(schema.get("properties", {}).keys())
            if props:
                return f"object ({', '.join(props[:3])}{'...' if len(props) > 3 else ''})"
            return "object"

        return cast("str", schema_type)


__all__ = ["OpenAPIHandler"]
