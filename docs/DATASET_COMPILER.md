# Packaged Dataset Compiler

The packaged dataset compiler ingests datasets that already exist on Hugging
Face, Kaggle, or local storage. It is separate from the URL scraper pipeline.

The generic entrypoint is:

```bash
python scripts/merge_datasets/merge_corpus_to_hf.py
```

## Supported Adapters

Each run must use exactly one adapter and target one output dataset:

| Adapter | Input modality | Main output fields |
|---|---|---|
| `text` | Text | `text`, `source`, `url`, `language`, `doc_id` |
| `asr` | Audio + transcript | `audio`, `transcription`, speaker/duration metadata |
| `ocr` | Image + text | `image`, `text`, document/page/layout metadata |
| `sft` | Conversations or instruction pairs | `messages` |

The `text` adapter retains the original five-column schema so existing text
repositories remain append-compatible.

ASR audio and OCR images are stored as Arrow `{"bytes", "path"}` structures.
The compiler embeds durable media bytes by default and retains the original
reference in `audio_source` or `image_source`. This prevents target rows from
depending on temporary source caches and avoids making `torch` and `torchcodec`
mandatory during compilation. Consumers can cast the storage columns to native
Hugging Face media features:

```python
from datasets import Audio, Image, load_dataset

dataset = load_dataset("org/nepali-asr-compile", split="train")
dataset = dataset.cast_column("audio", Audio())

ocr_dataset = load_dataset("org/nepali-ocr-compile", split="train")
ocr_dataset = ocr_dataset.cast_column("image", Image())
```

## Source Configuration

```yaml
target_repo: org/nepali-asr-compile

options:
  batch_size: 20000
  max_batch_mb: 256
  max_media_mb: 256
  embed_media: true
  default_language: ne
  dedupe_store: data/dedupe_nepali_asr.sqlite
  filters:
    min_chars: 2
    min_devanagari_ratio: 0.5

sources:
  - name: fleurs_nepali
    kind: hf
    repo: google/fleurs
    config: ne_np
    split: train
    adapter: asr
    modality: audio_text
    task_type: asr
    language: ne
    license: cc-by-4.0
    fields:
      audio: audio
      text: transcription
      doc_id: id
```

Run it with:

```bash
python scripts/merge_datasets/merge_corpus_to_hf.py \
  --config path/to/asr_compile.yml
```

## Inventory Compilation

Inventory JSONL records may declare:

```json
{
  "repo_id": "google/fleurs",
  "config": "ne_np",
  "split": "train",
  "provider": "huggingface",
  "adapter": "asr",
  "modality": "audio_text",
  "task_type": "asr_transcript",
  "language": "ne",
  "license": "cc-by-4.0",
  "mapping_suggested": {
    "audio": "audio",
    "text": "transcription",
    "doc_id": "id"
  },
  "usable": true
}
```

Only entries explicitly marked `usable: true` are compiled. Candidate
inventories should remain disabled until their schema, license, provenance, and
duplication risks have been reviewed.

```bash
python scripts/merge_datasets/merge_corpus_to_hf.py \
  --inventory sources/asr_transcription_sources.jsonl \
  --target-repo org/nepali-asr-compile \
  --dedupe-store data/dedupe_nepali_asr.sqlite \
  --checkpoint data/nepali_asr_done.txt
```

## Safety Rules

- A run cannot combine text, ASR, OCR, and SFT adapters.
- Existing target Parquet schemas are checked before new shards are appended.
- Dedupe keys are modality-aware.
- ASR and OCR dedupe combines normalized text with a media fingerprint.
- ASR/OCR rows embed media bytes by default, with per-item and per-shard limits.
- Checkpoints include the adapter for non-text runs.
- Unknown-license inventory entries should not be marked usable.
