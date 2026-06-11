"""Normalize commodity news files into Spanner RAG JSONL ingest records."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_SOURCES = [
    {
        "dataset": "oil_sentiment",
        "path": "/Users/pauldudko/VSProjects/sentiment_signal_oil/Data/Sentiment/all_oil_data_final_sentiment_fixed_adjusted.csv",
        "format": "csv",
        "default_commodities": ["oil"],
    },
    {
        "dataset": "sugar_macro_tags",
        "path": "/Users/pauldudko/VSProjects/adm/data/sugar_news_tagged_macro.jsonl",
        "format": "jsonl",
        "default_commodities": ["sugar"],
    },
    {
        "dataset": "fertilizer_news",
        "path": "/Users/pauldudko/VSProjects/metaprompting/output_data/fertilizer_news_march_april_2025.csv",
        "format": "csv",
        "default_commodities": ["fertilizer"],
    },
    {
        "dataset": "aluminum_knn_news",
        "path": "/Users/pauldudko/VSProjects/metaprompting/output_data/base_metals/Archive/aluminum_news_knn_result.csv",
        "format": "csv",
        "default_commodities": ["aluminum"],
    },
    {
        "dataset": "base_metals_news",
        "path": "/Users/pauldudko/VSProjects/metaprompting/output_data/base_metals/Archive/base_metals_news.csv",
        "format": "csv",
        "default_commodities": ["base_metals"],
    },
]


TEXT_COLUMNS = {
    "text",
    "combined_text",
    "text_snippet",
    "summary",
    "af",
    "title",
}

COMMODITY_PATTERNS = {
    "aluminum": r"\b(aluminum|aluminium|alumina|bauxite)\b",
    "ammonia": r"\bammonia\b",
    "base_metals": r"\b(base metals?)\b",
    "brent": r"\bbrent\b",
    "copper": r"\bcopper\b",
    "crude_oil": r"\b(crude|oil|wti|brent)\b",
    "fertilizer": r"\b(fertilizer|fertiliser|urea|ammonia|phosphate|potash|dap|map)\b",
    "gasoline": r"\b(gasoline|rbob|petrol)\b",
    "gasoil": r"\b(gasoil|diesel|ulsd|heating oil)\b",
    "gold": r"\bgold\b",
    "iron_ore": r"\biron ore\b",
    "lead": r"\blead\b",
    "lithium": r"\blithium\b",
    "nickel": r"\bnickel\b",
    "natural_gas": r"\b(natural gas|lng|henry hub|ttf|jkm)\b",
    "potash": r"\bpotash\b",
    "steel": r"\bsteel\b",
    "sugar": r"\bsugar\b",
    "sulfur": r"\b(sulfur|sulphur)\b",
    "tin": r"\btin\b",
    "zinc": r"\bzinc\b",
}


@dataclass(frozen=True)
class ConvertStats:
    dataset: str
    source_path: str
    documents: int = 0
    chunks: int = 0
    skipped: int = 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert local commodity datasets into Spanner RAG JSONL."
    )
    parser.add_argument(
        "--out-dir",
        default="data/spanner_rag_ingest",
        help="Output directory for documents.jsonl, chunks.jsonl, and manifest.json.",
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=3200,
        help="Approximate character budget per searchable chunk.",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=300,
        help="Character overlap between adjacent chunks.",
    )
    parser.add_argument(
        "--limit-per-source",
        type=int,
        default=None,
        help="Optional row limit per source for smoke tests.",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    docs_path = out_dir / "documents.jsonl"
    chunks_path = out_dir / "chunks.jsonl"
    manifest_path = out_dir / "manifest.json"

    stats: list[ConvertStats] = []
    started_at = datetime.now(UTC).isoformat()
    with docs_path.open("w", encoding="utf-8") as docs_file, chunks_path.open(
        "w",
        encoding="utf-8",
    ) as chunks_file:
        for source in DEFAULT_SOURCES:
            stat = convert_source(
                source=source,
                docs_file=docs_file,
                chunks_file=chunks_file,
                chunk_chars=args.chunk_chars,
                chunk_overlap=args.chunk_overlap,
                limit=args.limit_per_source,
            )
            stats.append(stat)

    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "started_at": started_at,
        "output": {
            "documents": docs_path.as_posix(),
            "chunks": chunks_path.as_posix(),
        },
        "schema_version": 1,
        "schema": {
            "documents": [
                "doc_id",
                "source_dataset",
                "source_file",
                "source_row",
                "title",
                "body",
                "text_for_embedding",
                "published_at",
                "source",
                "commodities",
                "tags",
                "sentiment",
                "metadata",
            ],
            "chunks": [
                "chunk_id",
                "doc_id",
                "source_dataset",
                "chunk_index",
                "chunk_text",
                "text_for_embedding",
                "token_estimate",
                "published_at",
                "source",
                "commodities",
                "tags",
                "metadata",
            ],
        },
        "stats": [stat.__dict__ for stat in stats],
        "totals": {
            "documents": sum(stat.documents for stat in stats),
            "chunks": sum(stat.chunks for stat in stats),
            "skipped": sum(stat.skipped for stat in stats),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest["totals"], indent=2))
    print(f"Wrote {docs_path}")
    print(f"Wrote {chunks_path}")
    print(f"Wrote {manifest_path}")
    return 0


def convert_source(
    *,
    source: Mapping[str, Any],
    docs_file: Any,
    chunks_file: Any,
    chunk_chars: int,
    chunk_overlap: int,
    limit: int | None,
) -> ConvertStats:
    dataset = str(source["dataset"])
    path = Path(str(source["path"]))
    default_commodities = list(source.get("default_commodities") or [])
    documents = chunks = skipped = 0
    for source_row, row in enumerate(iter_rows(path, str(source["format"])), start=1):
        if limit is not None and source_row > limit:
            break
        document = normalize_document(
            dataset=dataset,
            path=path,
            source_row=source_row,
            row=row,
            default_commodities=default_commodities,
        )
        if not document["text_for_embedding"].strip():
            skipped += 1
            continue
        docs_file.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
        docs_file.write("\n")
        documents += 1
        for chunk in chunk_document(
            document,
            chunk_chars=chunk_chars,
            chunk_overlap=chunk_overlap,
        ):
            chunks_file.write(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")))
            chunks_file.write("\n")
            chunks += 1
    return ConvertStats(
        dataset=dataset,
        source_path=path.as_posix(),
        documents=documents,
        chunks=chunks,
        skipped=skipped,
    )


def iter_rows(path: Path, source_format: str) -> Iterator[dict[str, Any]]:
    if source_format == "jsonl":
        with path.open(encoding="utf-8", errors="replace") as file:
            for line in file:
                if line.strip():
                    yield json.loads(line)
        return
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as file:
        reader = csv.DictReader(file)
        for row in reader:
            yield dict(row)


def normalize_document(
    *,
    dataset: str,
    path: Path,
    source_row: int,
    row: Mapping[str, Any],
    default_commodities: list[str],
) -> dict[str, Any]:
    clean = {clean_key(key): clean_value(value) for key, value in row.items()}
    title = first_text(clean, ["title", "headline", "af"])
    body = first_text(clean, ["text", "combined_text", "text_snippet", "summary", "af"])
    if not title and body:
        title = body[:180]
    published_at = normalize_datetime(
        first_text(clean, ["datetime_utc", "timestamp", "date", "published_at"])
    )
    source_name = first_text(clean, ["source", "publisher", "source_language"])
    tags = infer_tags(clean)
    commodities = infer_commodities(
        " ".join([title, body, " ".join(tags)]),
        defaults=default_commodities,
        row=clean,
    )
    sentiment = infer_sentiment(clean)
    text_for_embedding = build_embedding_text(
        title=title,
        body=body,
        commodities=commodities,
        tags=tags,
        source=source_name,
        published_at=published_at,
    )
    metadata = {
        key: value
        for key, value in clean.items()
        if key not in TEXT_COLUMNS and value not in (None, "", [])
    }
    doc_id = stable_id(
        [
            dataset,
            path.as_posix(),
            str(source_row),
            first_text(clean, ["id", "article_id"]),
            published_at or "",
            title,
        ]
    )
    return {
        "doc_id": doc_id,
        "source_dataset": dataset,
        "source_file": path.as_posix(),
        "source_row": source_row,
        "external_id": first_text(clean, ["id", "article_id"]) or None,
        "title": title,
        "body": body,
        "text_for_embedding": text_for_embedding,
        "published_at": published_at,
        "source": source_name or None,
        "commodities": commodities,
        "tags": tags,
        "sentiment": sentiment,
        "metadata": metadata,
    }


def chunk_document(
    document: Mapping[str, Any],
    *,
    chunk_chars: int,
    chunk_overlap: int,
) -> Iterable[dict[str, Any]]:
    text = str(document["text_for_embedding"]).strip()
    if len(text) <= chunk_chars:
        spans = [(0, text)]
    else:
        spans = []
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_chars)
            if end < len(text):
                boundary = max(
                    text.rfind("\n", start, end),
                    text.rfind(". ", start, end),
                    text.rfind(" ", start, end),
                )
                if boundary > start + max(400, chunk_chars // 3):
                    end = boundary + 1
            chunk_text = text[start:end].strip()
            if chunk_text:
                spans.append((start, chunk_text))
            if end >= len(text):
                break
            start = max(0, end - chunk_overlap)
    for index, (start, chunk_text) in enumerate(spans):
        chunk_id = stable_id([str(document["doc_id"]), str(index), chunk_text[:200]])
        yield {
            "chunk_id": chunk_id,
            "doc_id": document["doc_id"],
            "source_dataset": document["source_dataset"],
            "chunk_index": index,
            "char_start": start,
            "chunk_text": chunk_text,
            "text_for_embedding": chunk_text,
            "token_estimate": max(1, len(chunk_text) // 4),
            "published_at": document["published_at"],
            "source": document["source"],
            "commodities": document["commodities"],
            "tags": document["tags"],
            "metadata": {
                "source_file": document["source_file"],
                "source_row": document["source_row"],
                "external_id": document["external_id"],
                "title": document["title"],
            },
        }


def clean_key(key: Any) -> str:
    raw = str(key or "").strip()
    if not raw:
        return "unnamed_column"
    return re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_") or "unnamed_column"


def clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool | int | float | list | dict):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    if looks_like_list(text):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, list):
                return [clean_value(item) for item in parsed if clean_value(item) is not None]
        except (SyntaxError, ValueError):
            return text
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    return text


def looks_like_list(text: str) -> bool:
    return len(text) >= 2 and text[0] == "[" and text[-1] == "]"


def first_text(row: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            joined = ", ".join(str(item) for item in value if item is not None)
            if joined:
                return joined
        text = str(value).strip()
        if text:
            return text
    return ""


def normalize_datetime(value: str) -> str | None:
    if not value:
        return None
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00Z"
    if text.endswith("Z") and "T" in text:
        return text
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=UTC)
            return dt.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return text


def infer_tags(row: Mapping[str, Any]) -> list[str]:
    tags: list[str] = []
    for key in ("primary_tag", "source_category", "metal", "model", "tagger"):
        value = row.get(key)
        if value:
            tags.append(str(value))
    for key in ("secondary_tags", "codes"):
        value = row.get(key)
        if isinstance(value, list):
            tags.extend(str(item) for item in value if item)
        elif value:
            tags.append(str(value))
    return sorted(unique(tags))


def infer_commodities(
    text: str,
    *,
    defaults: list[str],
    row: Mapping[str, Any],
) -> list[str]:
    values = list(defaults)
    metal = row.get("metal")
    if metal:
        values.append(str(metal).lower().replace(" ", "_"))
    lower = text.lower()
    for commodity, pattern in COMMODITY_PATTERNS.items():
        if re.search(pattern, lower, flags=re.IGNORECASE):
            values.append(commodity)
    return sorted(unique(values))


def infer_sentiment(row: Mapping[str, Any]) -> dict[str, Any] | None:
    fields = {
        "label": row.get("sentiment") or row.get("embedding_sentiment"),
        "confidence": row.get("confidence"),
        "reasoning": row.get("reasoning"),
        "prob_negative": row.get("prob_-1"),
        "prob_neutral": row.get("prob_0"),
        "prob_positive": row.get("prob_1"),
        "original": row.get("sentiment_original"),
        "override_flag": row.get("override_flag"),
    }
    clean = {key: value for key, value in fields.items() if value not in (None, "")}
    return clean or None


def build_embedding_text(
    *,
    title: str,
    body: str,
    commodities: list[str],
    tags: list[str],
    source: str,
    published_at: str | None,
) -> str:
    parts = [
        f"Title: {title}" if title else "",
        f"Published: {published_at}" if published_at else "",
        f"Source: {source}" if source else "",
        f"Commodities: {', '.join(commodities)}" if commodities else "",
        f"Tags: {', '.join(tags[:40])}" if tags else "",
        body,
    ]
    return "\n".join(part for part in parts if part).strip()


def unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", str(value).strip())
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def stable_id(parts: Sequence[str]) -> str:
    raw = "\x1f".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
