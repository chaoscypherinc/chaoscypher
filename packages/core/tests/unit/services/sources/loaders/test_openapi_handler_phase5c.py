# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for OpenAPI handler Phase 5c — full spec coverage.

Covers:
- Task 1: Section emitters (securitySchemes, parameters, responses,
  requestBodies, tags + externalDocs, callbacks, webhooks, examples)
- Task 2: Recursive schema expansion (_expand_schema with depth cap)
- Task 3: Hard-require jsonref (OperationError on missing dep or bad refs)
- Task 4: Per-section coverage tracking (openapi_sections_emitted) and
  multi-spec archive support (_find_spec_files returns all specs)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.services.sources.loaders.archive.handlers.openapi_handler import (
    OpenAPIHandler,
)
from chaoscypher_core.settings import EngineSettings, LoaderSettings, PathSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(data_dir: Path, **loader_kwargs: Any) -> EngineSettings:
    """Build minimal EngineSettings."""
    loader = LoaderSettings(**loader_kwargs) if loader_kwargs else LoaderSettings()
    return EngineSettings(paths=PathSettings(data_dir=str(data_dir)), loader=loader)


def _write_spec(path: Path, spec: dict[str, Any]) -> None:
    """Write a JSON OpenAPI spec to path."""
    path.write_text(json.dumps(spec))


# ---------------------------------------------------------------------------
# Fixtures: spec payloads
# ---------------------------------------------------------------------------

_FULL_SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Full Coverage API", "version": "2.0.0"},
    "tags": [
        {"name": "items", "description": "Item operations"},
        {"name": "users", "description": "User operations"},
    ],
    "externalDocs": {"description": "See the docs", "url": "https://example.com/docs"},
    "paths": {
        "/items": {
            "get": {
                "summary": "List items",
                "operationId": "listItems",
                "tags": ["items"],
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "schema": {"type": "integer", "format": "int32"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/Item"},
                                }
                            }
                        },
                    }
                },
            }
        }
    },
    "components": {
        "schemas": {
            "Item": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "name": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["id", "name"],
            }
        },
        "securitySchemes": {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "JWT Bearer token",
            },
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
            },
        },
        "parameters": {
            "PageParam": {
                "name": "page",
                "in": "query",
                "required": False,
                "description": "Page number for pagination",
                "schema": {"type": "integer", "format": "int32"},
            }
        },
        "responses": {
            "NotFound": {
                "description": "Resource not found",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "error": {"type": "string"},
                                "code": {"type": "integer"},
                            },
                        }
                    }
                },
            }
        },
        "requestBodies": {
            "CreateItemRequest": {
                "description": "Payload to create a new item",
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                            },
                        }
                    }
                },
            }
        },
        "callbacks": {
            "onItemCreated": {
                "{$url}/callback": {
                    "post": {"summary": "Notify on item creation"},
                }
            }
        },
        "examples": {
            "ItemExample": {
                "summary": "A sample item",
                "value": {"id": "abc-123", "name": "Widget"},
            }
        },
    },
    "webhooks": {
        "newItem": {
            "post": {
                "summary": "New item webhook",
                "description": "Fires when a new item is created",
            }
        }
    },
}


# ---------------------------------------------------------------------------
# Task 1: Section emitters
# ---------------------------------------------------------------------------


class TestSectionEmitters:
    """Each major spec section produces at least one chunk when present."""

    def _process(self, tmp_path: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
        _write_spec(tmp_path / "openapi.json", spec)
        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        return handler.process(tmp_path, settings)

    def test_security_schemes_produce_chunks(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        security_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "security_scheme"]
        assert len(security_chunks) == 2  # BearerAuth + ApiKeyAuth

    def test_security_scheme_bearer_content(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        bearer = next(d for d in docs if d["metadata"].get("name") == "BearerAuth")
        assert "## Security Scheme: BearerAuth" in bearer["content"]
        assert "bearer" in bearer["content"].lower()
        assert "JWT" in bearer["content"]

    def test_security_scheme_apikey_content(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        apikey = next(d for d in docs if d["metadata"].get("name") == "ApiKeyAuth")
        assert "## Security Scheme: ApiKeyAuth" in apikey["content"]
        assert "X-API-Key" in apikey["content"]

    def test_reusable_parameters_produce_chunks(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        param_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "reusable_parameter"]
        assert len(param_chunks) == 1
        assert "## Reusable Parameter: PageParam" in param_chunks[0]["content"]
        assert "pagination" in param_chunks[0]["content"].lower()

    def test_reusable_responses_produce_chunks(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        response_chunks = [
            d for d in docs if d["metadata"].get("chunk_type") == "reusable_response"
        ]
        assert len(response_chunks) == 1
        assert "## Reusable Response: NotFound" in response_chunks[0]["content"]
        assert "not found" in response_chunks[0]["content"].lower()

    def test_reusable_request_bodies_produce_chunks(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        rb_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "reusable_request_body"]
        assert len(rb_chunks) == 1
        assert "## Reusable Request Body: CreateItemRequest" in rb_chunks[0]["content"]

    def test_tags_and_external_docs_produce_chunk(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        tags_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "tags"]
        assert len(tags_chunks) == 1
        content = tags_chunks[0]["content"]
        assert "## Tag: items" in content
        assert "## Tag: users" in content
        assert "https://example.com/docs" in content

    def test_callbacks_produce_chunks(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        cb_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "callback"]
        assert len(cb_chunks) == 1
        assert "## Callback: onItemCreated" in cb_chunks[0]["content"]

    def test_webhooks_produce_chunks(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        wh_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "webhook"]
        assert len(wh_chunks) == 1
        assert "## Webhook: newItem" in wh_chunks[0]["content"]
        assert "New item webhook" in wh_chunks[0]["content"]

    def test_examples_produce_chunks(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        ex_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "example"]
        assert len(ex_chunks) == 1
        content = ex_chunks[0]["content"]
        assert "## Example: ItemExample" in content
        assert "A sample item" in content
        assert "abc-123" in content

    def test_spec_with_no_extra_sections_still_works(self, tmp_path: Path) -> None:
        """Minimal spec with no components produces only info + paths chunks."""
        minimal: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "Minimal", "version": "1.0.0"},
            "paths": {
                "/ping": {
                    "get": {
                        "operationId": "ping",
                        "responses": {"200": {"description": "pong"}},
                    }
                }
            },
        }
        docs = self._process(tmp_path, minimal)
        assert len(docs) >= 2  # info + operation
        # No extra section chunks
        for d in docs:
            assert d["metadata"].get("chunk_type") not in {
                "security_scheme",
                "reusable_parameter",
                "reusable_response",
                "reusable_request_body",
                "callback",
                "webhook",
                "example",
            }

    def test_tags_only_no_external_docs(self, tmp_path: Path) -> None:
        """Tags chunk is emitted even without externalDocs."""
        spec: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "Minimal", "version": "1.0.0"},
            "tags": [{"name": "alpha", "description": "Alpha ops"}],
            "paths": {},
        }
        docs = self._process(tmp_path, spec)
        tags_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "tags"]
        assert len(tags_chunks) == 1
        assert "Tag: alpha" in tags_chunks[0]["content"]

    def test_external_docs_only_no_tags(self, tmp_path: Path) -> None:
        """ExternalDocs chunk is emitted even without tags."""
        spec: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "Minimal", "version": "1.0.0"},
            "externalDocs": {"url": "https://example.com"},
            "paths": {},
        }
        docs = self._process(tmp_path, spec)
        tags_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "tags"]
        assert len(tags_chunks) == 1

    def test_swagger2_security_definitions(self, tmp_path: Path) -> None:
        """Swagger 2.x securityDefinitions are emitted as security scheme chunks."""
        swagger2: dict[str, Any] = {
            "swagger": "2.0",
            "info": {"title": "Swagger 2", "version": "1.0"},
            "paths": {},
            "securityDefinitions": {
                "BasicAuth": {"type": "basic", "description": "HTTP Basic"},
            },
        }
        docs = self._process(tmp_path, swagger2)
        sec_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "security_scheme"]
        assert len(sec_chunks) == 1
        assert "BasicAuth" in sec_chunks[0]["content"]

    def test_openapi_section_metadata_key(self, tmp_path: Path) -> None:
        """Every section chunk carries an openapi_section metadata key."""
        docs = self._process(tmp_path, _FULL_SPEC)
        section_types = {
            "security_scheme": "security_schemes",
            "reusable_parameter": "parameters",
            "reusable_response": "responses",
            "reusable_request_body": "request_bodies",
            "callback": "callbacks",
            "webhook": "webhooks",
            "example": "examples",
            "tags": "tags",
        }
        for doc in docs:
            chunk_type = doc["metadata"].get("chunk_type", "")
            if chunk_type in section_types:
                assert doc["metadata"].get("openapi_section") == section_types[chunk_type], (
                    f"chunk_type={chunk_type} missing correct openapi_section"
                )


# ---------------------------------------------------------------------------
# Task 2: Recursive schema expansion
# ---------------------------------------------------------------------------


class TestRecursiveSchemaExpansion:
    """_expand_schema recurses into complex schema structures."""

    def setup_method(self) -> None:
        self.handler = OpenAPIHandler()

    def test_scalar_type(self) -> None:
        assert self.handler._expand_schema({"type": "string"}, max_depth=4) == "string"

    def test_scalar_type_with_format(self) -> None:
        result = self.handler._expand_schema({"type": "integer", "format": "int32"}, max_depth=4)
        assert result == "integer(int32)"

    def test_array_of_string(self) -> None:
        schema = {"type": "array", "items": {"type": "string"}}
        assert self.handler._expand_schema(schema, max_depth=4) == "array of string"

    def test_array_of_object(self) -> None:
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
            },
        }
        result = self.handler._expand_schema(schema, max_depth=4)
        assert result.startswith("array of object")
        assert "id" in result

    def test_object_with_properties(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        result = self.handler._expand_schema(schema, max_depth=4)
        assert "object" in result
        assert "name: string" in result
        assert "age: integer" in result

    def test_one_of(self) -> None:
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
            ]
        }
        result = self.handler._expand_schema(schema, max_depth=4)
        assert result.startswith("one of:")
        assert "string" in result
        assert "integer" in result

    def test_any_of(self) -> None:
        schema = {
            "anyOf": [
                {"type": "string"},
                {"type": "null"},
            ]
        }
        result = self.handler._expand_schema(schema, max_depth=4)
        assert result.startswith("any of:")
        assert "string" in result

    def test_all_of(self) -> None:
        schema = {
            "allOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}},
                {"type": "object", "properties": {"b": {"type": "integer"}}},
            ]
        }
        result = self.handler._expand_schema(schema, max_depth=4)
        assert result.startswith("all of:")
        assert "a: string" in result
        assert "b: integer" in result

    def test_enum(self) -> None:
        schema = {"type": "string", "enum": ["red", "green", "blue"]}
        result = self.handler._expand_schema(schema, max_depth=4)
        assert "one of:" in result
        assert "red" in result

    def test_enum_without_type(self) -> None:
        schema = {"enum": [1, 2, 3]}
        result = self.handler._expand_schema(schema, max_depth=4)
        assert "one of:" in result

    def test_depth_cap_fires(self) -> None:
        """At max_depth, expand_schema returns the depth-cap sentinel."""
        schema = {"type": "string"}
        result = self.handler._expand_schema(schema, depth=4, max_depth=4)
        assert "depth 4 reached" in result

    def test_depth_cap_in_nested_array(self) -> None:
        """Nesting deeper than max_depth caps gracefully."""
        # 5 levels of nesting; max_depth=2 should cap at depth 2
        schema: dict[str, Any] = {"type": "string"}
        for _ in range(5):
            schema = {"type": "array", "items": schema}
        result = self.handler._expand_schema(schema, depth=0, max_depth=2)
        assert "depth 2 reached" in result

    def test_empty_schema_returns_any(self) -> None:
        assert self.handler._expand_schema({}, max_depth=4) == "any"

    def test_non_dict_returns_any(self) -> None:
        result = self.handler._expand_schema("not a dict", max_depth=4)  # type: ignore[arg-type]
        assert result == "any"

    def test_max_depth_from_settings(self, tmp_path: Path) -> None:
        """_get_max_schema_depth reads from LoaderSettings."""
        settings = _make_settings(tmp_path, openapi_max_schema_depth=2)
        handler = OpenAPIHandler(settings=settings)
        assert handler._get_max_schema_depth() == 2

    def test_max_depth_default_is_4(self) -> None:
        """Default max depth is 4 when no settings are provided."""
        handler = OpenAPIHandler()
        assert handler._get_max_schema_depth() == 4

    def test_schema_expansion_used_in_operation_parameters(self, tmp_path: Path) -> None:
        """_format_operation uses _expand_schema instead of flat type lookup."""
        spec: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1"},
            "paths": {
                "/test": {
                    "get": {
                        "operationId": "test",
                        "parameters": [
                            {
                                "name": "ids",
                                "in": "query",
                                "schema": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        _write_spec(tmp_path / "openapi.json", spec)
        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(tmp_path, settings)
        op_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "operation"]
        assert len(op_chunks) == 1
        # With recursive expansion: "array of string", not "any"
        assert "array of string" in op_chunks[0]["content"]

    def test_schema_expansion_used_in_format_schemas(self, tmp_path: Path) -> None:
        """_format_schemas uses _expand_schema for property types."""
        spec: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
            "components": {
                "schemas": {
                    "Widget": {
                        "type": "object",
                        "properties": {
                            "ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "status": {
                                "type": "string",
                                "enum": ["active", "inactive"],
                            },
                        },
                    }
                }
            },
        }
        _write_spec(tmp_path / "openapi.json", spec)
        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(tmp_path, settings)
        schema_chunks = [d for d in docs if d["metadata"].get("chunk_type") == "schemas"]
        assert len(schema_chunks) == 1
        content = schema_chunks[0]["content"]
        assert "array of integer" in content
        assert "one of:" in content  # enum expansion


# ---------------------------------------------------------------------------
# Task 3: Hard-require jsonref
# ---------------------------------------------------------------------------


class TestHardRequireJsonref:
    """jsonref failures raise OperationError; process() catches it cleanly."""

    def test_jsonref_import_error_raises_operation_error(self) -> None:
        """_resolve_refs raises OperationError when jsonref is not importable."""
        from unittest.mock import patch

        handler = OpenAPIHandler()
        spec = {"openapi": "3.0.0", "info": {"title": "T", "version": "1"}, "paths": {}}

        import builtins

        real_import = builtins.__import__

        def block_jsonref(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "jsonref":
                raise ImportError("No module named 'jsonref'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=block_jsonref):
            with pytest.raises(OperationError, match="jsonref"):
                handler._resolve_refs(spec)

    def test_jsonref_exception_during_load_raises_operation_error(self, tmp_path: Path) -> None:
        """When jsonref.loads raises, _resolve_refs wraps it in OperationError."""
        from unittest.mock import MagicMock, patch

        handler = OpenAPIHandler()
        spec = {"openapi": "3.0.0", "paths": {}}

        mock_jsonref = MagicMock()
        mock_jsonref.loads.side_effect = ValueError("circular ref")

        with patch.dict("sys.modules", {"jsonref": mock_jsonref}):
            with pytest.raises(OperationError, match="circular ref"):
                handler._resolve_refs(spec)

    def test_process_catches_operation_error_returns_synthetic_doc(self, tmp_path: Path) -> None:
        """When _resolve_refs raises OperationError, process() returns a synthetic doc."""
        spec: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1"},
            "paths": {},
        }
        _write_spec(tmp_path / "openapi.json", spec)

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)

        def raise_op_error(s: dict[str, Any]) -> dict[str, Any]:
            raise OperationError(
                "Failed to resolve $ref references: circular", operation="archive_load"
            )

        handler._resolve_refs = raise_op_error  # type: ignore[method-assign]
        docs = handler.process(tmp_path, settings)

        assert len(docs) == 1
        meta = docs[0]["metadata"]
        assert meta.get("loader_files_skipped") == 1
        assert docs[0]["content"] == ""
        assert len(meta.get("loader_warnings", [])) >= 1

    def test_jsonref_is_direct_dependency(self) -> None:
        """Jsonref can be imported — it is a direct (or transitive) dependency."""
        import jsonref

        assert jsonref is not None


# ---------------------------------------------------------------------------
# Task 4: Per-section coverage tracking + multi-spec archives
# ---------------------------------------------------------------------------


class TestSectionCoverageTracking:
    """openapi_sections_emitted metadata is accurate for each processed spec."""

    def _process(self, tmp_path: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
        _write_spec(tmp_path / "openapi.json", spec)
        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        return handler.process(tmp_path, settings)

    def test_sections_emitted_present_on_all_docs(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        for doc in docs:
            assert "openapi_sections_emitted" in doc["metadata"], (
                f"chunk_type={doc['metadata'].get('chunk_type')} missing openapi_sections_emitted"
            )

    def test_sections_emitted_lists_paths(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        sections = docs[0]["metadata"]["openapi_sections_emitted"]
        assert "paths" in sections

    def test_sections_emitted_lists_schemas(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        sections = docs[0]["metadata"]["openapi_sections_emitted"]
        assert "schemas" in sections

    def test_sections_emitted_lists_security_schemes(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        sections = docs[0]["metadata"]["openapi_sections_emitted"]
        assert "security_schemes" in sections

    def test_sections_emitted_lists_parameters(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        sections = docs[0]["metadata"]["openapi_sections_emitted"]
        assert "parameters" in sections

    def test_sections_emitted_lists_responses(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        sections = docs[0]["metadata"]["openapi_sections_emitted"]
        assert "responses" in sections

    def test_sections_emitted_lists_request_bodies(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        sections = docs[0]["metadata"]["openapi_sections_emitted"]
        assert "request_bodies" in sections

    def test_sections_emitted_lists_tags(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        sections = docs[0]["metadata"]["openapi_sections_emitted"]
        assert "tags" in sections

    def test_sections_emitted_lists_callbacks(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        sections = docs[0]["metadata"]["openapi_sections_emitted"]
        assert "callbacks" in sections

    def test_sections_emitted_lists_webhooks(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        sections = docs[0]["metadata"]["openapi_sections_emitted"]
        assert "webhooks" in sections

    def test_sections_emitted_lists_examples(self, tmp_path: Path) -> None:
        docs = self._process(tmp_path, _FULL_SPEC)
        sections = docs[0]["metadata"]["openapi_sections_emitted"]
        assert "examples" in sections

    def test_absent_sections_not_listed(self, tmp_path: Path) -> None:
        """Sections with no content are not included in openapi_sections_emitted."""
        minimal: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "Minimal", "version": "1.0.0"},
            "paths": {},
        }
        docs = self._process(tmp_path, minimal)
        assert len(docs) >= 1
        sections = docs[0]["metadata"]["openapi_sections_emitted"]
        for absent in (
            "schemas",
            "security_schemes",
            "parameters",
            "responses",
            "request_bodies",
            "tags",
            "callbacks",
            "webhooks",
            "examples",
        ):
            assert absent not in sections, f"{absent} should not be listed for minimal spec"


class TestFindSpecFiles:
    """_find_spec_files returns all matching spec files."""

    def test_single_spec_at_root(self, tmp_path: Path) -> None:
        (tmp_path / "openapi.json").write_text("{}")
        handler = OpenAPIHandler()
        found = handler._find_spec_files(tmp_path)
        assert len(found) == 1

    def test_multiple_specs_same_root(self, tmp_path: Path) -> None:
        """openapi.json and swagger.json at root are both found."""
        (tmp_path / "openapi.json").write_text("{}")
        (tmp_path / "swagger.json").write_text("{}")
        handler = OpenAPIHandler()
        found = handler._find_spec_files(tmp_path)
        assert len(found) == 2

    def test_spec_in_subdirectory(self, tmp_path: Path) -> None:
        sub = tmp_path / "vendor"
        sub.mkdir()
        (sub / "openapi.json").write_text("{}")
        handler = OpenAPIHandler()
        found = handler._find_spec_files(tmp_path)
        assert len(found) == 1
        assert found[0] == sub / "openapi.json"

    def test_root_spec_and_subdirectory_spec(self, tmp_path: Path) -> None:
        """Root spec + subdirectory spec: both discovered."""
        (tmp_path / "openapi.json").write_text("{}")
        sub = tmp_path / "vendor"
        sub.mkdir()
        (sub / "openapi.yaml").write_text("{}")
        handler = OpenAPIHandler()
        found = handler._find_spec_files(tmp_path)
        assert len(found) == 2

    def test_no_specs_returns_empty_list(self, tmp_path: Path) -> None:
        handler = OpenAPIHandler()
        found = handler._find_spec_files(tmp_path)
        assert found == []

    def test_find_spec_file_returns_first(self, tmp_path: Path) -> None:
        """_find_spec_file (singular) returns the first result."""
        (tmp_path / "openapi.json").write_text("{}")
        (tmp_path / "swagger.json").write_text("{}")
        handler = OpenAPIHandler()
        single = handler._find_spec_file(tmp_path)
        assert single is not None

    def test_find_spec_file_returns_none_when_empty(self, tmp_path: Path) -> None:
        handler = OpenAPIHandler()
        assert handler._find_spec_file(tmp_path) is None


class TestMultiSpecArchive:
    """Multi-spec archives produce one set of documents per spec."""

    def _make_valid_spec(self, title: str) -> dict[str, Any]:
        return {
            "openapi": "3.0.0",
            "info": {"title": title, "version": "1.0.0"},
            "paths": {
                "/ping": {
                    "get": {
                        "operationId": "ping",
                        "responses": {"200": {"description": "pong"}},
                    }
                }
            },
        }

    def test_two_specs_produce_documents_from_both(self, tmp_path: Path) -> None:
        """An archive with two valid specs produces chunks from each."""
        _write_spec(tmp_path / "openapi.json", self._make_valid_spec("API One"))
        sub = tmp_path / "vendor"
        sub.mkdir()
        _write_spec(sub / "openapi.yaml", self._make_valid_spec("API Two"))

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(tmp_path, settings)

        titles = {d["metadata"].get("title") for d in docs}
        assert "API One" in titles
        assert "API Two" in titles

    def test_each_spec_has_own_source_metadata(self, tmp_path: Path) -> None:
        """Documents from different specs carry different 'source' metadata values."""
        _write_spec(tmp_path / "openapi.json", self._make_valid_spec("A"))
        sub = tmp_path / "vendor"
        sub.mkdir()
        _write_spec(sub / "openapi.json", self._make_valid_spec("B"))

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(tmp_path, settings)

        sources = {d["metadata"].get("source") for d in docs}
        # Should have two distinct source paths
        assert len(sources) == 2

    def test_bad_spec_in_multi_archive_does_not_block_good_spec(self, tmp_path: Path) -> None:
        """A broken spec file produces a synthetic error doc; the valid spec still processes."""
        _write_spec(tmp_path / "openapi.json", self._make_valid_spec("GoodAPI"))
        sub = tmp_path / "vendor"
        sub.mkdir()
        (sub / "openapi.json").write_text("{{{invalid json")

        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(tmp_path, settings)

        # Good spec docs + one synthetic error doc
        error_docs = [d for d in docs if d.get("content") == ""]
        good_docs = [d for d in docs if d.get("content") != ""]
        assert len(error_docs) == 1
        assert len(good_docs) >= 1

    def test_single_spec_still_works(self, tmp_path: Path) -> None:
        """Single-spec archives work unchanged (backward compat)."""
        _write_spec(tmp_path / "openapi.json", self._make_valid_spec("Solo"))
        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(tmp_path, settings)
        assert len(docs) >= 1
        assert any(d["metadata"].get("title") == "Solo" for d in docs)


# ---------------------------------------------------------------------------
# Task 2.7 — Unresolved $ref must raise, not stringify
# ---------------------------------------------------------------------------


class TestExpandSchemaUnresolvedRef:
    """Phase 7 audit-remediation: _expand_schema must raise OperationError on
    unresolved $ref instead of silently returning the basename as a string.
    """

    def test_openapi_handler_raises_on_unresolved_ref(self) -> None:
        """Unresolved $ref in schema must raise OperationError, not silently
        stringify the ref path (audit finding P2, 2026-05-09).
        """
        schema: dict[str, Any] = {"$ref": "#/components/schemas/MissingType"}

        handler = OpenAPIHandler()
        with pytest.raises(OperationError, match="[Uu]nresolved.*\\$ref"):
            handler._expand_schema(schema, depth=0, max_depth=4)

    def test_resolved_ref_is_not_affected(self, tmp_path: Path) -> None:
        """A spec whose $refs are fully resolved by _resolve_refs must still
        process without error (regression guard).
        """
        spec: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {"title": "RefTest", "version": "1.0.0"},
            "paths": {
                "/ping": {
                    "get": {
                        "operationId": "ping",
                        "summary": "Ping",
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Pong"}
                                    }
                                },
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {"Pong": {"type": "object", "properties": {"msg": {"type": "string"}}}}
            },
        }
        _write_spec(tmp_path / "openapi.json", spec)
        handler = OpenAPIHandler()
        settings = _make_settings(tmp_path)
        docs = handler.process(tmp_path, settings)
        assert any("ping" in (d.get("content") or "").lower() for d in docs)


# ---------------------------------------------------------------------------
# Security — external $ref resolution is blocked (SSRF / local-file-read)
# ---------------------------------------------------------------------------


class TestExternalRefRejected:
    """_resolve_refs must not dereference out-of-document ``$ref`` targets.

    jsonref's default loader fetches ``http(s)://`` (via requests) and reads
    ``file://`` / other URIs (via urlopen), with none of the codebase's SSRF
    defenses applied. A hostile spec extracted from an uploaded archive could
    otherwise read ``file:///data/secrets/*`` or probe LAN/metadata hosts and
    inline the response into indexed document text.
    """

    def test_file_uri_ref_raises_operation_error(self, tmp_path: Path) -> None:
        """A ``file://`` $ref must be refused, not read off disk."""
        secret = tmp_path / "secret.json"
        secret.write_text('{"leaked": "TOP_SECRET"}', encoding="utf-8")
        spec: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {"title": "Evil", "version": "1"},
            "paths": {"/x": {"get": {"responses": {"200": {"schema": {"$ref": secret.as_uri()}}}}}},
        }

        handler = OpenAPIHandler()
        with pytest.raises(OperationError, match="external OpenAPI \\$ref"):
            handler._resolve_refs(spec)

    def test_http_uri_ref_raises_operation_error(self) -> None:
        """An ``http://`` $ref must be refused, not fetched (SSRF guard)."""
        spec: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {"title": "Evil", "version": "1"},
            "paths": {
                "/y": {
                    "get": {
                        "responses": {
                            "200": {"schema": {"$ref": "http://169.254.169.254/latest/meta-data/"}}
                        }
                    }
                }
            },
        }

        handler = OpenAPIHandler()
        with pytest.raises(OperationError, match="external OpenAPI \\$ref"):
            handler._resolve_refs(spec)

    def test_internal_ref_still_resolves(self) -> None:
        """In-document ``#/...`` refs resolve without touching the loader."""
        spec: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {"title": "Good", "version": "1"},
            "paths": {
                "/z": {
                    "get": {"responses": {"200": {"schema": {"$ref": "#/components/schemas/Pet"}}}}
                }
            },
            "components": {
                "schemas": {"Pet": {"type": "object", "properties": {"id": {"type": "string"}}}}
            },
        }

        handler = OpenAPIHandler()
        resolved = handler._resolve_refs(spec)
        schema = resolved["paths"]["/z"]["get"]["responses"]["200"]["schema"]
        assert schema == {"type": "object", "properties": {"id": {"type": "string"}}}
