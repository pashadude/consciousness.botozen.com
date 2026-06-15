"""Load Spanner RAG JSONL documents and chunks into Cloud Spanner."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.cloud import spanner, storage

DOCUMENT_COLUMNS = [
    "DocId",
    "SourceDataset",
    "SourceFile",
    "SourceRow",
    "ExternalId",
    "Title",
    "Body",
    "TextForEmbedding",
    "PublishedAt",
    "Source",
    "Commodities",
    "Tags",
    "SentimentJson",
    "MetadataJson",
    "CreatedAt",
]

CHUNK_COLUMNS = [
    "DocId",
    "ChunkId",
    "SourceDataset",
    "ChunkIndex",
    "CharStart",
    "ChunkText",
    "TextForEmbedding",
    "TokenEstimate",
    "PublishedAt",
    "Source",
    "Commodities",
    "Tags",
    "MetadataJson",
    "Embedding",
    "CreatedAt",
]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT", "zenpulsar"),
    )
    parser.add_argument(
        "--instance",
        default=os.environ.get("SPANNER_RAG_INSTANCE_ID", "commodity-rag"),
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("SPANNER_RAG_DATABASE_ID", "trader_rag"),
    )
    parser.add_argument(
        "--documents",
        action="append",
        default=None,
        help="Documents JSONL path. Can be repeated.",
    )
    parser.add_argument(
        "--chunks",
        action="append",
        default=None,
        help="Chunks JSONL path. Can be repeated.",
    )
    parser.add_argument("--skip-documents", action="store_true")
    parser.add_argument("--skip-chunks", action="store_true")
    parser.add_argument("--limit-documents", type=int, default=None)
    parser.add_argument("--limit-chunks", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=2500)
    parser.add_argument("--progress-every", type=int, default=25000)
    args = parser.parse_args(argv)

    database = spanner.Client(project=args.project).instance(args.instance).database(
        args.database
    )
    if not args.skip_documents:
        count = load_records(
            database=database,
            table="RagDocuments",
            columns=DOCUMENT_COLUMNS,
            rows=iter_document_rows(paths(args.documents, default_documents())),
            batch_size=args.batch_size,
            limit=args.limit_documents,
            progress_label="documents",
            progress_every=args.progress_every,
        )
        print(f"Loaded documents: {count}")
    if not args.skip_chunks:
        count = load_records(
            database=database,
            table="RagChunks",
            columns=CHUNK_COLUMNS,
            rows=iter_chunk_rows(paths(args.chunks, default_chunks())),
            batch_size=args.batch_size,
            limit=args.limit_chunks,
            progress_label="chunks",
            progress_every=args.progress_every,
        )
        print(f"Loaded chunks: {count}")
    return 0


def default_documents() -> list[str]:
    bucket = os.environ.get("SPANNER_RAG_GCS_BUCKET")
    if bucket:
        return [
            f"gs://{bucket}/documents.jsonl",
            f"gs://{bucket}/user_talks_documents.jsonl",
            f"gs://{bucket}/strategy_documents.jsonl",
        ]
    return [
        "data/spanner_rag_ingest/documents.jsonl",
        "data/spanner_rag_ingest/user_talks_documents.jsonl",
        "data/spanner_rag_ingest/strategy_documents.jsonl",
    ]


def default_chunks() -> list[str]:
    bucket = os.environ.get("SPANNER_RAG_GCS_BUCKET")
    if bucket:
        return [
            f"gs://{bucket}/chunks.jsonl",
            f"gs://{bucket}/user_talks_chunks.jsonl",
            f"gs://{bucket}/strategy_chunks.jsonl",
        ]
    return [
        "data/spanner_rag_ingest/chunks.jsonl",
        "data/spanner_rag_ingest/user_talks_chunks.jsonl",
        "data/spanner_rag_ingest/strategy_chunks.jsonl",
    ]


def paths(values: Iterable[str] | None, default: list[str]) -> list[str]:
    return [value for value in (values or default) if value]


def load_records(
    *,
    database: Any,
    table: str,
    columns: list[str],
    rows: Iterator[list[Any]],
    batch_size: int,
    limit: int | None,
    progress_label: str,
    progress_every: int,
) -> int:
    total = 0
    batch: list[list[Any]] = []
    for row in rows:
        if limit is not None and total + len(batch) >= limit:
            break
        batch.append(row)
        if len(batch) >= batch_size:
            commit_batch(database, table, columns, batch)
            total += len(batch)
            batch.clear()
            report_progress(progress_label, total, progress_every)
    if batch and (limit is None or total < limit):
        if limit is not None:
            batch = batch[: max(0, limit - total)]
        commit_batch(database, table, columns, batch)
        total += len(batch)
        report_progress(progress_label, total, progress_every, force=True)
    return total


def commit_batch(
    database: Any,
    table: str,
    columns: list[str],
    values: list[list[Any]],
) -> None:
    with database.batch() as batch:
        batch.insert_or_update(table=table, columns=columns, values=values)


def report_progress(
    label: str,
    total: int,
    progress_every: int,
    *,
    force: bool = False,
) -> None:
    if force or (progress_every and total % progress_every == 0):
        print(f"Loaded {label}: {total}", flush=True)


def iter_document_rows(paths_: list[str]) -> Iterator[list[Any]]:
    for record in iter_jsonl(paths_):
        yield [
            record["doc_id"],
            record["source_dataset"],
            record.get("source_file"),
            as_int(record.get("source_row")),
            record.get("external_id"),
            record.get("title"),
            record.get("body"),
            record.get("text_for_embedding"),
            parse_timestamp(record.get("published_at")),
            record.get("source"),
            as_string_array(record.get("commodities")),
            as_string_array(record.get("tags")),
            json_string(record.get("sentiment")),
            json_string(record.get("metadata")),
            spanner.COMMIT_TIMESTAMP,
        ]


def iter_chunk_rows(paths_: list[str]) -> Iterator[list[Any]]:
    for record in iter_jsonl(paths_):
        yield [
            record["doc_id"],
            record["chunk_id"],
            record["source_dataset"],
            as_int(record.get("chunk_index")),
            as_int(record.get("char_start")),
            record.get("chunk_text"),
            record.get("text_for_embedding"),
            as_int(record.get("token_estimate")),
            parse_timestamp(record.get("published_at")),
            record.get("source"),
            as_string_array(record.get("commodities")),
            as_string_array(record.get("tags")),
            json_string(record.get("metadata")),
            None,
            spanner.COMMIT_TIMESTAMP,
        ]


def iter_jsonl(paths_: list[str]) -> Iterator[dict[str, Any]]:
    for path in paths_:
        with open_text(path) as file:
            for line in file:
                if line.strip():
                    yield json.loads(line)


def open_text(path: str) -> Any:
    if path.startswith("gs://"):
        bucket_name, object_name = parse_gs_uri(path)
        blob = storage.Client().bucket(bucket_name).blob(object_name)
        return blob.open("r", encoding="utf-8")
    return Path(path).open(encoding="utf-8", errors="replace")


def parse_gs_uri(uri: str) -> tuple[str, str]:
    stripped = uri.removeprefix("gs://")
    bucket, _, object_name = stripped.partition("/")
    if not bucket or not object_name:
        raise ValueError(f"Invalid GCS URI: {uri}")
    return bucket, object_name


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def json_string(value: Any) -> str | None:
    if value in (None, "", []):
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def as_string_array(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
