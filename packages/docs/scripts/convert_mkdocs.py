"""One-time MkDocs to Docusaurus markdown conversion script.

Converts:
- Admonitions: !!! type "title" -> :::type[title]
- Collapsible: ??? type "title" -> <details><summary>
- Tabs: === "Tab" -> <Tabs>/<TabItem> JSX
- Frontmatter: strips MkDocs-specific keys
- Icons: removes :material-*: shortcodes
- Image paths: ../assets/screenshots/ -> /img/screenshots/
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


# MkDocs admonition types -> Docusaurus equivalents
TYPE_MAP = {
    "note": "note",
    "tip": "tip",
    "info": "info",
    "warning": "warning",
    "danger": "danger",
    "important": "warning",
    "abstract": "info",
    "success": "tip",
    "question": "info",
    "failure": "danger",
    "bug": "danger",
    "example": "info",
    "quote": "note",
}

# MkDocs-specific frontmatter keys to strip
STRIP_FM_KEYS = {"hide", "render_macros", "icon"}


def convert_admonitions(text: str) -> str:
    """Convert !!! and ??? admonitions to Docusaurus ::: or <details> syntax."""
    lines = text.split("\n")
    result: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        match = re.match(r'^(\?{3}|!{3})\s+(\w+)(?:\s+"(.*?)")?\s*$', line)

        if not match:
            result.append(line)
            i += 1
            continue

        marker, admon_type, title = match.group(1), match.group(2), match.group(3)
        docusaurus_type = TYPE_MAP.get(admon_type, "note")
        is_collapsible = marker == "???"

        # Collect indented content (4 spaces)
        content_lines: list[str] = []
        i += 1
        while i < len(lines):
            if lines[i].startswith("    "):
                content_lines.append(lines[i][4:])  # de-indent
                i += 1
            elif lines[i].strip() == "":
                # Blank line could be inside or after the admonition
                # Look ahead to see if next non-blank line is still indented
                j = i + 1
                while j < len(lines) and lines[j].strip() == "":
                    j += 1
                if j < len(lines) and lines[j].startswith("    "):
                    content_lines.append("")
                    i += 1
                else:
                    break
            else:
                break

        content = "\n".join(content_lines).rstrip()

        if is_collapsible:
            display_title = title if title else docusaurus_type.capitalize()
            result.append("<details>")
            result.append(f"<summary>{display_title}</summary>")
            result.append("")
            result.append(content)
            result.append("")
            result.append("</details>")
        else:
            title_part = f"[{title}]" if title else ""
            result.append(f":::{docusaurus_type}{title_part}")
            result.append("")
            result.append(content)
            result.append("")
            result.append(":::")

    return "\n".join(result)


def convert_tabs(text: str) -> str:
    """Convert === "Tab" syntax to Docusaurus <Tabs>/<TabItem> JSX."""
    lines = text.split("\n")
    result: list[str] = []
    has_tabs = False
    i = 0

    while i < len(lines):
        line = lines[i]
        match = re.match(r'^=== "(.+?)"\s*$', line)

        if not match:
            result.append(line)
            i += 1
            continue

        # Start of a tab group -- collect all consecutive tabs
        has_tabs = True
        tabs: list[tuple[str, str, str]] = []

        while i < len(lines):
            tab_match = re.match(r'^=== "(.+?)"\s*$', lines[i])
            if not tab_match:
                break

            tab_label = tab_match.group(1)
            tab_value = re.sub(r"[^a-z0-9]+", "-", tab_label.lower()).strip("-")

            # Collect indented content
            content_lines: list[str] = []
            i += 1
            while i < len(lines):
                if lines[i].startswith("    "):
                    content_lines.append(lines[i][4:])  # de-indent
                    i += 1
                elif lines[i].strip() == "":
                    j = i + 1
                    while j < len(lines) and lines[j].strip() == "":
                        j += 1
                    if j < len(lines) and lines[j].startswith("    "):
                        content_lines.append("")
                        i += 1
                    elif j < len(lines) and re.match(r'^=== "', lines[j]):
                        # Blank before next tab -- skip
                        i = j
                        break
                    else:
                        break
                else:
                    break

            content = "\n".join(content_lines).rstrip()
            tabs.append((tab_label, tab_value, content))

        # Emit JSX
        result.append("<Tabs>")
        for tab_label, tab_value, content in tabs:
            result.append(f'<TabItem value="{tab_value}" label="{tab_label}">')
            result.append("")
            result.append(content)
            result.append("")
            result.append("</TabItem>")
        result.append("</Tabs>")
        result.append("")

    if has_tabs:
        # Prepend imports if not already present
        import_lines = [
            "import Tabs from '@theme/Tabs';",
            "import TabItem from '@theme/TabItem';",
            "",
        ]
        # Insert after frontmatter if present
        text_result = "\n".join(result)
        if text_result.startswith("---"):
            end_fm = text_result.index("---", 3) + 3
            return text_result[:end_fm] + "\n\n" + "\n".join(import_lines) + text_result[end_fm:]
        return "\n".join(import_lines) + "\n" + text_result

    return "\n".join(result)


def strip_frontmatter_keys(text: str) -> str:
    """Remove MkDocs-specific frontmatter keys."""
    if not text.startswith("---"):
        return text

    end_idx = text.index("---", 3)
    fm_block = text[3:end_idx].strip()
    rest = text[end_idx + 3 :]

    new_lines: list[str] = []
    skip_block = False
    for line in fm_block.split("\n"):
        key = line.split(":")[0].strip() if ":" in line else ""
        if key in STRIP_FM_KEYS:
            skip_block = True
            continue
        if skip_block and line.startswith(("  ", "\t")):
            continue  # Skip indented sub-values of stripped key
        skip_block = False
        new_lines.append(line)

    new_fm = "\n".join(new_lines).strip()
    if new_fm:
        return f"---\n{new_fm}\n---{rest}"
    return rest.lstrip("\n")


def convert_icon_shortcodes(text: str) -> str:
    """Remove :material-*:, :octicons-*:, :fontawesome-*:, :simple-*: shortcodes."""
    return re.sub(r":(?:material|octicons|fontawesome|simple)-[\w-]+:\s*", "", text)


def convert_image_paths(text: str) -> str:
    """Convert ../assets/screenshots/ to /img/screenshots/ and ../assets/ to /img/."""
    text = re.sub(
        r"\.\./assets/screenshots/",
        "/img/screenshots/",
        text,
    )
    return re.sub(
        r"\.\./assets/(logo|favicon)",
        r"/img/\1",
        text,
    )


def convert_file_content(text: str) -> str:
    """Run all conversions on a markdown file's content."""
    text = strip_frontmatter_keys(text)
    text = convert_icon_shortcodes(text)
    text = convert_image_paths(text)
    text = convert_admonitions(text)
    return convert_tabs(text)


def convert_blog_post(src_file: Path, dst_dir: Path) -> None:
    """Convert a MkDocs blog post to Docusaurus format."""
    content = src_file.read_text(encoding="utf-8")

    # Extract frontmatter
    if not content.startswith("---"):
        return
    end_fm = content.index("---", 3)
    fm_text = content[3:end_fm].strip()
    body = content[end_fm + 3 :].strip()

    # Parse frontmatter fields
    date = ""
    draft = False
    categories: list[str] = []
    in_categories = False
    for line in fm_text.split("\n"):
        if line.startswith("date:"):
            date = line.split(":", 1)[1].strip()
        elif line.startswith("draft:"):
            draft = line.split(":", 1)[1].strip().lower() == "true"
        elif line.startswith("categories:"):
            in_categories = True
        elif in_categories and line.strip().startswith("- "):
            categories.append(line.strip()[2:])
        elif not line.startswith("  ") and not line.startswith("\t"):
            in_categories = False

    # Extract title from first H1
    title_match = re.match(r"^# (.+)$", body, re.MULTILINE)
    title = title_match.group(1) if title_match else src_file.stem
    # Remove the H1 from body (Docusaurus uses frontmatter title)
    if title_match:
        body = body[: title_match.start()] + body[title_match.end() :]
        body = body.lstrip("\n")

    # Build slug from filename
    slug = src_file.stem

    # Build tags from categories
    tags = [re.sub(r"\s+", "-", c.lower()) for c in categories]

    # Build new frontmatter
    new_fm_lines = [
        f"slug: {slug}",
        f'title: "{title}"',
        "authors: [denis]",
    ]
    if tags:
        new_fm_lines.append(f"tags: [{', '.join(tags)}]")
    new_fm_lines.append(f"date: {date}")
    if draft:
        new_fm_lines.append("draft: true")

    new_fm = "\n".join(new_fm_lines)

    # Run content conversions on body
    body = convert_admonitions(body)
    body = convert_icon_shortcodes(body)
    body = convert_image_paths(body)

    # Write output
    dst_file = dst_dir / f"{date}-{slug}.md"
    dst_file.write_text(f"---\n{new_fm}\n---\n\n{body}", encoding="utf-8")
    print(f"  Blog: {src_file.name} -> {dst_file.name}")


def convert_directory(src_dir: Path, dst_dir: Path) -> None:
    """Convert all markdown files from src_dir to dst_dir."""
    for src_file in sorted(src_dir.rglob("*.md")):
        # Skip blog files (handled separately) and index.md (landing page)
        rel = src_file.relative_to(src_dir)
        if str(rel).startswith("blog"):
            continue
        if rel == Path("index.md"):
            continue

        content = src_file.read_text(encoding="utf-8")
        converted = convert_file_content(content)

        dst_file = dst_dir / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        dst_file.write_text(converted, encoding="utf-8")
        print(f"  Converted: {rel}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python convert_mkdocs.py <source_docs_dir> <dest_docs_dir>")
        sys.exit(1)

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])

    if not src.is_dir():
        print(f"Error: {src} is not a directory")
        sys.exit(1)

    print(f"Converting {src} -> {dst}")
    convert_directory(src, dst)
    print("Done.")
