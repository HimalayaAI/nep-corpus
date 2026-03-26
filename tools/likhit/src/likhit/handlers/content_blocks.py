"""Helpers for mixing paragraph and table content blocks."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from likhit.extractors.base import TextFragment
from likhit.models import ParagraphBlock, Table, TableBlock, TableRegion
from likhit.models.types import ContentBlock

ParagraphBuilder = Callable[[list[TextFragment]], list[str]]


def build_content_blocks(
    fragments: list[TextFragment],
    tables: list[Table],
    paragraph_builder: ParagraphBuilder,
) -> list[ContentBlock]:
    """Build ordered content blocks from fragments and accepted tables."""

    if not fragments and not tables:
        return []

    blocks: list[ContentBlock] = []
    fragments_by_page: dict[int, list[TextFragment]] = defaultdict(list)
    for fragment in fragments:
        fragments_by_page[fragment.page_number].append(fragment)

    insertions_by_page: dict[int, list[tuple[int, Table]]] = defaultdict(list)
    for table in tables:
        anchor_region = table.regions[0]
        page_fragments = fragments_by_page.get(anchor_region.page_number, [])
        insertion_index = _find_insertion_index(page_fragments, anchor_region)
        insertions_by_page[anchor_region.page_number].append((insertion_index, table))

    pages = sorted(
        set(fragments_by_page.keys())
        | {region.page_number for table in tables for region in table.regions}
    )
    for page_number in pages:
        page_fragments = fragments_by_page.get(page_number, [])
        excluded_indexes = {
            index
            for index, fragment in enumerate(page_fragments)
            if any(
                _fragment_in_region(fragment, region)
                for table in tables
                for region in table.regions
                if region.page_number == page_number
            )
        }
        insertions = sorted(
            insertions_by_page.get(page_number, []),
            key=lambda item: (item[0], item[1].regions[0].y0),
        )

        current_chunk: list[TextFragment] = []
        insertion_cursor = 0
        for index in range(len(page_fragments) + 1):
            while (
                insertion_cursor < len(insertions)
                and insertions[insertion_cursor][0] == index
            ):
                _flush_paragraph_chunk(blocks, current_chunk, paragraph_builder)
                blocks.append(TableBlock(insertions[insertion_cursor][1]))
                insertion_cursor += 1

            if index == len(page_fragments):
                continue

            if index not in excluded_indexes:
                current_chunk.append(page_fragments[index])

        _flush_paragraph_chunk(blocks, current_chunk, paragraph_builder)

    return blocks


def blocks_to_text(blocks: list[ContentBlock]) -> str:
    """Create a plain-text fallback body from content blocks."""

    parts: list[str] = []
    for block in blocks:
        if isinstance(block, ParagraphBlock):
            parts.append(block.text)
        else:
            parts.append(table_to_plain_text(block.table))
    return "\n\n".join(part.strip() for part in parts if part.strip()).strip()


def table_to_plain_text(table: Table) -> str:
    """Flatten a table to plain text for non-rendered use."""

    lines: list[str] = []
    if table.caption:
        lines.append(table.caption)

    grid = [["" for _ in range(table.col_count)] for _ in range(table.row_count)]
    for cell in table.cells:
        grid[cell.row][cell.col] = cell.text.replace("\n", " ").strip()

    for row in grid:
        if any(cell for cell in row):
            lines.append("\t".join(row).rstrip())
    return "\n".join(line for line in lines if line.strip())


def _flush_paragraph_chunk(
    blocks: list[ContentBlock],
    chunk: list[TextFragment],
    paragraph_builder: ParagraphBuilder,
) -> None:
    if not chunk:
        return

    paragraphs = paragraph_builder(chunk)
    blocks.extend(
        ParagraphBlock(text=paragraph) for paragraph in paragraphs if paragraph
    )
    chunk.clear()


def _find_insertion_index(
    page_fragments: list[TextFragment],
    region: TableRegion,
) -> int:
    for index, fragment in enumerate(page_fragments):
        if _fragment_in_region(fragment, region):
            return index
    for index, fragment in enumerate(page_fragments):
        if fragment.y0 >= region.y0:
            return index
    return len(page_fragments)


def _fragment_in_region(fragment: TextFragment, region: TableRegion) -> bool:
    center_x = (fragment.x0 + fragment.x1) / 2
    center_y = (fragment.y0 + fragment.y1) / 2
    return (
        region.x0 - 1.5 <= center_x <= region.x1 + 1.5
        and region.y0 - 1.5 <= center_y <= region.y1 + 1.5
    )
