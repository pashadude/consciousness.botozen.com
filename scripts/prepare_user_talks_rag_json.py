"""Convert Mutual Specification user-talk markdown into Spanner RAG JSONL."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from scripts.prepare_spanner_rag_json import chunk_document, stable_id


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        action="append",
        default=["docs/design/mutual_specification_user_talk_seed.md"],
    )
    parser.add_argument("--out-dir", default="data/spanner_rag_ingest")
    parser.add_argument("--chunk-chars", type=int, default=3200)
    parser.add_argument("--chunk-overlap", type=int, default=300)
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    docs_path = out_dir / "user_talks_documents.jsonl"
    chunks_path = out_dir / "user_talks_chunks.jsonl"
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    doc_count = chunk_count = 0
    with docs_path.open("w", encoding="utf-8") as docs_file, chunks_path.open(
        "w",
        encoding="utf-8",
    ) as chunks_file:
        for source_path in [Path(item) for item in args.source]:
            body = source_path.read_text(encoding="utf-8")
            title = title_from_markdown(body) or source_path.stem.replace("_", " ")
            document = {
                "doc_id": stable_id(["mutual_spec_user_talks", source_path.as_posix()]),
                "source_dataset": "mutual_spec_user_talks",
                "source_file": source_path.as_posix(),
                "source_row": 1,
                "external_id": source_path.stem,
                "title": title,
                "body": body,
                "text_for_embedding": "\n".join(
                    [
                        f"Title: {title}",
                        "Commodities: trader_workflow, human_ai_coordination",
                        "Tags: mutual_specification_game, user_talk, trader_agent",
                        body,
                    ]
                ),
                "published_at": created_at,
                "source": "user_talk_seed",
                "commodities": ["trader_workflow", "human_ai_coordination"],
                "tags": [
                    "mutual_specification_game",
                    "user_talk",
                    "trader_agent",
                    "specification_game",
                ],
                "sentiment": None,
                "metadata": {
                    "kind": "mutual_spec_user_talk_seed",
                    "source_path": source_path.as_posix(),
                },
            }
            docs_file.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
            docs_file.write("\n")
            doc_count += 1
            for chunk in chunk_document(
                document,
                chunk_chars=args.chunk_chars,
                chunk_overlap=args.chunk_overlap,
            ):
                chunks_file.write(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")))
                chunks_file.write("\n")
                chunk_count += 1
    print(json.dumps({"documents": doc_count, "chunks": chunk_count}, indent=2))
    print(f"Wrote {docs_path}")
    print(f"Wrote {chunks_path}")
    return 0


def title_from_markdown(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
