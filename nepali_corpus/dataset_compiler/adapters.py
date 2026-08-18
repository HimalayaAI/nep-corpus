from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

from datasets import Audio, Features, Image, Value
from datasets.download import DownloadConfig
from datasets.utils.file_utils import xopen

from nepali_corpus.dataset_compiler.quality_filters import (
    FilterSpec,
    normalize_text,
    passes_quality,
)

logger = logging.getLogger(__name__)


def item_get(item: Any, key: str) -> Any:
    if hasattr(item, "get"):
        try:
            return item.get(key)
        except Exception:
            pass
    try:
        return item[key]
    except Exception:
        return None


def get_field_value(item: Any, field_spec: Any) -> Any:
    """Resolve a field name, nested dotted path, or fallback field list."""
    if field_spec is None:
        return None
    if isinstance(field_spec, (list, tuple)):
        for spec in field_spec:
            value = get_field_value(item, spec)
            if value is not None:
                return value
        return None
    if isinstance(field_spec, str) and "." in field_spec:
        current = item
        for part in field_spec.split("."):
            if current is None:
                return None
            current = item_get(current, part)
        return current
    if isinstance(field_spec, str):
        return item_get(item, field_spec)
    return None


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _media_source(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return _optional_string(value)
    if isinstance(value, Mapping):
        return _optional_string(value.get("path"))
    return None


def _read_limited(file_obj: Any, max_bytes: Optional[int]) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = file_obj.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if max_bytes and total > max_bytes:
            raise ValueError(
                f"Media exceeds the configured {max_bytes / (1024 * 1024):.1f} MiB limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _read_media_path(path: str, context: "AdapterContext") -> bytes:
    download_config = DownloadConfig(token=context.hf_token)
    candidates = [path]
    if (
        context.source_repo
        and not os.path.isabs(path)
        and "://" not in path
        and "::" not in path
    ):
        candidates.append(
            f"hf://datasets/{context.source_repo}/{path.lstrip('/')}"
        )

    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            with xopen(
                candidate,
                "rb",
                download_config=download_config,
            ) as file_obj:
                return _read_limited(file_obj, context.max_media_bytes)
        except Exception as exc:
            last_error = exc
    raise OSError(f"Unable to read media path {path!r}") from last_error


def _media_storage(
    value: Any,
    context: Optional["AdapterContext"] = None,
) -> Optional[Dict[str, Any]]:
    """Normalize media and optionally embed durable bytes in the output row."""
    if value is None:
        return None
    media_bytes = None
    media_path = None
    if isinstance(value, (bytes, bytearray, memoryview)):
        media_bytes = bytes(value)
    elif isinstance(value, str):
        media_path = _optional_string(value)
    elif isinstance(value, Mapping):
        media_bytes = value.get("bytes")
        media_path = value.get("path")
        if media_bytes is not None:
            try:
                media_bytes = bytes(media_bytes)
            except (TypeError, ValueError):
                media_bytes = None
        media_path = _optional_string(media_path)

    if media_bytes is not None and context and context.max_media_bytes:
        if len(media_bytes) > context.max_media_bytes:
            logger.warning(
                "Skipping oversized media from %s (%s bytes)",
                context.source_name,
                len(media_bytes),
            )
            return None

    if context and context.embed_media and media_bytes is None and media_path:
        try:
            media_bytes = _read_media_path(media_path, context)
        except Exception as exc:
            logger.warning(
                "Could not materialize media from %s (%s): %s",
                context.source_name,
                media_path,
                exc,
            )
            return None

    if context and context.embed_media and media_bytes is None:
        return None
    if media_bytes is None and media_path is None:
        return None
    return {"bytes": media_bytes, "path": media_path}


def _media_fingerprint(media: Mapping[str, Any]) -> str:
    media_bytes = media.get("bytes")
    if media_bytes:
        return "bytes:" + hashlib.blake2b(bytes(media_bytes), digest_size=16).hexdigest()
    return "path:" + str(media.get("path") or "")


def _normalized_role(value: Any) -> Optional[str]:
    role = _optional_string(value)
    if not role:
        return None
    return {
        "human": "user",
        "user": "user",
        "gpt": "assistant",
        "assistant": "assistant",
        "system": "system",
    }.get(role.lower(), role.lower())


@dataclass(frozen=True)
class AdapterContext:
    source_name: str
    fields: Dict[str, Any]
    filter_spec: Optional[FilterSpec]
    default_language: Optional[str]
    source_repo: Optional[str] = None
    source_config: Optional[str] = None
    source_split: str = "train"
    license: Optional[str] = None
    task_type: Optional[str] = None
    embed_media: bool = True
    max_media_bytes: Optional[int] = None
    hf_token: Optional[str] = None

    def language_for(self, item: Dict[str, Any]) -> Optional[str]:
        language_field = self.fields.get("language")
        if language_field:
            language = _optional_string(get_field_value(item, language_field))
            if language:
                return language
        return self.default_language

    def provenance(self, item: Dict[str, Any]) -> Dict[str, Any]:
        doc_id = None
        doc_id_field = self.fields.get("doc_id") or self.fields.get("id")
        if doc_id_field:
            doc_id = _optional_string(get_field_value(item, doc_id_field))
        return {
            "source": self.source_name,
            "source_repo": self.source_repo,
            "source_config": self.source_config or "default",
            "source_split": self.source_split,
            "language": self.language_for(item),
            "license": self.license,
            "doc_id": doc_id,
        }


@dataclass(frozen=True)
class MappedItem:
    row: Dict[str, Any]
    dedupe_key: str


class ModalityAdapter:
    name = "base"
    aliases: Iterable[str] = ()

    def features(self) -> Features:
        raise NotImplementedError

    def map_item(self, item: Dict[str, Any], context: AdapterContext) -> Optional[MappedItem]:
        raise NotImplementedError

    def media_columns(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    def remote_dedupe_columns(self) -> List[str]:
        raise NotImplementedError

    def remote_dedupe_key(self, row: Dict[str, Any]) -> Optional[str]:
        raise NotImplementedError

    def payload_bytes(self, row: Dict[str, Any]) -> int:
        return 0

    def data_dict(self, rows: List[Dict[str, Any]]) -> Dict[str, List[Any]]:
        return {
            column: [row.get(column) for row in rows]
            for column in self.features().keys()
        }


class TextAdapter(ModalityAdapter):
    name = "text"
    aliases = ("text_only", "pretrain")

    def features(self) -> Features:
        # Keep the original compiler schema unchanged for append compatibility.
        return Features(
            {
                "text": Value("string"),
                "source": Value("string"),
                "url": Value("string"),
                "language": Value("string"),
                "doc_id": Value("string"),
            }
        )

    def map_item(self, item: Dict[str, Any], context: AdapterContext) -> Optional[MappedItem]:
        text_value = get_field_value(item, context.fields.get("text", "text"))
        if text_value is None:
            return None
        text = str(text_value).strip()
        text_norm = normalize_text(text)
        if not text_norm or not passes_quality(text_norm, context.filter_spec):
            return None

        row: Dict[str, Any] = {"text": text, "source": context.source_name}
        url_field = context.fields.get("url")
        if url_field:
            row["url"] = _optional_string(get_field_value(item, url_field))
        language = context.language_for(item)
        if language:
            row["language"] = language
        doc_id_field = context.fields.get("doc_id") or context.fields.get("id")
        if doc_id_field:
            row["doc_id"] = _optional_string(get_field_value(item, doc_id_field))
        return MappedItem(row=row, dedupe_key=text_norm)

    def remote_dedupe_columns(self) -> List[str]:
        return ["text"]

    def remote_dedupe_key(self, row: Dict[str, Any]) -> Optional[str]:
        text = normalize_text(str(row.get("text") or ""))
        return text or None


class ASRAdapter(ModalityAdapter):
    name = "asr"
    aliases = ("audio", "audio_text", "speech_to_text")

    def features(self) -> Features:
        return Features(
            {
                # Store the Arrow representation directly. This remains castable to
                # datasets.Audio while avoiding a mandatory torch/torchcodec encoder
                # dependency during corpus compilation.
                "audio": {
                    "bytes": Value("binary"),
                    "path": Value("string"),
                },
                "audio_source": Value("string"),
                "transcription": Value("string"),
                "normalized_transcription": Value("string"),
                "source": Value("string"),
                "source_repo": Value("string"),
                "source_config": Value("string"),
                "source_split": Value("string"),
                "language": Value("string"),
                "license": Value("string"),
                "doc_id": Value("string"),
                "speaker_id": Value("string"),
                "duration_ms": Value("int64"),
                "sampling_rate": Value("int64"),
            }
        )

    def media_columns(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        audio_field = fields.get("audio")
        if isinstance(audio_field, str) and "." not in audio_field:
            return {audio_field: Audio(decode=False)}
        return {}

    def map_item(self, item: Dict[str, Any], context: AdapterContext) -> Optional[MappedItem]:
        transcription_value = get_field_value(
            item,
            context.fields.get("text") or context.fields.get("transcription", "transcription"),
        )
        if transcription_value is None:
            return None
        transcription = str(transcription_value).strip()
        transcription_norm = normalize_text(transcription)
        if not transcription_norm or not passes_quality(transcription_norm, context.filter_spec):
            return None

        audio_value = get_field_value(item, context.fields.get("audio", "audio"))
        audio = _media_storage(audio_value, context)
        if not audio:
            return None

        row = context.provenance(item)
        row.update(
            {
                "audio": audio,
                "audio_source": _media_source(audio_value),
                "transcription": transcription,
                "normalized_transcription": transcription_norm,
                "speaker_id": _optional_string(
                    get_field_value(item, context.fields.get("speaker_id"))
                ),
                "duration_ms": _optional_int(
                    get_field_value(item, context.fields.get("duration_ms"))
                ),
                "sampling_rate": _optional_int(
                    get_field_value(item, context.fields.get("sampling_rate"))
                ),
            }
        )
        dedupe_key = f"{transcription_norm}\n{_media_fingerprint(audio)}"
        return MappedItem(row=row, dedupe_key=dedupe_key)

    def payload_bytes(self, row: Dict[str, Any]) -> int:
        audio = row.get("audio")
        if isinstance(audio, Mapping) and audio.get("bytes"):
            return len(audio["bytes"])
        return 0

    def remote_dedupe_columns(self) -> List[str]:
        return ["audio", "normalized_transcription", "transcription"]

    def remote_dedupe_key(self, row: Dict[str, Any]) -> Optional[str]:
        transcription = normalize_text(
            str(row.get("normalized_transcription") or row.get("transcription") or "")
        )
        audio = _media_storage(row.get("audio"))
        if not transcription or not audio:
            return None
        return f"{transcription}\n{_media_fingerprint(audio)}"


class OCRAdapter(ModalityAdapter):
    name = "ocr"
    aliases = ("image", "image_text", "document_ocr")

    def features(self) -> Features:
        return Features(
            {
                # Use the Arrow storage shape directly so a local source path
                # cannot replace embedded bytes during target encoding.
                "image": {
                    "bytes": Value("binary"),
                    "path": Value("string"),
                },
                "image_source": Value("string"),
                "text": Value("string"),
                "source": Value("string"),
                "source_repo": Value("string"),
                "source_config": Value("string"),
                "source_split": Value("string"),
                "language": Value("string"),
                "license": Value("string"),
                "doc_id": Value("string"),
                "document_id": Value("string"),
                "page_number": Value("int64"),
                "bounding_boxes_json": Value("string"),
            }
        )

    def media_columns(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        image_field = fields.get("image")
        if isinstance(image_field, str) and "." not in image_field:
            return {image_field: Image(decode=False)}
        return {}

    def map_item(self, item: Dict[str, Any], context: AdapterContext) -> Optional[MappedItem]:
        text_value = get_field_value(item, context.fields.get("text", "text"))
        if text_value is None:
            return None
        text = str(text_value).strip()
        text_norm = normalize_text(text)
        if not text_norm or not passes_quality(text_norm, context.filter_spec):
            return None

        image_value = get_field_value(item, context.fields.get("image", "image"))
        image = _media_storage(image_value, context)
        if not image:
            return None

        boxes = get_field_value(
            item,
            context.fields.get("bounding_boxes") or context.fields.get("bbox"),
        )
        row = context.provenance(item)
        row.update(
            {
                "image": image,
                "image_source": _media_source(image_value),
                "text": text,
                "document_id": _optional_string(
                    get_field_value(item, context.fields.get("document_id"))
                ),
                "page_number": _optional_int(
                    get_field_value(item, context.fields.get("page_number"))
                ),
                "bounding_boxes_json": (
                    json.dumps(boxes, ensure_ascii=False, sort_keys=True)
                    if boxes is not None
                    else None
                ),
            }
        )
        dedupe_key = f"{text_norm}\n{_media_fingerprint(image)}"
        return MappedItem(row=row, dedupe_key=dedupe_key)

    def payload_bytes(self, row: Dict[str, Any]) -> int:
        image = row.get("image")
        if isinstance(image, Mapping) and image.get("bytes"):
            return len(image["bytes"])
        return 0

    def remote_dedupe_columns(self) -> List[str]:
        return ["image", "text"]

    def remote_dedupe_key(self, row: Dict[str, Any]) -> Optional[str]:
        text = normalize_text(str(row.get("text") or ""))
        image = _media_storage(row.get("image"))
        if not text or not image:
            return None
        return f"{text}\n{_media_fingerprint(image)}"


class SFTAdapter(ModalityAdapter):
    name = "sft"
    aliases = ("conversation", "conversations", "sharegpt", "instruction")

    def features(self) -> Features:
        return Features(
            {
                "messages": [
                    {
                        "role": Value("string"),
                        "content": Value("string"),
                    }
                ],
                "source": Value("string"),
                "source_repo": Value("string"),
                "source_config": Value("string"),
                "source_split": Value("string"),
                "language": Value("string"),
                "license": Value("string"),
                "doc_id": Value("string"),
            }
        )

    def _messages(self, item: Dict[str, Any], fields: Dict[str, Any]) -> List[Dict[str, str]]:
        messages_value = get_field_value(
            item,
            fields.get("messages") or fields.get("conversations"),
        )
        messages: List[Dict[str, str]] = []
        if isinstance(messages_value, list):
            for message in messages_value:
                if not isinstance(message, Mapping):
                    continue
                role = _normalized_role(message.get("role") or message.get("from"))
                content = _optional_string(message.get("content") or message.get("value"))
                if role and content:
                    messages.append({"role": role, "content": content})
            return messages

        instruction = _optional_string(
            get_field_value(
                item,
                fields.get("instruction") or fields.get("prompt") or "instruction",
            )
        )
        response = _optional_string(
            get_field_value(
                item,
                fields.get("response") or fields.get("output") or "response",
            )
        )
        if instruction and response:
            return [
                {"role": "user", "content": instruction},
                {"role": "assistant", "content": response},
            ]
        return []

    def map_item(self, item: Dict[str, Any], context: AdapterContext) -> Optional[MappedItem]:
        messages = self._messages(item, context.fields)
        if len(messages) < 2:
            return None
        normalized_parts = [
            f"{message['role']}:{normalize_text(message['content'])}"
            for message in messages
        ]
        if any(part.endswith(":") for part in normalized_parts):
            return None
        dedupe_key = "\n".join(normalized_parts)

        row = context.provenance(item)
        row["messages"] = messages
        return MappedItem(row=row, dedupe_key=dedupe_key)

    def remote_dedupe_columns(self) -> List[str]:
        return ["messages"]

    def remote_dedupe_key(self, row: Dict[str, Any]) -> Optional[str]:
        messages = row.get("messages")
        if not isinstance(messages, list):
            return None
        normalized_parts = []
        for message in messages:
            if not isinstance(message, Mapping):
                return None
            role = _normalized_role(message.get("role"))
            content = normalize_text(str(message.get("content") or ""))
            if not role or not content:
                return None
            normalized_parts.append(f"{role}:{content}")
        return "\n".join(normalized_parts) or None


_ADAPTERS: Dict[str, ModalityAdapter] = {}
for _adapter in (TextAdapter(), ASRAdapter(), OCRAdapter(), SFTAdapter()):
    _ADAPTERS[_adapter.name] = _adapter
    for _alias in _adapter.aliases:
        _ADAPTERS[_alias] = _adapter


def infer_adapter_name(
    *,
    adapter: Optional[str] = None,
    modality: Optional[str] = None,
    task_type: Optional[str] = None,
) -> str:
    for candidate in (adapter, task_type, modality, "text"):
        normalized = str(candidate or "").strip().lower().replace("-", "_")
        if normalized in _ADAPTERS:
            return _ADAPTERS[normalized].name
    raise ValueError(
        f"Unsupported dataset adapter/modality: adapter={adapter!r}, "
        f"modality={modality!r}, task_type={task_type!r}"
    )


def get_adapter(name: str) -> ModalityAdapter:
    normalized = str(name or "text").strip().lower().replace("-", "_")
    try:
        return _ADAPTERS[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported dataset adapter: {name}") from exc
