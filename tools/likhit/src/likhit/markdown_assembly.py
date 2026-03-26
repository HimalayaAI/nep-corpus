"""Generic Markdown assembly for the public conversion path."""

from __future__ import annotations

from likhit.handlers.content_blocks import table_to_plain_text
from likhit.models import Table
from likhit.models.repair_types import RepairedBlock


def assemble_markdown(blocks: list[RepairedBlock]) -> str:
    """Render ordered repaired blocks into editable Markdown."""

    rendered: list[str] = []

    for block in sorted(blocks, key=lambda item: item.order_index):
        if block.table is not None:
            rendered.append(_render_table_block(block.table))
            continue

        text = block.text.strip()
        if not text:
            continue

        if block.heading_level is not None:
            level = max(1, min(block.heading_level, 6))
            rendered.append(f"{'#' * level} {text}")
            continue

        if block.list_marker is not None:
            body = text[len(block.list_marker) :].strip()
            rendered.append(f"{block.list_marker} {body}")
            continue

        rendered.append(text)

    return "\n\n".join(part.strip() for part in rendered if part.strip()).strip()


def derive_markdown_title(blocks: list[RepairedBlock]) -> str | None:
    """Return a best-effort title for MarkItDown result metadata."""

    for block in sorted(blocks, key=lambda item: item.order_index):
        if block.heading_level == 1 and block.text.strip():
            return block.text.strip()
    for block in sorted(blocks, key=lambda item: item.order_index):
        if block.text.strip():
            return block.text.strip()[:120]
    return None


def _render_table_block(table: Table) -> str:
    if not _can_render_markdown_table(table):
        return table_to_plain_text(table)

    grid = [["" for _ in range(table.col_count)] for _ in range(table.row_count)]
    for cell in table.cells:
        grid[cell.row][cell.col] = _normalize_cell(cell.text)

    header = [
        _escape_pipes(cell or f"Column {index + 1}")
        for index, cell in enumerate(grid[0])
    ]
    rows = [
        [_escape_pipes(cell) for cell in row]
        for row in grid[1:]
        if any(cell.strip() for cell in row)
    ]

    lines: list[str] = []
    if table.caption:
        lines.append(table.caption.strip())
        lines.append("")

    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines).strip()


def _can_render_markdown_table(table: Table) -> bool:
    if table.row_count < 2 or table.col_count < 2:
        return False
    if any(cell.rowspan > 1 or cell.colspan > 1 for cell in table.cells):
        return False
    return True


def _normalize_cell(text: str) -> str:
    return " ".join(part.strip() for part in text.splitlines() if part.strip()).strip()


def _escape_pipes(text: str) -> str:
    return text.replace("|", "\\|")
