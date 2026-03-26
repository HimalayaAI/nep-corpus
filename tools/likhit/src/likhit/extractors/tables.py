"""Native PDF table extraction helpers."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import re

from likhit.extractors.base import TextFragment
from likhit.models import Table, TableCell, TableRegion

_EDGE_TOLERANCE = 1.5


def detect_page_tables(
    page: object,
    page_fragments: list[TextFragment],
    index_offset: int = 0,
) -> list[Table]:
    """Extract accepted native PDF tables from a page."""

    if not hasattr(page, "find_tables"):
        return []

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        finder = page.find_tables()

    tables: list[Table] = []
    for offset, fitz_table in enumerate(finder.tables):
        table = _build_table(
            fitz_table,
            page_fragments,
            page_number=page.number + 1,
            page_height=float(page.rect.height),
            index=index_offset + offset,
        )
        if table is not None and _is_accepted_table(table):
            tables.append(table)
    return tables


def merge_continuation_tables(tables: list[Table]) -> list[Table]:
    """Merge obvious continuation tables across consecutive pages."""

    if not tables:
        return []

    ordered = sorted(tables, key=lambda table: (table.page_number, table.index))
    merged: list[Table] = []
    for table in ordered:
        if merged and _should_merge_tables(merged[-1], table):
            merged[-1] = _merge_table_pair(merged[-1], table)
            continue
        merged.append(table)
    return merged


def _build_table(
    fitz_table: object,
    page_fragments: list[TextFragment],
    *,
    page_number: int,
    page_height: float,
    index: int,
) -> Table | None:
    x_edges = _cluster_edges(
        [fitz_table.bbox[0], fitz_table.bbox[2]]
        + [cell[0] for row in fitz_table.rows for cell in row.cells if cell is not None]
        + [cell[2] for row in fitz_table.rows for cell in row.cells if cell is not None]
    )
    y_edges = _cluster_edges(
        [fitz_table.bbox[1], fitz_table.bbox[3]]
        + [cell[1] for row in fitz_table.rows for cell in row.cells if cell is not None]
        + [cell[3] for row in fitz_table.rows for cell in row.cells if cell is not None]
    )

    if (
        len(x_edges) != fitz_table.col_count + 1
        or len(y_edges) != fitz_table.row_count + 1
    ):
        return None

    cells: list[TableCell] = []
    for row_index, row in enumerate(fitz_table.rows):
        for col_index, bbox in enumerate(row.cells):
            if bbox is None:
                continue

            start_col = _closest_edge_index(x_edges, bbox[0])
            end_col = _closest_edge_index(x_edges, bbox[2])
            start_row = _closest_edge_index(y_edges, bbox[1])
            end_row = _closest_edge_index(y_edges, bbox[3])
            if (
                start_col is None
                or end_col is None
                or start_row is None
                or end_row is None
                or end_col <= start_col
                or end_row <= start_row
            ):
                return None

            text = _extract_cell_text(page_fragments, bbox)
            cells.append(
                TableCell(
                    row=start_row,
                    col=start_col,
                    text=text,
                    rowspan=end_row - start_row,
                    colspan=end_col - start_col,
                )
            )

    return Table(
        row_count=fitz_table.row_count,
        col_count=fitz_table.col_count,
        cells=cells,
        caption=_extract_caption(fitz_table),
        index=index,
        regions=[
            TableRegion(
                page_number=page_number,
                x0=float(fitz_table.bbox[0]),
                y0=float(fitz_table.bbox[1]),
                x1=float(fitz_table.bbox[2]),
                y1=float(fitz_table.bbox[3]),
                page_height=page_height,
            )
        ],
    )


def _extract_caption(fitz_table: object) -> str | None:
    header = getattr(fitz_table, "header", None)
    if header is None or not getattr(header, "external", False):
        return None

    caption_parts = [part.strip() for part in header.names if part and part.strip()]
    if not caption_parts:
        return None
    return " ".join(dict.fromkeys(caption_parts))


def _extract_cell_text(
    page_fragments: list[TextFragment],
    bbox: tuple[float, float, float, float],
) -> str:
    matching = [
        fragment
        for fragment in page_fragments
        if _fragment_center_in_bbox(fragment, bbox)
    ]
    matching.sort(key=lambda fragment: (fragment.y0, fragment.x0))

    lines: list[str] = []
    for fragment in matching:
        text = _normalize_cell_text(fragment.text)
        if not text:
            continue
        if not lines or lines[-1] != text:
            lines.append(text)
    return "\n".join(lines)


def _normalize_cell_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _fragment_center_in_bbox(
    fragment: TextFragment,
    bbox: tuple[float, float, float, float],
) -> bool:
    center_x = (fragment.x0 + fragment.x1) / 2
    center_y = (fragment.y0 + fragment.y1) / 2
    return (
        bbox[0] - _EDGE_TOLERANCE <= center_x <= bbox[2] + _EDGE_TOLERANCE
        and bbox[1] - _EDGE_TOLERANCE <= center_y <= bbox[3] + _EDGE_TOLERANCE
    )


def _cluster_edges(values: list[float]) -> list[float]:
    if not values:
        return []

    ordered = sorted(float(value) for value in values)
    clusters: list[list[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        if abs(value - clusters[-1][-1]) <= _EDGE_TOLERANCE:
            clusters[-1].append(value)
            continue
        clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _closest_edge_index(edges: list[float], target: float) -> int | None:
    for index, edge in enumerate(edges):
        if abs(edge - target) <= _EDGE_TOLERANCE:
            return index
    return None


def _is_accepted_table(table: Table) -> bool:
    if table.row_count < 2 or table.col_count < 2:
        return False

    nonempty_cells = [cell for cell in table.cells if cell.text.strip()]
    if len(nonempty_cells) < 2:
        return False

    populated_rows = {cell.row for cell in nonempty_cells}
    populated_cols = {cell.col for cell in nonempty_cells}
    return len(populated_rows) >= 2 and len(populated_cols) >= 2


def _should_merge_tables(current: Table, next_table: Table) -> bool:
    current_region = current.regions[-1]
    next_region = next_table.regions[0]

    if next_region.page_number != current_region.page_number + 1:
        return False
    if current.col_count != next_table.col_count:
        return False

    if current.caption and next_table.caption and current.caption != next_table.caption:
        return False

    if abs(current_region.x0 - next_region.x0) > 6.0:
        return False
    if abs(current_region.x1 - next_region.x1) > 6.0:
        return False

    current_bottom_cutoff = current_region.page_height * 0.7
    next_top_cutoff = next_region.page_height * 0.3
    if current_region.page_height and current_region.y1 < current_bottom_cutoff:
        return False
    if next_region.page_height and next_region.y0 > next_top_cutoff:
        return False

    return True


def _merge_table_pair(current: Table, next_table: Table) -> Table:
    drop_count = _shared_header_prefix(current, next_table)
    next_cells = []
    row_offset = current.row_count

    for cell in next_table.cells:
        if cell.row < drop_count:
            continue
        next_cells.append(
            TableCell(
                row=cell.row - drop_count + row_offset,
                col=cell.col,
                text=cell.text,
                rowspan=cell.rowspan,
                colspan=cell.colspan,
            )
        )

    return Table(
        row_count=current.row_count + max(next_table.row_count - drop_count, 0),
        col_count=current.col_count,
        cells=current.cells + next_cells,
        caption=current.caption or next_table.caption,
        index=current.index,
        regions=current.regions + next_table.regions,
    )


def _shared_header_prefix(current: Table, next_table: Table) -> int:
    current_rows = _table_row_signatures(current)
    next_rows = _table_row_signatures(next_table)
    max_prefix = min(5, len(current_rows), len(next_rows))

    for size in range(max_prefix, 0, -1):
        if current_rows[:size] == next_rows[:size] and size < next_table.row_count:
            return size
    return 0


def _table_row_signatures(table: Table) -> list[tuple[str, ...]]:
    rows = [["" for _ in range(table.col_count)] for _ in range(table.row_count)]
    for cell in table.cells:
        rows[cell.row][cell.col] = _normalize_signature_text(cell.text)
    return [tuple(row) for row in rows]


def _normalize_signature_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
