"""Helpers for repairing Kalimati-encoded CIAA PDFs."""

from __future__ import annotations

import io
import logging
import os
import re
import tempfile
from typing import Optional

import fitz

from likhit.errors import ExtractionError

logger = logging.getLogger(__name__)

_PUA_REPH = "\uf000"
_PUA_IKAR = "\uf001"
_VIRAMA = "\u094d"
_RA = "\u0930"
_IKAR = "\u093f"


def _is_devanagari_consonant(char: str) -> bool:
    return "\u0915" <= char <= "\u0939"


def _is_devanagari_matra(char: str) -> bool:
    return "\u093e" <= char <= "\u094c" or char in {"\u0962", "\u0963"}


def _parse_tounicode_cmap(cmap_bytes: bytes) -> dict[int, str]:
    text = cmap_bytes.decode("utf-8", errors="replace")
    mapping: dict[int, str] = {}

    for block in re.finditer(r"beginbfchar\s*(.*?)\s*endbfchar", text, re.DOTALL):
        for match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block.group(1)
        ):
            gid = int(match.group(1), 16)
            unicode_hex = match.group(2)
            chars = "".join(
                chr(int(unicode_hex[index : index + 4], 16))
                for index in range(0, len(unicode_hex), 4)
            )
            if chars:
                mapping[gid] = chars

    for block in re.finditer(r"beginbfrange\s*(.*?)\s*endbfrange", text, re.DOTALL):
        content = block.group(1)
        for match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]",
            content,
            re.DOTALL,
        ):
            start, end = int(match.group(1), 16), int(match.group(2), 16)
            for index, hex_value in enumerate(
                re.findall(r"<([0-9A-Fa-f]+)>", match.group(3))
            ):
                gid = start + index
                if gid > end:
                    break
                chars = "".join(
                    chr(int(hex_value[offset : offset + 4], 16))
                    for offset in range(0, len(hex_value), 4)
                )
                if chars:
                    mapping[gid] = chars

        cleaned = re.sub(r"<[^>]+>\s*<[^>]+>\s*\[.*?\]", "", content, flags=re.DOTALL)
        for match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", cleaned
        ):
            start, end = int(match.group(1), 16), int(match.group(2), 16)
            if len(match.group(3)) <= 4:
                unicode_start = int(match.group(3), 16)
                for gid in range(start, end + 1):
                    mapping[gid] = chr(unicode_start + (gid - start))

    return mapping


def _build_cmap_stream(mapping: dict[int, str]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    entries = sorted(mapping.items())
    for chunk_start in range(0, len(entries), 100):
        chunk = entries[chunk_start : chunk_start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for gid, unicode_value in chunk:
            hex_gid = f"<{gid:04X}>"
            hex_unicode = (
                "<" + "".join(f"{ord(char):04X}" for char in unicode_value) + ">"
            )
            lines.append(f"{hex_gid} {hex_unicode}")
        lines.append("endbfchar")
    lines.extend(
        [
            "endcmap",
            "CMapName currentdict /CMap defineresource pop",
            "end",
            "end",
        ]
    )
    return "\n".join(lines).encode("ascii")


def _analyze_gsub(
    font, glyph_order: list[str], gid_to_correct: dict[int, str]
) -> dict[int, str]:
    gsub = font["GSUB"]
    lookup_features: dict[int, set[str]] = {}

    for feature_record in gsub.table.FeatureList.FeatureRecord:
        tag = feature_record.FeatureTag
        for lookup_index in feature_record.Feature.LookupListIndex:
            lookup_features.setdefault(lookup_index, set()).add(tag)

    derived: dict[int, str] = {}
    for lookup_index, lookup in enumerate(gsub.table.LookupList.Lookup):
        features = lookup_features.get(lookup_index, set())
        for subtable in lookup.SubTable:
            if lookup.LookupType == 1 and hasattr(subtable, "mapping"):
                for from_name, to_name in subtable.mapping.items():
                    if from_name not in glyph_order or to_name not in glyph_order:
                        continue
                    from_gid = glyph_order.index(from_name)
                    to_gid = glyph_order.index(to_name)
                    from_unicode = gid_to_correct.get(from_gid)
                    if from_unicode is None:
                        continue

                    if features & {"half", "haln"}:
                        derived[to_gid] = from_unicode + _VIRAMA
                    elif features & {"rphf"}:
                        derived[to_gid] = _RA + _VIRAMA
                    elif features & {"blwf"}:
                        derived[to_gid] = _VIRAMA + from_unicode
                    elif features & {"nukt"}:
                        derived[to_gid] = from_unicode + "\u093c"
                    else:
                        derived[to_gid] = from_unicode

            elif lookup.LookupType == 4 and hasattr(subtable, "ligatures"):
                for first_name, ligatures in subtable.ligatures.items():
                    if first_name not in glyph_order:
                        continue
                    for ligature in ligatures:
                        output_name = ligature.LigGlyph
                        if output_name not in glyph_order:
                            continue
                        component_names = [first_name] + list(ligature.Component)
                        pieces: list[str] = []
                        resolved = True
                        for component_name in component_names:
                            if component_name not in glyph_order:
                                resolved = False
                                break
                            component_gid = glyph_order.index(component_name)
                            value = gid_to_correct.get(component_gid) or derived.get(
                                component_gid
                            )
                            if value is None:
                                resolved = False
                                break
                            pieces.append(value)
                        if resolved:
                            derived[glyph_order.index(output_name)] = "".join(pieces)

    for gid, value in list(derived.items()):
        for index in range(len(value) - 2):
            if (
                _is_devanagari_consonant(value[index])
                and value[index] != _RA
                and value[index + 1] == _RA
                and value[index + 2] == _VIRAMA
            ):
                derived[gid] = value[: index + 1] + _VIRAMA + _RA + value[index + 3 :]
                break

    return derived


def _is_ra_virama_swap(old_value: str, new_value: str) -> bool:
    if len(old_value) != len(new_value) or len(old_value) < 2:
        return False
    for index in range(len(old_value) - 1):
        if (
            old_value[index] == _VIRAMA
            and old_value[index + 1] == _RA
            and new_value[index] == _RA
            and new_value[index + 1] == _VIRAMA
            and old_value[:index] == new_value[:index]
            and old_value[index + 2 :] == new_value[index + 2 :]
        ):
            return True
        if (
            old_value[index] == _RA
            and old_value[index + 1] == _VIRAMA
            and new_value[index] == _VIRAMA
            and new_value[index + 1] == _RA
            and old_value[:index] == new_value[:index]
            and old_value[index + 2 :] == new_value[index + 2 :]
        ):
            return True
    return False


def _get_font_correction_map(doc: fitz.Document, type0_xref: int) -> dict[int, str]:
    try:
        from fontTools.ttLib import TTFont
    except ModuleNotFoundError:
        raise ExtractionError(
            "fonttools is required for Kalimati font fixing but is not installed"
        )

    try:

        def _follow_xref(xref: int) -> int:
            obj = doc.xref_object(xref, compressed=False).strip()
            if obj.startswith("["):
                match = re.search(r"(\d+)\s+\d+\s+R", obj)
                if match:
                    return int(match.group(1))
            return xref

        type0_dict = doc.xref_object(type0_xref, compressed=False)
        descendant_match = re.search(
            r"/DescendantFonts\s*\[?\s*(\d+)\s+\d+\s+R", type0_dict
        )
        if not descendant_match:
            return {}
        cidfont_xref = _follow_xref(int(descendant_match.group(1)))
        cid_dict = doc.xref_object(cidfont_xref, compressed=False)
        descriptor_match = re.search(r"/FontDescriptor\s+(\d+)\s+\d+\s+R", cid_dict)
        if not descriptor_match:
            return {}
        descriptor_dict = doc.xref_object(
            int(descriptor_match.group(1)), compressed=False
        )
        fontfile_match = re.search(r"/FontFile2\s+(\d+)\s+\d+\s+R", descriptor_dict)
        if not fontfile_match:
            return {}

        font_data = doc.xref_stream(int(fontfile_match.group(1)))
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ttf") as tmp:
                tmp.write(font_data)
                temp_path = tmp.name
            font = TTFont(temp_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

        glyph_order = font.getGlyphOrder()
        best_cmap = font["cmap"].getBestCmap()
        name_to_unicode = {name: codepoint for codepoint, name in best_cmap.items()}

        gid_to_correct: dict[int, str] = {}
        for gid, glyph_name in enumerate(glyph_order):
            if glyph_name in name_to_unicode:
                gid_to_correct[gid] = chr(name_to_unicode[glyph_name])

        derived = _analyze_gsub(font, glyph_order, gid_to_correct)
        full_map = dict(derived)
        full_map.update(gid_to_correct)
        font.close()
        return full_map
    except Exception as exc:
        logger.warning(
            "Failed to build Kalimati correction map for xref=%s: %s",
            type0_xref,
            exc,
        )
        return {}


def _get_fontfile_xref(doc: fitz.Document, type0_xref: int) -> Optional[int]:
    try:

        def _follow_xref(xref: int) -> int:
            obj = doc.xref_object(xref, compressed=False).strip()
            if obj.startswith("["):
                match = re.search(r"(\d+)\s+\d+\s+R", obj)
                if match:
                    return int(match.group(1))
            return xref

        type0_dict = doc.xref_object(type0_xref, compressed=False)
        descendant_match = re.search(
            r"/DescendantFonts\s*\[?\s*(\d+)\s+\d+\s+R", type0_dict
        )
        if not descendant_match:
            return None
        cidfont_xref = _follow_xref(int(descendant_match.group(1)))
        cid_dict = doc.xref_object(cidfont_xref, compressed=False)
        descriptor_match = re.search(r"/FontDescriptor\s+(\d+)\s+\d+\s+R", cid_dict)
        if not descriptor_match:
            return None
        descriptor_dict = doc.xref_object(
            int(descriptor_match.group(1)), compressed=False
        )
        fontfile_match = re.search(r"/FontFile2\s+(\d+)\s+\d+\s+R", descriptor_dict)
        if not fontfile_match:
            return None
        return int(fontfile_match.group(1))
    except Exception:
        return None


def _patch_single_cmap(
    doc: fitz.Document, to_unicode_xref: int, correction_map: dict[int, str]
) -> int:
    pdf_map = _parse_tounicode_cmap(doc.xref_stream(to_unicode_xref))
    patched_map = dict(pdf_map)
    corrections = 0

    for gid, pdf_value in pdf_map.items():
        if gid not in correction_map:
            continue
        correct_value = correction_map[gid]
        if pdf_value == correct_value or _is_ra_virama_swap(pdf_value, correct_value):
            continue
        if correct_value == _RA + _VIRAMA:
            patched_map[gid] = _PUA_REPH
        elif correct_value == _IKAR and pdf_value != _IKAR:
            patched_map[gid] = _PUA_IKAR
        elif (
            len(correct_value) >= 2
            and correct_value.endswith(_RA + _VIRAMA)
            and len(pdf_value) >= 1
            and _is_devanagari_matra(pdf_value[0])
            and pdf_value[0] != _IKAR
        ):
            patched_map[gid] = pdf_value[0] + _PUA_REPH
        else:
            patched_map[gid] = correct_value
        corrections += 1

    for gid, correct_value in correction_map.items():
        if gid in patched_map:
            continue
        if correct_value == _RA + _VIRAMA:
            patched_map[gid] = _PUA_REPH
        elif correct_value == _IKAR:
            patched_map[gid] = _PUA_IKAR
        else:
            patched_map[gid] = correct_value

    doc.update_stream(to_unicode_xref, _build_cmap_stream(patched_map))
    return corrections


def fix_kalimati_cmap(doc: fitz.Document) -> tuple[fitz.Document, bool]:
    kalimati_fonts: dict[int, int] = {}
    fontfile_maps: dict[int, dict[int, str]] = {}
    to_unicode_maps: dict[int, dict[int, str]] = {}

    for page_index in range(doc.page_count):
        for font_info in doc[page_index].get_fonts(full=True):
            xref, _ext, font_type, name, _encoding = font_info[:5]
            if font_type != "Type0":
                continue
            base_name = name.split("+", 1)[-1] if "+" in name else name
            if "kalimati" not in base_name.lower():
                continue

            font_dict = doc.xref_object(xref, compressed=False)
            match = re.search(r"/ToUnicode\s+(\d+)\s+\d+\s+R", font_dict)
            if match:
                kalimati_fonts[int(match.group(1))] = xref

    if not kalimati_fonts:
        return doc, False

    for to_unicode_xref, type0_xref in kalimati_fonts.items():
        fontfile_xref = _get_fontfile_xref(doc, type0_xref)
        if fontfile_xref is not None and fontfile_xref in fontfile_maps:
            to_unicode_maps[to_unicode_xref] = fontfile_maps[fontfile_xref]
            continue
        correction_map = _get_font_correction_map(doc, type0_xref)
        if not correction_map:
            continue
        to_unicode_maps[to_unicode_xref] = correction_map
        if fontfile_xref is not None:
            fontfile_maps[fontfile_xref] = correction_map

    if not to_unicode_maps:
        raise ExtractionError("Unable to repair Kalimati font mappings for this PDF")

    for to_unicode_xref, correction_map in to_unicode_maps.items():
        _patch_single_cmap(doc, to_unicode_xref, correction_map)

    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    buffer.seek(0)
    return fitz.open(stream=buffer, filetype="pdf"), True


def reorder_devanagari(text: str) -> str:
    if _PUA_REPH not in text and _PUA_IKAR not in text:
        return text

    chars = list(text)
    index = 0
    while index < len(chars):
        if chars[index] == _PUA_IKAR:
            if index + 1 < len(chars) and _is_devanagari_consonant(chars[index + 1]):
                end = index + 1
                while (
                    end + 2 < len(chars)
                    and chars[end + 1] == _VIRAMA
                    and _is_devanagari_consonant(chars[end + 2])
                ):
                    end += 2
                chars.pop(index)
                chars.insert(end, _IKAR)
            else:
                chars[index] = _IKAR
                index += 1
        else:
            index += 1

    index = 0
    while index < len(chars):
        if chars[index] == _PUA_REPH:
            cursor = index - 1
            while cursor >= 0 and (
                _is_devanagari_matra(chars[cursor])
                or chars[cursor] in "\u0901\u0902\u0903\u094d"
            ):
                cursor -= 1
            while (
                cursor >= 2
                and _is_devanagari_consonant(chars[cursor])
                and chars[cursor - 1] == _VIRAMA
                and _is_devanagari_consonant(chars[cursor - 2])
            ):
                cursor -= 2
            if cursor >= 0 and _is_devanagari_consonant(chars[cursor]):
                chars.pop(index)
                chars.insert(cursor, _VIRAMA)
                chars.insert(cursor, _RA)
                index += 2
            else:
                chars[index] = _RA
                chars.insert(index + 1, _VIRAMA)
                index += 2
        else:
            index += 1

    return "".join(chars)


def _is_devanagari_combining(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x093E <= codepoint <= 0x094D
        or 0x0901 <= codepoint <= 0x0903
        or codepoint == 0x093C
        or codepoint in {0x0962, 0x0963}
    )


def normalize_devanagari_spacing(text: str) -> str:
    if not text:
        return text

    result: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == " ":
            previous = result[-1] if result else None
            next_char = text[index + 1] if index + 1 < len(text) else None
            remove = False
            if next_char and (
                _is_devanagari_combining(next_char)
                or next_char in {_PUA_REPH, _PUA_IKAR}
            ):
                remove = True
            if previous == _VIRAMA:
                remove = True
            if remove:
                index += 1
                continue
        result.append(text[index])
        index += 1

    return "".join(result)
