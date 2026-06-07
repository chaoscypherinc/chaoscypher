"""Tests for MkDocs to Docusaurus markdown conversion."""

import textwrap

from convert_mkdocs import convert_admonitions, convert_file_content, convert_tabs


class TestAdmonitions:
    """Test admonition conversion from !!! syntax to ::: syntax."""

    def test_basic_note(self):
        source = textwrap.dedent("""\
            !!! note
                This is a note.
        """)
        expected = textwrap.dedent("""\
            :::note

            This is a note.

            :::
        """)
        assert convert_admonitions(source) == expected

    def test_note_with_title(self):
        source = textwrap.dedent("""\
            !!! note "Custom Title"
                This is a note with a title.
        """)
        expected = textwrap.dedent("""\
            :::note[Custom Title]

            This is a note with a title.

            :::
        """)
        assert convert_admonitions(source) == expected

    def test_collapsible(self):
        source = textwrap.dedent("""\
            ??? tip "Click to expand"
                Hidden content here.
        """)
        expected = textwrap.dedent("""\
            <details>
            <summary>Click to expand</summary>

            Hidden content here.

            </details>
        """)
        assert convert_admonitions(source) == expected

    def test_collapsible_no_title(self):
        source = textwrap.dedent("""\
            ??? note
                Hidden note.
        """)
        expected = textwrap.dedent("""\
            <details>
            <summary>Note</summary>

            Hidden note.

            </details>
        """)
        assert convert_admonitions(source) == expected

    def test_type_mapping_important(self):
        source = textwrap.dedent("""\
            !!! important "Watch out"
                Something important.
        """)
        expected = textwrap.dedent("""\
            :::warning[Watch out]

            Something important.

            :::
        """)
        assert convert_admonitions(source) == expected

    def test_type_mapping_failure(self):
        source = textwrap.dedent("""\
            !!! failure "404 Not Found"
                Resource not found.
        """)
        expected = textwrap.dedent("""\
            :::danger[404 Not Found]

            Resource not found.

            :::
        """)
        assert convert_admonitions(source) == expected

    def test_multiline_content(self):
        source = textwrap.dedent("""\
            !!! tip "Multi"
                Line one.

                Line two.

                ```python
                code_here()
                ```
        """)
        expected = textwrap.dedent("""\
            :::tip[Multi]

            Line one.

            Line two.

            ```python
            code_here()
            ```

            :::
        """)
        assert convert_admonitions(source) == expected

    def test_admonition_followed_by_text(self):
        source = textwrap.dedent("""\
            !!! note
                Inside admonition.

            Outside admonition.
        """)
        expected = textwrap.dedent("""\
            :::note

            Inside admonition.

            :::

            Outside admonition.
        """)
        assert convert_admonitions(source) == expected


class TestTabs:
    """Test tab conversion from === syntax to Tabs/TabItem JSX."""

    def test_basic_tabs(self):
        source = textwrap.dedent("""\
            === "Python"
                ```python
                print("hello")
                ```

            === "CLI"
                ```bash
                echo hello
                ```
        """)
        result = convert_tabs(source)
        assert "<Tabs>" in result
        assert '<TabItem value="python" label="Python">' in result
        assert '<TabItem value="cli" label="CLI">' in result
        assert "</TabItem>" in result
        assert "</Tabs>" in result
        assert "```python" in result
        assert "```bash" in result

    def test_tabs_add_import(self):
        source = textwrap.dedent("""\
            Some text.

            === "Tab A"
                Content A.

            === "Tab B"
                Content B.
        """)
        result = convert_tabs(source)
        assert "import Tabs from '@theme/Tabs';" in result
        assert "import TabItem from '@theme/TabItem';" in result


class TestFullConversion:
    """Test the full file conversion pipeline."""

    def test_strips_mkdocs_frontmatter_keys(self):
        source = textwrap.dedent("""\
            ---
            hide:
              - navigation
              - toc
            render_macros: true
            ---

            # Title
        """)
        result = convert_file_content(source)
        assert "hide:" not in result
        assert "render_macros:" not in result
        assert "# Title" in result

    def test_icon_shortcodes_removed(self):
        source = "### :material-graph: Knowledge Graph"
        result = convert_file_content(source)
        assert ":material-graph:" not in result
        assert "Knowledge Graph" in result

    def test_image_path_conversion(self):
        source = "![Screenshot](../assets/screenshots/search.png)"
        result = convert_file_content(source)
        assert "/img/screenshots/search.png" in result
