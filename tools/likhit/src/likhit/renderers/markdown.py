"""Markdown renderer for extracted documents."""

from __future__ import annotations

from collections import OrderedDict
import re

import yaml

from likhit.models import ExtractionResult, ParagraphBlock, Section, Table, TableBlock
from likhit.renderers.base import OutputRenderer

_SERIAL_PATTERN = re.compile(r"^[०-९0-9]+(?:[.)।])?$")


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _caption_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.replace("\uf001", "")).casefold()


def _normalize_header_label(text: str) -> str:
    compact = _clean_text(text).replace(" ", "")
    aliases = {
        "आयोगकोनिर्णय": "आयोगको निर्णय",
        "मिति/मुद्दानंर": "मिति/मुद्दा नं र",
    }
    return aliases.get(compact, _clean_text(text))


def _anchor_grid(table: Table) -> list[list[str]]:
    grid = [["" for _ in range(table.col_count)] for _ in range(table.row_count)]
    for cell in table.cells:
        grid[cell.row][cell.col] = " ".join(
            _clean_text(part) for part in cell.text.splitlines() if part.strip()
        )
    return grid


def _expanded_grid(table: Table) -> list[list[str]]:
    grid = [["" for _ in range(table.col_count)] for _ in range(table.row_count)]
    for cell in table.cells:
        text = " ".join(
            _clean_text(part) for part in cell.text.splitlines() if part.strip()
        )
        for row in range(cell.row, min(cell.row + cell.rowspan, table.row_count)):
            for col in range(cell.col, min(cell.col + cell.colspan, table.col_count)):
                if not grid[row][col]:
                    grid[row][col] = text
    return grid


def _is_record_key_header(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    return compact in {
        "सि.नं",
        "सि.नं.",
        "क्र.सं",
        "क्र.सं.",
        "क्रसं",
        "क्रसं.",
        "sn",
        "s.n.",
        "no",
        "no.",
    }


def _row_nonempty_values(row: list[str]) -> list[str]:
    return [cell.strip() for cell in row if cell.strip()]


def _looks_like_title_row(row: list[str]) -> bool:
    values = _row_nonempty_values(row)
    return len(row) >= 4 and bool(values) and len(set(values)) == 1


def _is_placeholder_header(text: str) -> bool:
    return bool(re.fullmatch(r"स्तम्भ \d+", text.strip()))


def _first_nonempty_cell(row: list[str]) -> str:
    for cell in row:
        if cell.strip():
            return cell.strip()
    return ""


def _looks_like_data_key(value: str) -> bool:
    return bool(_SERIAL_PATTERN.fullmatch(value.strip()))


def _title_row_count(expanded: list[list[str]]) -> int:
    count = 0
    for row in expanded:
        if not _looks_like_title_row(row):
            break
        count += 1
    return count


def _data_start(expanded: list[list[str]], title_rows: int) -> int:
    for index in range(title_rows, len(expanded)):
        if any(_looks_like_data_key(cell) for cell in expanded[index] if cell.strip()):
            return index
    return min(title_rows + 1, len(expanded))


def _header_parts(
    expanded: list[list[str]],
    title_rows: int,
    data_start: int,
) -> list[list[str]]:
    headers: list[list[str]] = []
    for col in range(len(expanded[0]) if expanded else 0):
        seen: list[str] = []
        for row in range(title_rows, data_start):
            value = expanded[row][col].strip()
            if value and value not in seen:
                seen.append(value)
        headers.append(seen or [f"स्तम्भ {col + 1}"])
    return headers


def _compose_headers(header_parts: list[list[str]]) -> list[str]:
    return [" / ".join(parts) for parts in header_parts]


def _find_key_column(expanded: list[list[str]], data_start: int) -> int | None:
    best_column: int | None = None
    best_count = 0
    for col in range(len(expanded[0]) if expanded else 0):
        count = sum(
            1
            for row in expanded[data_start:]
            if row[col].strip() and _looks_like_data_key(row[col])
        )
        if count > best_count:
            best_column = col
            best_count = count
    return best_column if best_count > 0 else None


def _collect_column_values(
    anchor_rows: list[list[str]],
    row_indexes: list[int],
    col: int,
) -> list[str]:
    values: list[str] = []
    for row_index in row_indexes:
        value = anchor_rows[row_index][col].strip()
        if value and value not in values:
            values.append(value)
    return values


def _column_samples(
    anchor_rows: list[list[str]],
    col: int,
    limit: int = 4,
) -> list[str]:
    values: list[str] = []
    for row in anchor_rows:
        value = row[col].strip()
        if value and value not in values:
            values.append(value)
        if len(values) >= limit:
            break
    return values


def _is_decision_value(value: str) -> bool:
    compact = value.replace(" ", "")
    return "/" in compact and ("CR-" in compact or "२०८" in compact or "208" in compact)


def _is_claim_value(value: str) -> bool:
    return "दफा" in value or "रु." in value or "रु" in value


def _is_person_value(value: str) -> bool:
    markers = (
        "अधिकृत",
        "इन्जिनियर",
        "अध्यक्ष",
        "सचिव",
        "सदस्य",
        "उपाध्यक्ष",
        "उपसचिव",
        "निर्देशक",
        "प्रमुख",
        "प्रसाद",
        "कुमार",
        "प्रा.लि.",
    )
    return any(marker in value for marker in markers)


def _infer_header_parts(
    anchor_rows: list[list[str]],
    header_parts: list[list[str]],
    key_col: int,
) -> list[list[str]]:
    if not header_parts:
        return header_parts

    populated_cols = [
        col
        for col in range(len(header_parts))
        if col != key_col and _column_samples(anchor_rows, col)
    ]
    generic_cols = {
        col
        for col in populated_cols
        if all(_is_placeholder_header(part) for part in header_parts[col])
    }
    if not generic_cols:
        return header_parts

    text_cols: list[int] = []
    decision_col: int | None = None
    person_col: int | None = None
    claim_cols: list[int] = []

    for col in populated_cols:
        samples = _column_samples(anchor_rows, col)
        if not samples:
            continue
        joined = " ".join(samples)
        if decision_col is None and any(
            _is_decision_value(sample) for sample in samples
        ):
            decision_col = col
            continue
        if any(_is_claim_value(sample) for sample in samples):
            claim_cols.append(col)
            continue
        if person_col is None and any(_is_person_value(sample) for sample in samples):
            person_col = col
            continue
        if len(joined) > 20:
            text_cols.append(col)

    inferred = [parts[:] for parts in header_parts]

    if text_cols:
        inferred[text_cols[0]] = ["उजुरीको व्यहोरा"]
    if len(text_cols) > 1:
        inferred[text_cols[1]] = ["अनुसन्धानबाट पुष्टि भएको व्यहोरा"]
    if decision_col is not None:
        inferred[decision_col] = [
            "आयोगको निर्णय",
            "मिति/आरोपपत्र दायर",
            "मिति/मुद्दा नं र",
            "प्रतिवादी सङ्ख्या",
        ]
    if person_col is not None:
        inferred[person_col] = ["प्रतिवादीको नाम, पद र कार्यालय"]
    for col in claim_cols:
        inferred[col] = ["भ्रष्टाचारनिवारण ऐन, २०५९ बमोजिम कसुर/सजाय मागदाबी/बिगो"]

    return inferred


def _group_row_indexes(
    expanded_rows: list[list[str]],
    key_col: int,
    *,
    continuation_key: str | None = None,
) -> list[tuple[str, list[int]]]:
    groups: list[tuple[str, list[int]]] = []
    current_key: str | None = None
    current_rows: list[int] = []

    for row_index, row in enumerate(expanded_rows):
        key = row[key_col].strip()
        if key and _looks_like_data_key(key):
            if current_rows and current_key == key:
                current_rows.append(row_index)
                continue
            if current_rows:
                groups.append((current_key or str(len(groups) + 1), current_rows))
            current_key = key
            current_rows = [row_index]
            continue

        if current_rows:
            current_rows.append(row_index)
            continue

        if continuation_key is not None:
            current_key = continuation_key
            current_rows = [row_index]
            continue

        fallback_key = str(len(groups) + 1)
        groups.append((fallback_key, [row_index]))

    if current_rows:
        groups.append((current_key or str(len(groups) + 1), current_rows))
    return groups


def _render_field(
    lines: list[str],
    header_parts: list[str],
    values: list[str],
) -> None:
    if not values:
        return

    main_header = _normalize_header_label(header_parts[0])
    subheaders = [_normalize_header_label(part) for part in header_parts[1:]]
    values = _dedupe_overlapping_values(values)

    if not subheaders and len(values) == 1:
        lines.append(f"- **{main_header}:** {values[0]}")
        return

    if not subheaders:
        if _should_render_as_list(main_header, values):
            lines.append(f"- **{main_header}:**")
            for value in values:
                lines.append(f"  - {value}")
            return
        lines.append(f"- **{main_header}:** {_join_values(values)}")
        return

    lines.append(f"- **{main_header}:**")
    assignments = _assign_values_to_subheaders(subheaders, values)
    for subheader, subvalues in assignments:
        joined = _join_values(subvalues)
        if joined:
            lines.append(f"  - **{subheader}:** {joined}")


def _join_values(values: list[str]) -> str:
    return _clean_text(" ".join(value for value in values if value))


def _dedupe_overlapping_values(values: list[str]) -> list[str]:
    cleaned = [_clean_text(value) for value in values if _clean_text(value)]
    if len(cleaned) <= 1:
        return cleaned

    first = cleaned[0]
    rest = cleaned[1:]
    if rest and all(value in first for value in rest):
        return rest
    return cleaned


def _should_render_as_list(header: str, values: list[str]) -> bool:
    if len(values) <= 1:
        return False

    list_headers = (
        "प्रतिवादी",
        "कसुर",
        "मागदाबी",
        "बिगो",
    )
    if any(marker in header for marker in list_headers):
        return True

    if all(len(value) <= 36 for value in values):
        return False
    return False


def _assign_values_to_subheaders(
    subheaders: list[str],
    values: list[str],
) -> list[tuple[str, list[str]]]:
    if not subheaders:
        return []
    if len(subheaders) == 1:
        return [(subheaders[0], values)]
    if len(values) == len(subheaders):
        return [
            (header, [value]) for header, value in zip(subheaders, values, strict=False)
        ]
    if len(subheaders) == 2:
        return [
            (subheaders[0], values[:-1] or values),
            (subheaders[1], values[-1:] if len(values) > 1 else []),
        ]
    if len(subheaders) >= 3 and len(values) >= 3:
        return [
            (subheaders[0], values[:-2]),
            (subheaders[1], [values[-2]]),
            (subheaders[2], [values[-1]]),
        ]
    return [
        (header, values) if index == 0 else (header, [])
        for index, header in enumerate(subheaders)
    ]


def _render_simple_records(
    table: Table,
    *,
    include_caption: bool = True,
) -> tuple[str, str | None]:
    anchor = _anchor_grid(table)
    expanded = _expanded_grid(table)
    if not anchor:
        return "", None

    title_rows = _title_row_count(expanded)
    data_start = _data_start(expanded, title_rows)
    headers = _compose_headers(_header_parts(expanded, title_rows, data_start))
    body_rows = [
        row for row in anchor[data_start:] if any(cell.strip() for cell in row)
    ]
    if not body_rows:
        return "", None

    lines: list[str] = []
    if include_caption and table.caption:
        lines.append(table.caption)
        lines.append("")

    use_first_cell_as_heading = bool(headers) and _is_record_key_header(headers[0])
    normalized_rows = _merge_continuation_rows(body_rows, use_first_cell_as_heading)
    last_heading: str | None = None

    for row_index, row in enumerate(normalized_rows, start=1):
        row_heading = row[0].strip() if row and row[0].strip() else str(row_index)
        if use_first_cell_as_heading:
            lines.append(f"**{row_heading}**")
            pairs = zip(headers[1:], row[1:], strict=False)
            last_heading = row_heading
        else:
            lines.append(f"**पंक्ति {row_index}**")
            pairs = zip(headers, row, strict=False)

        for header, value in pairs:
            cleaned_value = value.strip()
            if not cleaned_value:
                continue
            lines.append(f"- **{header}:** {cleaned_value}")
        lines.append("")
    return "\n".join(lines).strip(), last_heading


def _render_grouped_records(
    table: Table,
    *,
    include_caption: bool = True,
    continuation_key: str | None = None,
) -> tuple[str, str | None]:
    anchor = _anchor_grid(table)
    expanded = _expanded_grid(table)
    if not anchor:
        return "", continuation_key

    title_rows = _title_row_count(expanded)
    data_start = _data_start(expanded, title_rows)
    header_parts = _header_parts(expanded, title_rows, data_start)
    key_col = _find_key_column(expanded, data_start)
    if key_col is None:
        return _render_simple_records(table, include_caption=include_caption)

    anchor_rows = anchor[data_start:]
    expanded_rows = expanded[data_start:]
    header_parts = _infer_header_parts(anchor_rows, header_parts, key_col)
    groups = _group_row_indexes(
        expanded_rows,
        key_col,
        continuation_key=continuation_key,
    )
    if not groups:
        return _render_simple_records(table, include_caption=include_caption)

    lines: list[str] = []
    if include_caption and table.caption:
        lines.append(table.caption)
        lines.append("")

    last_key: str | None = continuation_key
    for record_key, row_indexes in groups:
        heading = (
            f"{record_key} (जारी)"
            if continuation_key is not None
            and record_key == continuation_key
            and row_indexes[0] == 0
            else record_key
        )
        lines.append(f"**{heading}**")
        grouped_fields: OrderedDict[str, tuple[list[str], list[str]]] = OrderedDict()
        for col, parts in enumerate(header_parts):
            if col == key_col:
                continue
            values = _collect_column_values(anchor_rows, row_indexes, col)
            if not values:
                continue
            normalized_parts = [_normalize_header_label(part) for part in parts]
            main_header = normalized_parts[0]
            subheaders = normalized_parts[1:]
            existing = grouped_fields.get(main_header)
            if existing is None:
                grouped_fields[main_header] = (subheaders, values)
                continue
            existing_subheaders, existing_values = existing
            if existing_subheaders and not subheaders:
                continue
            if subheaders and not existing_subheaders:
                grouped_fields[main_header] = (subheaders, values)
                continue
            grouped_fields[main_header] = (
                existing_subheaders or subheaders,
                existing_values
                + [value for value in values if value not in existing_values],
            )
        for main_header, (subheaders, values) in grouped_fields.items():
            _render_field(lines, [main_header, *subheaders], values)
        lines.append("")
        last_key = record_key
        continuation_key = None
    return "\n".join(lines).strip(), last_key


def _merge_continuation_rows(
    rows: list[list[str]],
    use_first_cell_as_heading: bool,
) -> list[list[str]]:
    if not use_first_cell_as_heading:
        return rows

    merged: list[list[str]] = []
    for row in rows:
        if merged and not row[0].strip():
            previous = merged[-1]
            for index in range(1, len(row)):
                addition = row[index].strip()
                if not addition:
                    continue
                previous[index] = (
                    f"{previous[index]} {addition}".strip()
                    if previous[index].strip()
                    else addition
                )
            continue
        merged.append(row[:])
    return merged


def _render_table(
    table: Table,
    *,
    include_caption: bool = True,
    continuation_key: str | None = None,
) -> tuple[str, str | None]:
    if any(cell.rowspan > 1 or cell.colspan > 1 for cell in table.cells):
        return _render_grouped_records(
            table,
            include_caption=include_caption,
            continuation_key=continuation_key,
        )
    return _render_simple_records(table, include_caption=include_caption)


def _looks_like_page_furniture(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(re.match(r"^\d+\s*परिच्छेद", text)) or "वार्षिकप्रतिवेदन" in compact


def _render_section(section: Section) -> list[str]:
    parts: list[str] = []
    if section.heading:
        parts.append(f"{'#' * section.level} {section.heading}")
        parts.append("")
    if section.blocks:
        previous_table_key: str | None = None
        for index, block in enumerate(section.blocks):
            if (
                isinstance(block, ParagraphBlock)
                and _looks_like_page_furniture(block.text)
                and (
                    (index > 0 and isinstance(section.blocks[index - 1], TableBlock))
                    or (
                        index + 1 < len(section.blocks)
                        and isinstance(section.blocks[index + 1], TableBlock)
                    )
                )
            ):
                continue
            if index:
                parts.append("")
            if isinstance(block, ParagraphBlock):
                parts.append(block.text)
                previous_table_key = None
            elif isinstance(block, TableBlock):
                include_caption = True
                if (
                    index > 0
                    and isinstance(section.blocks[index - 1], ParagraphBlock)
                    and block.table.caption
                    and _caption_key(section.blocks[index - 1].text)
                    == _caption_key(block.table.caption)
                ):
                    include_caption = False
                rendered, previous_table_key = _render_table(
                    block.table,
                    include_caption=include_caption,
                    continuation_key=previous_table_key,
                )
                parts.append(rendered)
    else:
        parts.append(section.body)
    for subsection in section.subsections:
        parts.append("")
        parts.extend(_render_section(subsection))
    return parts


class MarkdownRenderer(OutputRenderer):
    """Render extraction results as Markdown with YAML frontmatter."""

    def render(self, result: ExtractionResult) -> str:
        frontmatter = OrderedDict(
            [
                ("title", result.title),
                ("doc_type", result.doc_type.value),
                ("likhit_version", result.likhit_version),
            ]
        )
        if result.publication_date:
            frontmatter["publication_date"] = result.publication_date
        if result.source_url:
            frontmatter["source_url"] = result.source_url

        body_lines: list[str] = []
        for index, section in enumerate(result.sections):
            if index:
                body_lines.append("")
            body_lines.extend(_render_section(section))

        frontmatter_text = yaml.safe_dump(
            dict(frontmatter),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        body = "\n".join(body_lines).strip()
        return f"---\n{frontmatter_text}\n---\n\n{body}\n"
