"""Tests for RST→Markdown sanitization in the API docs generator."""

import textwrap

import griffe
from generate_api_docs import (
    escape_table_cell,
    format_signature_params,
    sanitize_docstring,
)


class TestEscapeTableCell:
    """Pipes inside table cells must be escaped so GFM doesn't split them."""

    def test_escapes_union_pipe(self):
        assert escape_table_cell("bytes | None") == "bytes \\| None"

    def test_leaves_plain_text_alone(self):
        assert escape_table_cell("str") == "str"


class TestSanitizeDocstring:
    """RST docstring idioms must convert to Markdown-safe equivalents."""

    def test_meth_role_becomes_code_span(self):
        assert (
            sanitize_docstring("Forwarded to :meth:`process_document`.")
            == "Forwarded to `process_document`."
        )

    def test_func_role_becomes_code_span(self):
        assert (
            sanitize_docstring("See :func:`chaoscypher_core.add_document_sync` for docs.")
            == "See `chaoscypher_core.add_document_sync` for docs."
        )

    def test_double_backticks_become_single(self):
        assert sanitize_docstring("Uses ``separator`` to join.") == "Uses `separator` to join."

    def test_trailing_double_colon_with_block_becomes_fence(self):
        source = textwrap.dedent("""\
            Example::

                async with adapter.session_scope():
                    pass
        """)
        result = sanitize_docstring(source)
        assert "Example:" in result
        assert "Example::" not in result
        assert "```" in result
        assert "async with adapter.session_scope():" in result

    def test_trailing_double_colon_without_block_collapses(self):
        assert (
            sanitize_docstring("lifecycle for text-only sources::")
            == "lifecycle for text-only sources:"
        )

    def test_inline_conversion_skips_fenced_code(self):
        source = textwrap.dedent("""\
            Prose with ``literal``.

            ```python
            print("``not-a-literal``")
            ```
        """)
        result = sanitize_docstring(source)
        assert "Prose with `literal`." in result
        assert 'print("``not-a-literal``")' in result

    def test_plain_markdown_untouched(self):
        source = "A `code` span and a [link](x.md).\n\n- bullet"
        assert sanitize_docstring(source) == source

    def test_literal_block_inside_args_section_keeps_next_arg(self):
        source = textwrap.dedent("""\
            Args:
                config: Mapping, e.g.::

                    {"key": "value"}

                other: second arg.
        """)
        result = sanitize_docstring(source)
        assert "config: Mapping, e.g.:" in result
        assert "e.g.::" not in result
        assert '{"key": "value"}' in result
        assert "other: second arg." in result
        # The fence must close before the next arg — it must not be swallowed.
        closing_fence = result.index("```", result.index('{"key"'))
        assert closing_fence < result.index("other: second arg.")


class TestFormatSignatureParams:
    """Signatures must preserve `/`, `*`, `*args`, and `**kwargs` markers."""

    @staticmethod
    def _func(*params):
        return griffe.Function("f", parameters=griffe.Parameters(*params))

    def test_keyword_only_gets_bare_star(self):
        func = self._func(
            griffe.Parameter("a", kind=griffe.ParameterKind.positional_or_keyword),
            griffe.Parameter("b", kind=griffe.ParameterKind.keyword_only),
        )
        assert format_signature_params(func) == "a, *, b"

    def test_var_positional_and_var_keyword(self):
        func = self._func(
            griffe.Parameter("args", kind=griffe.ParameterKind.var_positional),
            griffe.Parameter("kwargs", kind=griffe.ParameterKind.var_keyword),
        )
        assert format_signature_params(func) == "*args, **kwargs"

    def test_var_positional_suppresses_bare_star(self):
        func = self._func(
            griffe.Parameter("args", kind=griffe.ParameterKind.var_positional),
            griffe.Parameter("b", kind=griffe.ParameterKind.keyword_only),
        )
        assert format_signature_params(func) == "*args, b"

    def test_positional_only_gets_slash(self):
        func = self._func(
            griffe.Parameter("a", kind=griffe.ParameterKind.positional_only),
            griffe.Parameter("b", kind=griffe.ParameterKind.positional_or_keyword),
        )
        assert format_signature_params(func) == "a, /, b"

    def test_self_dropped_and_kwonly_annotation_default_kept(self):
        func = self._func(
            griffe.Parameter("self", kind=griffe.ParameterKind.positional_or_keyword),
            griffe.Parameter(
                "x",
                annotation="int",
                default="0",
                kind=griffe.ParameterKind.keyword_only,
            ),
        )
        assert format_signature_params(func) == "*, x: int = 0"

    def test_var_keyword_default_suppressed(self):
        func = self._func(
            griffe.Parameter(
                "kwargs",
                annotation="Any",
                default="{}",
                kind=griffe.ParameterKind.var_keyword,
            ),
        )
        assert format_signature_params(func) == "**kwargs: Any"
